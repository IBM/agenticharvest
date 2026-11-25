#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Base intermediate-representation abstractions.

Generic building blocks shared by every domain's IR — not the Excel workbook IR
itself (that lives in ``agenticharvest.domains.excel.ir``). Modeling the IR as a graph
with typed data-flow edges is what makes ``get_dependents``-style tools portable
across domains.

Contents:
    base        the graph: Node, Edge, Artifact, IRGraph + traversal
    source      accepting a source in various forms: ResolvedSource, resolvers
"""

from agenticharvest.core.ir.base import (
    EDGE_CONTAINS,
    EDGE_DATA_FLOW,
    EDGE_DEPENDS_ON,
    NODE_ARTIFACT,
    NODE_PROCEDURE,
    NODE_REGION,
    Artifact,
    Edge,
    IRGraph,
    Node,
)
from agenticharvest.core.ir.source import (
    RawSource,
    ResolvedSource,
    SourceMetadata,
    SourceResolver,
    read_bytes,
    sha256_bytes,
)

__all__ = [
    "EDGE_CONTAINS",
    "EDGE_DATA_FLOW",
    "EDGE_DEPENDS_ON",
    "NODE_ARTIFACT",
    "NODE_PROCEDURE",
    "NODE_REGION",
    "Artifact",
    "Edge",
    "IRGraph",
    "Node",
    "RawSource",
    "ResolvedSource",
    "SourceMetadata",
    "SourceResolver",
    "read_bytes",
    "sha256_bytes",
]
