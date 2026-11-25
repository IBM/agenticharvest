#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The SQLite tool specs and their handlers.

Four tools read the database and one changes it. Reads open the file in SQLite's
read-only URI mode (``mode=ro``), so even a malicious ``query`` cannot write: the
engine itself refuses. ``query`` additionally accepts a single ``SELECT`` / ``WITH``
statement only. ``execute`` is the one write tool — it carries ``Effect.WRITE``,
so the gateway's dry-run gate demands ``confirm=true`` — and it runs exactly one
DML statement inside a transaction, reporting the rows it changed.

Every handler takes the database ``path`` and returns plain JSON, matching the
Excel tools' shape, so channels, taps, and agents treat both domains alike.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agenticharvest.core.tools.spec import Effect, ToolSpec

#: The most rows ``query`` returns in one call, whatever the caller asks.
MAX_QUERY_ROWS = 500

# DML that ``execute`` accepts. DDL (CREATE / DROP / ALTER), PRAGMA, ATTACH, and
# transaction control are refused: an agent changes data, not the schema.
_DML = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)
_READ = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

# The tables a trigger body writes to: INSERT INTO t / UPDATE t / DELETE FROM t.
_TRIGGER_WRITES = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+"
    r"[\"`\[]?(\w+)",
    re.IGNORECASE,
)


def _require(params: dict, name: str) -> str:
    """Return a required string parameter, raising a clear error if it is absent."""
    value = params.get(name)
    if not value:
        raise ValueError(f"Missing required parameter: '{name}'.")
    return str(value)


def _database(params: dict) -> Path:
    """Resolve and validate the ``path`` parameter to an existing database file."""
    path = Path(_require(params, "path")).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"No database file at '{path}'.")
    return path


@contextmanager
def _connect(path: Path, *, read_only: bool) -> Iterator[sqlite3.Connection]:
    """Open ``path``; read-only connections are enforced by SQLite itself."""
    uri = f"{path.resolve().as_uri()}?mode={'ro' if read_only else 'rw'}"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _single_statement(sql: str) -> str:
    """Return ``sql`` as one statement, refusing a batch of several.

    Walks the text with SQLite's own ``complete_statement`` so a ``;`` inside a
    string literal (``WHERE note = 'a;b'``) does not count as a terminator.
    """
    text = sql.strip()
    for index, char in enumerate(text):
        if char == ";" and sqlite3.complete_statement(text[: index + 1]):
            if text[index + 1 :].strip(" \t\r\n;"):
                raise ValueError("Pass exactly one SQL statement.")
            return text[:index].strip()
    return text


def _quote(identifier: str) -> str:
    """Quote an SQL identifier for safe interpolation."""
    return '"' + identifier.replace('"', '""') + '"'


def _known_tables(connection: sqlite3.Connection) -> dict[str, str]:
    """Return table/view name -> type for every user object in the database."""
    rows = connection.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {row["name"]: row["type"] for row in rows}


def _triggers(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every trigger with its table, timing/event, and the tables it writes."""
    rows = connection.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
    ).fetchall()
    triggers = []
    for row in rows:
        sql = row["sql"] or ""
        header = re.search(
            r"\b(BEFORE|AFTER|INSTEAD\s+OF)?\s*(INSERT|UPDATE|DELETE)\b", sql, re.IGNORECASE
        )
        body = sql[sql.upper().find("BEGIN"):] if "BEGIN" in sql.upper() else ""
        timing = (header.group(1) or "AFTER").upper() if header else ""
        triggers.append(
            {
                "name": row["name"],
                "table": row["tbl_name"],
                "event": f"{timing} {header.group(2).upper()}".strip() if header else "",
                "writes": sorted(set(_TRIGGER_WRITES.findall(body))),
            }
        )
    return triggers


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------


def list_tables(params: dict) -> list[dict[str, Any]]:
    """List every table and view with its row count."""
    with _connect(_database(params), read_only=True) as connection:
        return [
            {
                "name": name,
                "type": kind,
                "rows": connection.execute(
                    f"SELECT COUNT(*) FROM {_quote(name)}"
                ).fetchone()[0],
            }
            for name, kind in _known_tables(connection).items()
        ]


def describe_table(params: dict) -> dict[str, Any]:
    """Describe one table: columns, primary key, foreign keys, and its triggers."""
    table = _require(params, "table")
    with _connect(_database(params), read_only=True) as connection:
        known = _known_tables(connection)
        if table not in known:
            raise ValueError(f"No table named '{table}'. Tables: {', '.join(known)}.")
        columns = [
            {
                "name": row["name"],
                "type": row["type"],
                "nullable": not row["notnull"],
                "primary_key": bool(row["pk"]),
            }
            for row in connection.execute(f"PRAGMA table_info({_quote(table)})")
        ]
        foreign_keys = [
            {"column": row["from"], "references": f"{row['table']}.{row['to']}"}
            for row in connection.execute(f"PRAGMA foreign_key_list({_quote(table)})")
        ]
        triggers = _triggers(connection)
    return {
        "name": table,
        "type": known[table],
        "columns": columns,
        "foreign_keys": foreign_keys,
        "triggers": [t["name"] for t in triggers if t["table"] == table],
        "written_by_triggers": [t["name"] for t in triggers if table in t["writes"]],
    }


def list_triggers(params: dict) -> list[dict[str, Any]]:
    """List every trigger with the table it fires on and the tables it writes."""
    with _connect(_database(params), read_only=True) as connection:
        return _triggers(connection)


def query(params: dict) -> dict[str, Any]:
    """Run one read-only SELECT and return its columns and rows."""
    sql = _single_statement(_require(params, "sql"))
    if not _READ.match(sql):
        raise ValueError("query accepts a single SELECT (or WITH ... SELECT) statement.")
    max_rows = min(int(params.get("max_rows") or 100), MAX_QUERY_ROWS)
    with _connect(_database(params), read_only=True) as connection:
        cursor = connection.execute(sql, params.get("args") or [])
        columns = [d[0] for d in cursor.description or []]
        rows = [list(row) for row in cursor.fetchmany(max_rows + 1)]
    return {
        "columns": columns,
        "rows": rows[:max_rows],
        "truncated": len(rows) > max_rows,
    }


def execute(params: dict) -> dict[str, Any]:
    """Run one INSERT / UPDATE / DELETE in a transaction; report rows changed."""
    sql = _single_statement(_require(params, "sql"))
    if not _DML.match(sql):
        raise ValueError("execute accepts a single INSERT, UPDATE, DELETE, or REPLACE.")
    with _connect(_database(params), read_only=False) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:  # commits on success, rolls back on error
            cursor = connection.execute(sql, params.get("args") or [])
        return {"rows_changed": cursor.rowcount, "table": _target_table(sql)}


def _target_table(sql: str) -> str:
    """Best-effort name of the table a DML statement changes."""
    match = _TRIGGER_WRITES.search(sql) or re.search(
        r"\bREPLACE\s+INTO\s+[\"`\[]?(\w+)", sql, re.IGNORECASE
    )
    return match.group(1) if match else ""


# ----------------------------------------------------------------------
# Specs, paired with their handlers
# ----------------------------------------------------------------------

_PATH_PROP = {"path": {"type": "string", "description": "Path to the SQLite database file."}}
_ARGS_PROP = {
    "args": {
        "type": "array",
        "description": "Values bound to the statement's ? placeholders, in order.",
        "items": {},
    }
}


def sqlite_tools() -> list[tuple[ToolSpec, Any]]:
    """Return the SQLite domain's ``(spec, handler)`` pairs."""
    return [
        (
            ToolSpec(
                name="list_tables",
                description="List the tables and views in a database with their row counts.",
                input_schema={
                    "type": "object",
                    "properties": dict(_PATH_PROP),
                    "required": ["path"],
                },
            ),
            list_tables,
        ),
        (
            ToolSpec(
                name="describe_table",
                description="Describe a table's columns, keys, and the triggers that "
                "touch it.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "table": {"type": "string", "description": "Table name."},
                    },
                    "required": ["path", "table"],
                },
            ),
            describe_table,
        ),
        (
            ToolSpec(
                name="list_triggers",
                description="List the triggers in a database and the tables each "
                "one writes.",
                input_schema={
                    "type": "object",
                    "properties": dict(_PATH_PROP),
                    "required": ["path"],
                },
            ),
            list_triggers,
        ),
        (
            ToolSpec(
                name="query",
                description="Run one read-only SELECT statement and return the rows.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "sql": {"type": "string", "description": "A single SELECT statement."},
                        **_ARGS_PROP,
                        "max_rows": {
                            "type": "integer",
                            "description": f"Most rows to return (default 100, "
                            f"at most {MAX_QUERY_ROWS}).",
                        },
                    },
                    "required": ["path", "sql"],
                },
            ),
            query,
        ),
        (
            ToolSpec(
                name="execute",
                description="Run one INSERT, UPDATE, or DELETE statement against the "
                "database.",
                effect=Effect.WRITE,
                input_schema={
                    "type": "object",
                    "properties": {
                        **_PATH_PROP,
                        "sql": {"type": "string", "description": "A single DML statement."},
                        **_ARGS_PROP,
                        "confirm": {
                            "type": "boolean",
                            "description": "Must be true to apply this change.",
                        },
                    },
                    "required": ["path", "sql"],
                },
            ),
            execute,
        ),
    ]
