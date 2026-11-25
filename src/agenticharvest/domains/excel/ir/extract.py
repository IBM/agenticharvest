#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Build a ``WorkbookIR`` from a workbook.

This is the deterministic pass. It asks a backend for the workbook's structure
and VBA, runs the pure-text analyzer to derive the call graph and data flow,
then assembles the generic IR graph — sheet and macro nodes, CALLS edges from
the call graph, READS/WRITES edges from the data flow, and TRIGGERS edges from
controls to the macros they run. An agentic pass may later enrich the same graph
with descriptions and refined roles, but nothing here needs an LLM.

The extractor knows nothing about openpyxl or any other engine: every read goes
through :class:`ExcelBackend`, so the same assembly works over a file backend, a
live-Excel backend, or a future .xlsb reader.
"""

from __future__ import annotations

from agenticharvest.core.ir.base import IRGraph, Node
from agenticharvest.core.ir.source import RawSource, ResolvedSource
from agenticharvest.domains.excel.backends.base import ExcelBackend
from agenticharvest.domains.excel.ir import vba
from agenticharvest.domains.excel.ir.models import (
    Button,
    Macro,
    Sheet,
    SheetRole,
    WorkbookIR,
    calls_edge,
    reads_edge,
    triggers_edge,
    writes_edge,
)

# Controls are UI, not data-flow, so they are not first-class IR nodes; a
# lightweight "control" node owns each TRIGGERS edge to a macro.
_CONTROL_PREFIX = "control:"


def build_ir(
    source: RawSource | ResolvedSource,
    *,
    backend: ExcelBackend | None = None,
    max_header_cols: int = 64,
) -> WorkbookIR:
    """Build the workbook IR from a source.

    Args:
        source: A resolved source, or any raw form accepted by the Excel source
            resolver (path / bytes / file-like).
        backend: The backend to read structure and VBA through. Defaults to the
            read-only openpyxl backend bound to the resolved source path.
        max_header_cols: How many leading columns of the first row to read as a
            sheet's header.

    Returns:
        A populated ``WorkbookIR`` whose graph carries sheet and macro nodes and
        the CALLS / READS / WRITES / TRIGGERS edges between them.
    """
    resolved = _ensure_resolved(source)
    backend = backend or _default_backend(resolved.path)

    try:
        sheets, codename_map = backend.read_sheets(max_header_cols)
        named_ranges = backend.read_named_ranges()
        buttons = backend.read_controls()
        modules = backend.read_vba_modules()
    finally:
        backend.close()

    macros = vba.parse_macros(modules)
    call_edges = vba.build_call_edges(macros)
    flows = vba.infer_data_flow(macros, codename_map, [s.name for s in sheets])

    _assign_sheet_roles(sheets, flows)

    graph = IRGraph(artifact=resolved.artifact)
    _populate_graph(graph, sheets, macros, call_edges, flows, buttons)

    return WorkbookIR(
        graph=graph,
        named_ranges={nr.name: nr for nr in named_ranges},
        buttons=buttons,
    )


def _ensure_resolved(source: RawSource | ResolvedSource) -> ResolvedSource:
    """Return a ``ResolvedSource``, resolving raw input via the Excel resolver."""
    if isinstance(source, ResolvedSource):
        return source
    from agenticharvest.domains.excel.source import ExcelSource

    return ExcelSource().resolve(source)


def _default_backend(path: str) -> ExcelBackend:
    """Construct the default read-only backend for ``path``."""
    from agenticharvest.domains.excel.backends.openpyxl.backend import OpenpyxlBackend

    return OpenpyxlBackend(path)


# ----------------------------------------------------------------------
# Roles and graph assembly
# ----------------------------------------------------------------------


def _assign_sheet_roles(
    sheets: list[Sheet], flows: dict[str, dict[str, list[str]]]
) -> None:
    """Set each sheet's data-flow role from the inferred read/write sets.

    A sheet written by any macro is an OUTPUT; one only ever read is an INPUT;
    one neither read nor written stays UNKNOWN. Telling a pure reference table
    apart from a user-supplied input is left to the agentic pass, which has the
    context to make that call.

    Args:
        sheets: The workbook's sheets (mutated in place).
        flows: Per-macro read/write sets from the data-flow inference.
    """
    written: set[str] = set()
    read: set[str] = set()
    for flow in flows.values():
        written.update(flow["writes"])
        read.update(flow["reads"])
    for sheet in sheets:
        if sheet.name in written:
            sheet.role = SheetRole.OUTPUT
        elif sheet.name in read:
            sheet.role = SheetRole.INPUT
        else:
            sheet.role = SheetRole.UNKNOWN


def _populate_graph(
    graph: IRGraph,
    sheets: list[Sheet],
    macros: list[Macro],
    call_edges: list[tuple[str, str]],
    flows: dict[str, dict[str, list[str]]],
    buttons: list[Button],
) -> None:
    """Add all nodes and edges to the IR graph.

    Args:
        graph: The graph to populate (mutated in place).
        sheets: Sheet models to add as region nodes.
        macros: Macro models to add as procedure nodes.
        call_edges: ``(caller, callee)`` pairs for CALLS edges.
        flows: Per-macro read/write sets for READS / WRITES edges.
        buttons: Controls for TRIGGERS edges.
    """
    for sheet in sheets:
        graph.add_node(WorkbookIR.sheet_node(sheet))
    for macro in macros:
        graph.add_node(WorkbookIR.macro_node(macro))

    for caller, callee in call_edges:
        graph.add_edge(calls_edge(caller, callee))

    for macro_name, flow in flows.items():
        for sheet_name in flow["reads"]:
            graph.add_edge(reads_edge(macro_name, sheet_name))
        for sheet_name in flow["writes"]:
            graph.add_edge(writes_edge(macro_name, sheet_name))

    _add_trigger_edges(graph, buttons)


def _add_trigger_edges(graph: IRGraph, buttons: list[Button]) -> None:
    """Add a control node and TRIGGERS edge for each macro-bound control.

    Args:
        graph: The graph to add to (mutated in place).
        buttons: Controls; only those bound to a macro produce an edge.
    """
    for i, button in enumerate(buttons):
        if not button.macro:
            continue
        control_id = f"{_CONTROL_PREFIX}{button.sheet}:{button.cell or i}"
        graph.add_node(
            Node(
                id=control_id,
                kind="control",
                label=button.label,
                attrs={
                    "sheet": button.sheet,
                    "cell": button.cell,
                    "kind": button.kind,
                },
            )
        )
        graph.add_edge(triggers_edge(control_id, button.macro))
