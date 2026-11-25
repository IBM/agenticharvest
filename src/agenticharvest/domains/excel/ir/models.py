#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The workbook IR: Excel's concrete node, edge, and view types.

A workbook reduces to the generic ``IRGraph`` from ``agenticharvest.core.ir``:

    nodes   one per sheet, one per macro (a "procedure")
    edges   CALLS      macro -> macro          (the call graph)
            READS      macro -> sheet          (data flow in)
            WRITES     macro -> sheet          (data flow out)
            TRIGGERS   button/control -> macro (what a click runs)

The typed models below (``Sheet``, ``Macro``, ``NamedRange``, ``Button``) are
what callers actually work with; ``WorkbookIR`` is a thin, read-only view that
projects the underlying graph into those types and answers the questions the
Excel tools need ("which macros write this sheet?", "what does this macro
call?"). The graph stays the single source of truth, so the IR still serializes
and mines like any other domain's.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from agenticharvest.core.ir.base import (
    NODE_PROCEDURE,
    NODE_REGION,
    Edge,
    IRGraph,
    Node,
)

# --- Excel-specific kinds --------------------------------------------------
# Sheets are "regions" of the workbook artifact; macros are "procedures". Using
# the generic base kinds keeps cross-domain tooling working, while the string
# constants below name the Excel edge semantics.
NODE_SHEET = NODE_REGION
NODE_MACRO = NODE_PROCEDURE

EDGE_CALLS = "calls"       # macro invokes another macro
EDGE_READS = "reads"       # macro reads values from a sheet
EDGE_WRITES = "writes"     # macro writes values to a sheet
EDGE_TRIGGERS = "triggers"  # a control (button/hyperlink) runs a macro


class SheetRole(str, Enum):
    """How a sheet participates in the workbook's data flow.

    Derived from the data-flow edges: a sheet only ever read is an input the
    user supplies or a reference table; a sheet written by some macro is a
    computed output. The role guides agents and tools toward the sheets a task
    should touch.
    """

    INPUT = "input"          # read by macros, never written — user-supplied
    OUTPUT = "output"        # written by at least one macro — computed result
    REFERENCE = "reference"  # read-only lookup data, not user-edited
    UNKNOWN = "unknown"      # role could not be determined


class Sheet(BaseModel):
    """A worksheet in the workbook.

    Attributes:
        name: The sheet's display name (its natural key and node id).
        codename: The VBA code name (e.g. ``Sheet1``), when known. Macros often
            reference sheets by code name rather than display name.
        headers: The header row values, left to right, trailing blanks trimmed.
        role: The sheet's data-flow role (see ``SheetRole``).
        index: Zero-based position in the workbook, when known.
        description: Optional human/agent-authored summary of the sheet.
    """

    name: str
    codename: str | None = None
    headers: list[str] = Field(default_factory=list)
    role: SheetRole = SheetRole.UNKNOWN
    index: int | None = None
    description: str | None = None


class Macro(BaseModel):
    """A VBA procedure (Sub or Function) defined in the workbook.

    Attributes:
        name: The procedure name (its node id). Event handlers that recur across
            modules may be disambiguated by the extractor.
        module: The VBA module the procedure is defined in, when known.
        source: The procedure's VBA source text, when extracted.
        is_event_handler: ``True`` for ``Worksheet_*`` / ``Workbook_*`` handlers.
        description: Optional human/agent-authored summary of what it does.
    """

    name: str
    module: str | None = None
    source: str | None = None
    is_event_handler: bool = False
    description: str | None = None


class NamedRange(BaseModel):
    """A user-defined named range or name.

    Attributes:
        name: The defined name (its key).
        sheet: The sheet the name resolves to, or ``None`` for workbook-scope
            names that resolve to a constant or formula.
        ref: The cell/range reference or raw value the name points at.
        scope: ``"workbook"`` or ``"sheet"``.
    """

    name: str
    sheet: str | None = None
    ref: str | None = None
    scope: str = "workbook"


class Button(BaseModel):
    """A clickable control that triggers a macro.

    Covers form/ActiveX buttons and the self-referencing-hyperlink trick some
    workbooks use as clickable cells.

    Attributes:
        label: The visible text on the control.
        macro: The macro the control runs, or ``None`` for a navigation-only
            hyperlink.
        sheet: The sheet the control lives on.
        cell: The anchor cell, when the control is a hyperlink.
        kind: ``"button"`` or ``"hyperlink"``.
    """

    label: str
    macro: str | None = None
    sheet: str | None = None
    cell: str | None = None
    kind: str = "button"


class WorkbookIR(BaseModel):
    """A typed, read-only view over a workbook's ``IRGraph``.

    Construct one with :meth:`from_graph` after the extractor has populated the
    graph. All queries read through to the graph, so the graph remains the
    single serializable source of truth; this view just spares callers from
    walking raw nodes and edges.

    Attributes:
        graph: The underlying generic IR graph.
        named_ranges: Defined names, keyed by name. Stored on the view rather
            than as nodes because they are lookups, not data-flow participants.
        buttons: Controls that trigger macros.
    """

    graph: IRGraph
    named_ranges: dict[str, NamedRange] = Field(default_factory=dict)
    buttons: list[Button] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def sheet_node(sheet: Sheet) -> Node:
        """Build the IR node for a sheet (id = display name)."""
        return Node(
            id=sheet.name,
            kind=NODE_SHEET,
            label=sheet.name,
            attrs=sheet.model_dump(exclude={"name"}),
        )

    @staticmethod
    def macro_node(macro: Macro) -> Node:
        """Build the IR node for a macro (id = macro name)."""
        return Node(
            id=macro.name,
            kind=NODE_MACRO,
            label=macro.name,
            attrs=macro.model_dump(exclude={"name"}),
        )

    # ------------------------------------------------------------------
    # Typed accessors
    # ------------------------------------------------------------------

    def sheets(self) -> list[Sheet]:
        """Return every sheet in workbook order."""
        return [self._to_sheet(n) for n in self.graph.nodes_of_kind(NODE_SHEET)]

    def macros(self) -> list[Macro]:
        """Return every macro in graph order."""
        return [self._to_macro(n) for n in self.graph.nodes_of_kind(NODE_MACRO)]

    def sheet(self, name: str) -> Sheet | None:
        """Return the named sheet, or ``None`` if it is not in the workbook."""
        node = self.graph.get_node(name)
        if node is None or node.kind != NODE_SHEET:
            return None
        return self._to_sheet(node)

    def macro(self, name: str) -> Macro | None:
        """Return the named macro, or ``None`` if absent."""
        node = self.graph.get_node(name)
        if node is None or node.kind != NODE_MACRO:
            return None
        return self._to_macro(node)

    # ------------------------------------------------------------------
    # Relationship queries (used directly by the Excel tools)
    # ------------------------------------------------------------------

    def readers_of(self, sheet: str) -> list[str]:
        """Names of macros that read ``sheet`` (incoming READS edges)."""
        return [e.source for e in self.graph.in_edges(sheet, EDGE_READS)]

    def writers_of(self, sheet: str) -> list[str]:
        """Names of macros that write ``sheet`` (incoming WRITES edges)."""
        return [e.source for e in self.graph.in_edges(sheet, EDGE_WRITES)]

    def reads(self, macro: str) -> list[str]:
        """Sheets ``macro`` reads (outgoing READS edges)."""
        return [e.target for e in self.graph.out_edges(macro, EDGE_READS)]

    def writes(self, macro: str) -> list[str]:
        """Sheets ``macro`` writes (outgoing WRITES edges)."""
        return [e.target for e in self.graph.out_edges(macro, EDGE_WRITES)]

    def call_chain(self, macro: str, direction: str = "callees") -> list[str]:
        """Transitively-reachable macros over the CALLS graph.

        Args:
            macro: The macro to start from.
            direction: ``"callees"`` walks what ``macro`` calls (downstream);
                ``"callers"`` walks what calls ``macro`` (upstream).

        Returns:
            Reachable macro names in breadth-first order, excluding ``macro``.
        """
        graph_direction = "out" if direction == "callees" else "in"
        return self.graph.reachable(macro, EDGE_CALLS, graph_direction)

    def triggers_for(self, macro: str) -> list[Button]:
        """Return the controls that trigger ``macro``."""
        return [b for b in self.buttons if b.macro == macro]

    # ------------------------------------------------------------------
    # Internal conversions
    # ------------------------------------------------------------------

    @staticmethod
    def _to_sheet(node: Node) -> Sheet:
        """Rebuild a ``Sheet`` from its IR node."""
        return Sheet(name=node.id, **node.attrs)

    @staticmethod
    def _to_macro(node: Node) -> Macro:
        """Rebuild a ``Macro`` from its IR node."""
        return Macro(name=node.id, **node.attrs)


def calls_edge(caller: str, callee: str) -> Edge:
    """Build a CALLS edge from ``caller`` to ``callee``."""
    return Edge(source=caller, target=callee, kind=EDGE_CALLS)


def reads_edge(macro: str, sheet: str) -> Edge:
    """Build a READS edge from ``macro`` to ``sheet``."""
    return Edge(source=macro, target=sheet, kind=EDGE_READS)


def writes_edge(macro: str, sheet: str) -> Edge:
    """Build a WRITES edge from ``macro`` to ``sheet``."""
    return Edge(source=macro, target=sheet, kind=EDGE_WRITES)


def triggers_edge(control_id: str, macro: str) -> Edge:
    """Build a TRIGGERS edge from a control node to ``macro``."""
    return Edge(source=control_id, target=macro, kind=EDGE_TRIGGERS)
