#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Excel's OCEL mapping — the domain half of the event log.

``ExcelOCEL`` is the one place that knows an Excel tool call touches a *workbook*
made of *sheets* and *macros*. It extends the generic mapper: for each Excel tool
it emits typed objects (``workbook`` / ``sheet`` / ``macro``) and relates the
event to them with qualifiers a miner can group on — a ``get_data_flow`` event
``reads`` some sheets and ``writes`` others; a ``get_call_graph`` event ``calls``
some macros and is ``called_by`` others; a ``read_range`` event ``reads`` a
sheet's values; a ``write_cell`` event ``writes`` a sheet, and a ``run_macro``
event ``runs`` a macro.

Enriching the two write tools this way is what keeps them on the same
``workbook`` case as the reads: without it they would fall back to the generic
``artifact`` object and vanish from a workbook-keyed process model. So the mined
process shows a read/inspect phase flowing into the mutation — which is the whole
point of observing the write tools.

Everything generic — the id, the timestamp, the ``ok`` / ``error`` attributes,
the fallback for a tool it does not recognize — comes from
``GenericOcelMapper``. Object ids are derived deterministically from the workbook
path and the element name, so the same sheet seen across many calls collapses to
one object in the log.
"""

from __future__ import annotations

from typing import Any

from agenticharvest.core.events.mapping import EventMapping, GenericOcelMapper
from agenticharvest.core.events.model import Object, Relationship
from agenticharvest.core.tools.result import ToolResult

# Object types this domain contributes to the log. Open strings, like the IR's
# node kinds — the core fixes none of them.
OBJ_WORKBOOK = "workbook"
OBJ_SHEET = "sheet"
OBJ_MACRO = "macro"

# The tools ExcelOCEL enriches. Anything else falls back to the generic mapper.
_ENRICHED = frozenset(
    {
        "list_sheets",
        "describe_sheet",
        "list_macros",
        "get_call_graph",
        "get_data_flow",
        "read_range",
        "write_cell",
        "run_macro",
    }
)


#: Tools whose activity can carry the sheet they act on (see ``qualify_sheets``).
_SHEET_SCOPED = frozenset({"describe_sheet", "read_range", "write_cell"})


class ExcelOCEL(GenericOcelMapper):
    """Maps Excel tool calls to workbook/sheet/macro objects and relationships.

    Args:
        qualify_sheets: When ``True``, a tool that acts on one sheet records its
            activity as ``tool:Sheet`` (``read_range:Policy``) instead of the bare
            tool name. A mined process then distinguishes "read the policy" from
            "read the claims" — the difference between a tool log and a business
            process. Off by default, so activities stay the plain tool names.
    """

    def __init__(self, *, qualify_sheets: bool = False) -> None:
        self._qualify_sheets = qualify_sheets

    def map(self, tool: str, params: dict, result: ToolResult) -> EventMapping:
        """Emit typed objects for an Excel tool; defer unknown tools to the base."""
        path = params.get("path")
        if tool not in _ENRICHED or not path:
            return super().map(tool, params, result)

        path = str(path)
        attributes: dict[str, Any] = {"ok": result.ok}
        if result.error:
            attributes["error"] = result.error

        # The workbook is the object every Excel call targets.
        workbook = Object(id=self._workbook_id(path), type=OBJ_WORKBOOK)
        objects: list[Object] = [workbook]
        relationships: list[Relationship] = [
            Relationship(object_id=workbook.id, qualifier="target")
        ]

        # A failed call still records the attempt against the workbook, but its
        # output is absent — there is nothing more to relate.
        if result.ok:
            self._enrich(tool, path, result.output, objects, relationships)

        activity = tool
        if self._qualify_sheets and tool in _SHEET_SCOPED and params.get("sheet"):
            activity = f"{tool}:{params['sheet']}"

        return EventMapping(
            activity=activity,
            attributes=attributes,
            objects=objects,
            relationships=relationships,
        )

    # ------------------------------------------------------------------
    # Per-tool enrichment
    # ------------------------------------------------------------------

    def _enrich(
        self,
        tool: str,
        path: str,
        output: Any,
        objects: list[Object],
        relationships: list[Relationship],
    ) -> None:
        """Add the sheet/macro objects and relationships a tool's output implies."""
        if tool == "list_sheets":
            for name in self._names(output):
                self._add_sheet(path, name, "lists", objects, relationships)
        elif tool == "describe_sheet":
            self._add_sheet(path, output.get("name"), "describes", objects, relationships)
            for macro in output.get("read_by", []):
                self._add_macro(path, macro, "reader", objects, relationships)
            for macro in output.get("written_by", []):
                self._add_macro(path, macro, "writer", objects, relationships)
        elif tool == "list_macros":
            for name in self._names(output):
                self._add_macro(path, name, "lists", objects, relationships)
        elif tool == "get_call_graph":
            self._add_macro(path, output.get("macro"), "subject", objects, relationships)
            for macro in output.get("calls", []):
                self._add_macro(path, macro, "calls", objects, relationships)
            for macro in output.get("called_by", []):
                self._add_macro(path, macro, "called_by", objects, relationships)
        elif tool == "get_data_flow":
            self._add_macro(path, output.get("macro"), "subject", objects, relationships)
            for sheet in output.get("reads", []):
                self._add_sheet(path, sheet, "reads", objects, relationships)
            for sheet in output.get("writes", []):
                self._add_sheet(path, sheet, "writes", objects, relationships)
        elif tool == "read_range":
            self._add_sheet(path, output.get("sheet"), "reads", objects, relationships)
        elif tool == "write_cell":
            # The write tool's result is a CellEdit: the sheet it mutated.
            self._add_sheet(path, output.get("sheet"), "writes", objects, relationships)
        elif tool == "run_macro":
            # The run tool's result is a MacroRun: the macro it executed.
            self._add_macro(path, output.get("macro"), "runs", objects, relationships)

    def _add_sheet(
        self,
        path: str,
        name: Any,
        qualifier: str,
        objects: list[Object],
        relationships: list[Relationship],
    ) -> None:
        """Add a sheet object (belonging to the workbook) and relate the event."""
        if not name:
            return
        obj = Object(
            id=self._sheet_id(path, str(name)),
            type=OBJ_SHEET,
            relationships=[
                Relationship(object_id=self._workbook_id(path), qualifier="belongs_to")
            ],
        )
        objects.append(obj)
        relationships.append(Relationship(object_id=obj.id, qualifier=qualifier))

    def _add_macro(
        self,
        path: str,
        name: Any,
        qualifier: str,
        objects: list[Object],
        relationships: list[Relationship],
    ) -> None:
        """Add a macro object (belonging to the workbook) and relate the event."""
        if not name:
            return
        obj = Object(
            id=self._macro_id(path, str(name)),
            type=OBJ_MACRO,
            relationships=[
                Relationship(object_id=self._workbook_id(path), qualifier="belongs_to")
            ],
        )
        objects.append(obj)
        relationships.append(Relationship(object_id=obj.id, qualifier=qualifier))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _names(output: Any) -> list[str]:
        """Extract a list of names from a list-of-dicts (or list-of-str) output."""
        names: list[str] = []
        for item in output or []:
            if isinstance(item, dict):
                name = item.get("name")
                if name:
                    names.append(str(name))
            elif item:
                names.append(str(item))
        return names

    @staticmethod
    def _workbook_id(path: str) -> str:
        """Deterministic id for the workbook at ``path``."""
        return f"{OBJ_WORKBOOK}:{path}"

    @staticmethod
    def _sheet_id(path: str, name: str) -> str:
        """Deterministic id for a sheet, namespaced by its workbook."""
        return f"{OBJ_SHEET}:{path}:{name}"

    @staticmethod
    def _macro_id(path: str, name: str) -> str:
        """Deterministic id for a macro, namespaced by its workbook."""
        return f"{OBJ_MACRO}:{path}:{name}"
