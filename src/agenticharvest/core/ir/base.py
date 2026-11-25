#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Generic intermediate-representation graph.

Every domain (Excel today, PDFs / databases / codebases tomorrow) reduces its
source artifact to the same shape: a set of typed **nodes** joined by typed
**edges**. Modeling the IR as one generic graph is what lets tools like
"what depends on X?" or "trace the data flow into Y" stay portable — the tool
walks edges by kind and never needs to know it is looking at a spreadsheet.

Node and edge *kinds* are plain strings, not a closed enum: the core defines a
handful of universal kinds here and each domain contributes its own (see
``agenticharvest.domains.excel.ir.models``). This keeps the graph open for extension
without touching core.

The models are Pydantic v2 so an IR round-trips to JSON losslessly — the same
graph can be built in-process, cached to disk, or shipped over a channel.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from pydantic import BaseModel, Field

# --- Universal kinds -------------------------------------------------------
# Domains extend these with their own string constants. They are deliberately
# generic: an "artifact" node is any structured document; a "region" is a
# navigable part of it; a "procedure" is executable logic attached to it.
NODE_ARTIFACT = "artifact"
NODE_REGION = "region"
NODE_PROCEDURE = "procedure"

# "depends_on" and "data_flow" are the two edges nearly every domain needs:
# a structural dependency, and a directional read/write of data.
EDGE_DEPENDS_ON = "depends_on"
EDGE_DATA_FLOW = "data_flow"
EDGE_CONTAINS = "contains"


class Node(BaseModel):
    """A single element of the IR graph.

    Attributes:
        id: Stable, unique identifier within one graph. Tools and edges refer
            to nodes by this id, so it must be deterministic across rebuilds of
            the same source (e.g. derive it from the element's natural key).
        kind: The node's type as a string constant (see the ``NODE_*`` module
            constants and each domain's own kinds). Kept open so new domains add
            kinds without a core change.
        label: Human-readable name for display; not required to be unique.
        attrs: Domain-specific fields that do not warrant a typed model. Typed
            domain models (e.g. ``Sheet``) are the preferred home for structured
            data; ``attrs`` is the escape hatch for the long tail.
    """

    id: str
    kind: str
    label: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    """A directed, typed relationship between two nodes.

    Attributes:
        source: ``Node.id`` the edge originates from.
        target: ``Node.id`` the edge points to.
        kind: The relationship type (see ``EDGE_*`` constants and domain
            kinds). Direction is meaningful: for ``EDGE_DATA_FLOW`` the
            convention is source produces / writes and target consumes / reads,
            but each edge kind documents its own semantics.
        attrs: Domain-specific edge metadata (e.g. the cell range a data-flow
            edge covers).
    """

    source: str
    target: str
    kind: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    """Provenance for the source an IR graph was built from.

    Kept separate from the graph nodes so the same descriptor can front any
    domain. The content hash makes IR builds cacheable and lets the event log
    tie every downstream tool call back to an exact input.

    Attributes:
        uri: Where the source came from (a ``file://`` path, an upload id, a
            URL). Opaque to core.
        kind: The source's type discriminator (e.g. ``"excel"``). Domains match
            on this to claim an artifact.
        content_hash: Hex digest of the raw bytes, or ``None`` if not computed.
        size_bytes: Raw size, when known.
        metadata: Free-form provenance (original filename, mime type, …).
    """

    uri: str
    kind: str
    content_hash: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IRGraph(BaseModel):
    """A typed graph over one source artifact.

    Stored as flat node and edge lists so the model serializes cleanly to JSON.
    Adjacency lookups are built on demand and cached, so repeated traversal is
    cheap without bloating the serialized form. The cache is invalidated
    whenever nodes or edges are mutated through the provided methods; mutate the
    lists directly only before any traversal.

    Attributes:
        artifact: Provenance of the source this graph represents.
        nodes: All nodes, in insertion order.
        edges: All edges, in insertion order.
    """

    artifact: Artifact | None = None
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    # Adjacency indexes are derived state, never serialized. ``exclude`` keeps
    # them out of ``model_dump``; a leading underscore keeps them private.
    _node_index: dict[str, Node] | None = None
    _out_edges: dict[str, list[Edge]] | None = None
    _in_edges: dict[str, list[Edge]] | None = None

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node: Node) -> Node:
        """Append a node and invalidate the adjacency cache.

        Args:
            node: The node to add. Its ``id`` should be unique within the graph;
                a duplicate id shadows the earlier node in ``get_node``.

        Returns:
            The same node, for call chaining.
        """
        self.nodes.append(node)
        self._invalidate()
        return node

    def add_edge(self, edge: Edge) -> Edge:
        """Append an edge and invalidate the adjacency cache.

        The edge's ``source`` and ``target`` are not required to exist yet, so a
        graph can be assembled edges-first; call ``dangling_edges`` afterward to
        validate referential integrity.

        Args:
            edge: The edge to add.

        Returns:
            The same edge, for call chaining.
        """
        self.edges.append(edge)
        self._invalidate()
        return edge

    def _invalidate(self) -> None:
        """Drop cached adjacency indexes so they rebuild on next access."""
        self._node_index = None
        self._out_edges = None
        self._in_edges = None

    # ------------------------------------------------------------------
    # Indexing (built lazily, cached until mutation)
    # ------------------------------------------------------------------

    def _ensure_index(self) -> None:
        """Build the node and adjacency indexes if not already cached."""
        if self._node_index is not None:
            return
        node_index: dict[str, Node] = {}
        out_edges: dict[str, list[Edge]] = {}
        in_edges: dict[str, list[Edge]] = {}
        for node in self.nodes:
            node_index[node.id] = node
        for edge in self.edges:
            out_edges.setdefault(edge.source, []).append(edge)
            in_edges.setdefault(edge.target, []).append(edge)
        self._node_index = node_index
        self._out_edges = out_edges
        self._in_edges = in_edges

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> Node | None:
        """Return the node with ``node_id``, or ``None`` if absent."""
        self._ensure_index()
        assert self._node_index is not None
        return self._node_index.get(node_id)

    def nodes_of_kind(self, kind: str) -> list[Node]:
        """Return every node whose ``kind`` equals ``kind``, in graph order."""
        return [n for n in self.nodes if n.kind == kind]

    def edges_of_kind(self, kind: str) -> list[Edge]:
        """Return every edge whose ``kind`` equals ``kind``, in graph order."""
        return [e for e in self.edges if e.kind == kind]

    def out_edges(self, node_id: str, kind: str | None = None) -> list[Edge]:
        """Edges leaving ``node_id``, optionally filtered to one ``kind``.

        Args:
            node_id: The source node.
            kind: If given, only edges of this kind are returned.

        Returns:
            Matching edges in graph order (empty if the node has none).
        """
        self._ensure_index()
        assert self._out_edges is not None
        edges = self._out_edges.get(node_id, [])
        return [e for e in edges if kind is None or e.kind == kind]

    def in_edges(self, node_id: str, kind: str | None = None) -> list[Edge]:
        """Edges entering ``node_id``, optionally filtered to one ``kind``.

        Args:
            node_id: The target node.
            kind: If given, only edges of this kind are returned.

        Returns:
            Matching edges in graph order (empty if the node has none).
        """
        self._ensure_index()
        assert self._in_edges is not None
        edges = self._in_edges.get(node_id, [])
        return [e for e in edges if kind is None or e.kind == kind]

    def neighbors(
        self,
        node_id: str,
        kind: str | None = None,
        direction: str = "out",
    ) -> list[Node]:
        """Return nodes adjacent to ``node_id`` along matching edges.

        Args:
            node_id: The node to start from.
            kind: If given, only traverse edges of this kind.
            direction: ``"out"`` follows outgoing edges to their targets,
                ``"in"`` follows incoming edges to their sources.

        Returns:
            The adjacent nodes that exist in the graph, in edge order.
            Dangling references are skipped.
        """
        if direction == "out":
            ids = [e.target for e in self.out_edges(node_id, kind)]
        elif direction == "in":
            ids = [e.source for e in self.in_edges(node_id, kind)]
        else:  # pragma: no cover - defensive
            raise ValueError(f"direction must be 'in' or 'out', got {direction!r}")
        result = []
        for nid in ids:
            node = self.get_node(nid)
            if node is not None:
                result.append(node)
        return result

    def reachable(
        self,
        start: str,
        kind: str | None = None,
        direction: str = "out",
    ) -> list[str]:
        """Transitively-reachable node ids from ``start`` via a breadth-first walk.

        Powers "call chain" / "everything downstream of X" style tools. The
        start node itself is not included in the result.

        Args:
            start: Node id to begin the traversal from.
            kind: If given, only edges of this kind are followed.
            direction: ``"out"`` (successors) or ``"in"`` (predecessors).

        Returns:
            Reachable node ids in breadth-first discovery order, each once.
        """
        seen: set[str] = {start}
        order: list[str] = []
        frontier: list[str] = [start]
        while frontier:
            current = frontier.pop(0)
            for neighbor in self.neighbors(current, kind, direction):
                if neighbor.id not in seen:
                    seen.add(neighbor.id)
                    order.append(neighbor.id)
                    frontier.append(neighbor.id)
        return order

    def dangling_edges(self) -> list[Edge]:
        """Return edges whose source or target node is missing.

        Useful as a post-build integrity check when a graph is assembled
        edges-first.
        """
        self._ensure_index()
        assert self._node_index is not None
        index = self._node_index
        return [
            e for e in self.edges if e.source not in index or e.target not in index
        ]

    def extend(self, nodes: Iterable[Node] = (), edges: Iterable[Edge] = ()) -> None:
        """Bulk-add nodes and edges, invalidating the cache once.

        Args:
            nodes: Nodes to append.
            edges: Edges to append.
        """
        self.nodes.extend(nodes)
        self.edges.extend(edges)
        self._invalidate()

    def __iter__(self) -> Iterator[Node]:  # type: ignore[override]
        """Iterate over the graph's nodes in insertion order."""
        return iter(self.nodes)
