#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The Excel tool specs and their handlers.

Five read-only tools project the workbook IR (``list_sheets``, ``describe_sheet``,
``list_macros``, ``get_call_graph``, ``get_data_flow``); each is a thin wrapper
that resolves a path, builds (or reuses) its IR, and returns the answer the tool
promises. All the real read work lives in ``build_ir`` and the ``WorkbookIR``
view — these handlers add no analysis of their own. A sixth read tool,
``read_range``, returns cell *values* — the data the structure describes — through
the file-level backend.

Two write tools change the artifact. ``write_cell`` sets a cell value through the
file-level openpyxl backend (available everywhere). ``run_macro`` runs a VBA
macro, which needs a live execution engine (xlwings + a real Excel), so it is
routed through :func:`~agenticharvest.domains.excel.backends.registry.select_backend`
and reports itself unavailable when no such backend is installed — rather than
pretending. Both carry ``Effect.WRITE``, so the gateway's dry-run gate forces a
``confirm=true`` before either touches the workbook.

Descriptions are one plain sentence each: a capability statement a model can act
on, with no implementation detail.
"""

from __future__ import annotations

from typing import Any

from agenticharvest.core.tools.spec import Effect, ToolSpec
from agenticharvest.domains.excel.backends.base import Capability
from agenticharvest.domains.excel.backends.registry import select_backend
from agenticharvest.domains.excel.ir import WorkbookIR, build_ir
from agenticharvest.domains.excel.source import ExcelSource

# A tool-using model will call several tools against the same workbook in one
# turn; parsing it once per call would be wasteful. Cache the built IR by the
# source's content hash, so identical bytes (even via different paths) share one
# parse and any changed file re-parses.
_IR_CACHE: dict[str, WorkbookIR] = {}


def _load_ir(path: str) -> WorkbookIR:
    """Resolve ``path`` to a workbook IR, reusing a cached build by content hash."""
    resolved = ExcelSource().resolve(path)
    key = resolved.artifact.content_hash or resolved.path
    cached = _IR_CACHE.get(key)
    if cached is None:
        cached = build_ir(resolved)
        _IR_CACHE[key] = cached
    return cached


def _require(params: dict, name: str) -> str:
    """Return a required string parameter, raising a clear error if it is absent."""
    value = params.get(name)
    if not value:
        raise ValueError(f"Missing required parameter: '{name}'.")
    return str(value)


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------


def list_sheets(params: dict) -> list[dict[str, Any]]:
    """List every sheet with its code name, data-flow role, and headers."""
    ir = _load_ir(_require(params, "path"))
    return [
        {
            "name": s.name,
            "codename": s.codename,
            "role": s.role.value,
            "headers": s.headers,
        }
        for s in ir.sheets()
    ]


def describe_sheet(params: dict) -> dict[str, Any]:
    """Return one sheet's detail, plus the macros that read and write it."""
    ir = _load_ir(_require(params, "path"))
    name = _require(params, "sheet")
    sheet = ir.sheet(name)
    if sheet is None:
        known = ", ".join(s.name for s in ir.sheets())
        raise ValueError(f"No sheet named '{name}'. Sheets: {known}.")
    return {
        "name": sheet.name,
        "codename": sheet.codename,
        "role": sheet.role.value,
        "headers": sheet.headers,
        "read_by": ir.readers_of(sheet.name),
        "written_by": ir.writers_of(sheet.name),
    }


def list_macros(params: dict) -> list[dict[str, Any]]:
    """List every macro with its module and whether it is an event handler."""
    ir = _load_ir(_require(params, "path"))
    return [
        {
            "name": m.name,
            "module": m.module,
            "is_event_handler": m.is_event_handler,
        }
        for m in ir.macros()
    ]


def get_call_graph(params: dict) -> dict[str, Any]:
    """Return the macros a macro calls and the macros that call it."""
    ir = _load_ir(_require(params, "path"))
    name = _require(params, "macro")
    if ir.macro(name) is None:
        known = ", ".join(m.name for m in ir.macros())
        raise ValueError(f"No macro named '{name}'. Macros: {known}.")
    return {
        "macro": name,
        "calls": ir.call_chain(name, direction="callees"),
        "called_by": ir.call_chain(name, direction="callers"),
    }


def get_data_flow(params: dict) -> dict[str, Any]:
    """Return the sheets a macro reads from and writes to."""
    ir = _load_ir(_require(params, "path"))
    name = _require(params, "macro")
    if ir.macro(name) is None:
        known = ", ".join(m.name for m in ir.macros())
        raise ValueError(f"No macro named '{name}'. Macros: {known}.")
    return {
        "macro": name,
        "reads": ir.reads(name),
        "writes": ir.writes(name),
    }


#: The most rows ``read_range`` returns in one call, whatever the caller asks.
MAX_READ_ROWS = 500


def read_range(params: dict) -> dict[str, Any]:
    """Return the cell values in a range of a sheet, row by row."""
    resolved = ExcelSource().resolve(_require(params, "path"))
    sheet = _require(params, "sheet")
    ref = params.get("range") or None
    max_rows = min(int(params.get("max_rows") or 200), MAX_READ_ROWS)

    # Reads go through the file-level backend, exactly as IR extraction does,
    # so a value read never depends on a live engine being installed.
    from agenticharvest.domains.excel.backends.openpyxl.backend import OpenpyxlBackend

    backend = OpenpyxlBackend(resolved.path)
    try:
        rows = backend.read_range(sheet, ref, max_rows=max_rows)
    finally:
        backend.close()
    return {"sheet": sheet, "range": ref, "rows": rows}


# ----------------------------------------------------------------------
# Write handlers
#
# ``confirm`` is consumed by the gateway's dry-run gate before these run, so it
# is ignored here. A path is resolved through ``ExcelSource`` so a write only
# ever lands on a validated workbook file.
# ----------------------------------------------------------------------


def write_cell(params: dict) -> dict[str, Any]:
    """Set one cell's value in the workbook, returning its before/after."""
    resolved = ExcelSource().resolve(_require(params, "path"))
    sheet = _require(params, "sheet")
    cell = _require(params, "cell")
    if "value" not in params:
        raise ValueError("Missing required parameter: 'value'.")

    backend = select_backend(resolved.path, Capability.WRITE)
    if backend is None:
        raise RuntimeError(
            "No backend available to write cells. Install the 'excel' extra."
        )
    edit = backend.write_cell(sheet, cell, params["value"])
    # The bytes changed, so any IR cached under the old content hash is stale;
    # the next read re-parses under the new hash regardless, but drop the stale
    # entry so it does not linger.
    _IR_CACHE.pop(resolved.artifact.content_hash or resolved.path, None)
    return edit.model_dump()


def run_macro(params: dict) -> dict[str, Any]:
    """Run a macro against a live Excel engine, returning what it produced."""
    resolved = ExcelSource().resolve(_require(params, "path"))
    macro = _require(params, "macro")
    args = params.get("args")
    if args is not None and not isinstance(args, list):
        raise ValueError("Parameter 'args' must be a list when provided.")

    backend = select_backend(resolved.path, Capability.EXECUTE)
    if backend is None:
        raise RuntimeError(
            "No execution backend available to run macros. Running a macro needs "
            "a live Excel engine: install the 'excel-live' extra (xlwings) on a "
            "machine with Microsoft Excel."
        )
    run = backend.run_macro(macro, args)
    _IR_CACHE.pop(resolved.artifact.content_hash or resolved.path, None)
    return run.model_dump()


# ----------------------------------------------------------------------
# Specs, paired with their handlers
# ----------------------------------------------------------------------

_PATH_PROP = {"path": {"type": "string", "description": "Path to the workbook file."}}


def excel_tools() -> list[tuple[ToolSpec, Any]]:
    """Return the Excel domain's ``(spec, handler)`` pairs."""
    return [
        (
            ToolSpec(
                name="list_sheets",
                description="List the sheets in a workbook with their code name, "
                "data-flow role, and header row.",
                input_schema={
                    "type": "object",
                    "properties": dict(_PATH_PROP),
                    "required": ["path"],
                },
            ),
            list_sheets,
        ),
        (
            ToolSpec(
                name="describe_sheet",
                description="Describe one sheet and the macros that read from and "
                "write to it.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "sheet": {"type": "string", "description": "Sheet name."},
                    },
                    "required": ["path", "sheet"],
                },
            ),
            describe_sheet,
        ),
        (
            ToolSpec(
                name="list_macros",
                description="List the macros in a workbook with their module and "
                "whether each is an event handler.",
                input_schema={
                    "type": "object",
                    "properties": dict(_PATH_PROP),
                    "required": ["path"],
                },
            ),
            list_macros,
        ),
        (
            ToolSpec(
                name="get_call_graph",
                description="Get the macros a given macro calls and the macros that "
                "call it.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "macro": {"type": "string", "description": "Macro name."},
                    },
                    "required": ["path", "macro"],
                },
            ),
            get_call_graph,
        ),
        (
            ToolSpec(
                name="get_data_flow",
                description="Get the sheets a given macro reads from and writes to.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "macro": {"type": "string", "description": "Macro name."},
                    },
                    "required": ["path", "macro"],
                },
            ),
            get_data_flow,
        ),
        (
            ToolSpec(
                name="read_range",
                description="Read the cell values in a range of a sheet, row by row.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "sheet": {"type": "string", "description": "Sheet name."},
                        "range": {
                            "type": "string",
                            "description": "Range in A1 notation, e.g. 'A1:D20'. "
                            "Omit to read the whole used range.",
                        },
                        "max_rows": {
                            "type": "integer",
                            "description": f"Most rows to return (default 200, "
                            f"at most {MAX_READ_ROWS}).",
                        },
                    },
                    "required": ["path", "sheet"],
                },
            ),
            read_range,
        ),
        (
            ToolSpec(
                name="write_cell",
                description="Set the value of a single cell on a sheet in the "
                "workbook.",
                effect=Effect.WRITE,
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "sheet": {"type": "string", "description": "Sheet name."},
                        "cell": {
                            "type": "string",
                            "description": "Cell in A1 notation, e.g. 'B1'.",
                        },
                        "value": {"description": "The value to write into the cell."},
                        "confirm": {
                            "type": "boolean",
                            "description": "Must be true to apply this write.",
                        },
                    },
                    "required": ["path", "sheet", "cell", "value"],
                },
            ),
            write_cell,
        ),
        (
            ToolSpec(
                name="run_macro",
                description="Run a macro in the workbook and return its result.",
                effect=Effect.WRITE,
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "macro": {"type": "string", "description": "Macro name."},
                        "args": {
                            "type": "array",
                            "description": "Positional arguments to pass, if any.",
                            "items": {},
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "Must be true to run the macro.",
                        },
                    },
                    "required": ["path", "macro"],
                },
            ),
            run_macro,
        ),
    ]
