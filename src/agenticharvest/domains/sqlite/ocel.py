#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""SQLite's OCEL mapping — the domain half of the event log.

``SqliteOCEL`` is the one place that knows a SQLite tool call touches a
*database* made of *tables*. Like ``ExcelOCEL`` it extends the generic mapper:
every call relates to its ``database`` object (the case a miner keys on), and a
call that names a table relates to that ``table`` too — ``reads`` for a query,
``writes`` for an ``execute``, ``describes`` for ``describe_table``.

Activities can be table-qualified (``query:tickets``), so a mined process shows
"look at the open tickets, then look up the agents, then assign" rather than
"query, query, execute".
"""

from __future__ import annotations

import re
from typing import Any

from agenticharvest.core.events.mapping import EventMapping, GenericOcelMapper
from agenticharvest.core.events.model import Object, Relationship
from agenticharvest.core.tools.result import ToolResult

OBJ_DATABASE = "database"
OBJ_TABLE = "table"

_ENRICHED = frozenset(
    {"list_tables", "describe_table", "list_triggers", "query", "execute"}
)

# The first table a statement reads from or writes to, for qualifying activities.
_FROM_TABLE = re.compile(r"\bFROM\s+[\"`\[]?(\w+)", re.IGNORECASE)
_WRITE_TABLE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)"
    r"\s+[\"`\[]?(\w+)",
    re.IGNORECASE,
)


def table_of(tool: str, params: dict) -> str:
    """Return the table a call acts on, or ``""`` when it names none."""
    if tool == "describe_table":
        return str(params.get("table") or "")
    sql = str(params.get("sql") or "")
    pattern = _WRITE_TABLE if tool == "execute" else _FROM_TABLE
    match = pattern.search(sql)
    return match.group(1) if match else ""


class SqliteOCEL(GenericOcelMapper):
    """Maps SQLite tool calls to database/table objects and relationships.

    Args:
        qualify_tables: When ``True``, a call that acts on one table records its
            activity as ``tool:table`` (``query:tickets``). Off by default.
    """

    def __init__(self, *, qualify_tables: bool = False) -> None:
        self._qualify_tables = qualify_tables

    def map(self, tool: str, params: dict, result: ToolResult) -> EventMapping:
        """Emit typed objects for a SQLite tool; defer unknown tools to the base."""
        path = params.get("path")
        if tool not in _ENRICHED or not path:
            return super().map(tool, params, result)

        path = str(path)
        attributes: dict[str, Any] = {"ok": result.ok}
        if result.error:
            attributes["error"] = result.error

        database = Object(id=f"{OBJ_DATABASE}:{path}", type=OBJ_DATABASE)
        objects: list[Object] = [database]
        relationships = [Relationship(object_id=database.id, qualifier="target")]

        table = table_of(tool, params)
        if table:
            qualifier = {"execute": "writes", "describe_table": "describes"}.get(tool, "reads")
            objects.append(
                Object(
                    id=f"{OBJ_TABLE}:{path}:{table}",
                    type=OBJ_TABLE,
                    relationships=[Relationship(object_id=database.id, qualifier="belongs_to")],
                )
            )
            relationships.append(
                Relationship(object_id=f"{OBJ_TABLE}:{path}:{table}", qualifier=qualifier)
            )

        activity = f"{tool}:{table}" if self._qualify_tables and table else tool
        return EventMapping(
            activity=activity,
            attributes=attributes,
            objects=objects,
            relationships=relationships,
        )


__all__ = ["OBJ_DATABASE", "OBJ_TABLE", "SqliteOCEL", "table_of"]
