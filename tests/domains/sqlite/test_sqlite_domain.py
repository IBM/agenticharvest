#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the SQLite domain: tools, safety guards, and the OCEL mapper."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agenticharvest.core.events import OcelTap
from agenticharvest.core.tools import ToolRegistry
from agenticharvest.core.tools.result import ToolResult
from agenticharvest.domains.sqlite import domain
from agenticharvest.domains.sqlite.ocel import SqliteOCEL, table_of
from agenticharvest.domains.sqlite.samples import build_support_db
from agenticharvest.domains.sqlite.tools import (
    describe_table,
    execute,
    list_tables,
    list_triggers,
    query,
    sqlite_tools,
)
from agenticharvest.gateway.dispatch import ToolRequest, dispatch


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return build_support_db(tmp_path / "support.db")


def test_domain_exposes_four_reads_and_one_gated_write() -> None:
    specs = {s.name: s for s in domain.tool_specs()}
    assert set(specs) == {"list_tables", "describe_table", "list_triggers", "query", "execute"}
    assert {n for n, s in specs.items() if s.effect == "write"} == {"execute"}
    assert all(s.domain == "sqlite" for s in specs.values())
    assert all(len(s.description) < 120 for s in specs.values())


def test_list_tables_counts_rows(db: Path) -> None:
    tables = {t["name"]: t for t in list_tables({"path": str(db)})}
    assert set(tables) == {"agents", "tickets", "assignments", "audit_log"}
    assert tables["agents"]["rows"] == 5
    assert tables["tickets"]["rows"] == 3


def test_describe_table_reports_keys_and_trigger_writers(db: Path) -> None:
    detail = describe_table({"path": str(db), "table": "tickets"})
    columns = {c["name"]: c for c in detail["columns"]}
    assert columns["id"]["primary_key"] is True
    assert detail["foreign_keys"] == [{"column": "assignee", "references": "agents.id"}]
    assert detail["written_by_triggers"] == ["log_assignment"]

    with pytest.raises(ValueError, match="No table named"):
        describe_table({"path": str(db), "table": "nope"})


def test_list_triggers_recovers_event_and_writes(db: Path) -> None:
    (trigger,) = list_triggers({"path": str(db)})
    assert trigger["name"] == "log_assignment"
    assert trigger["table"] == "assignments"
    assert trigger["event"] == "AFTER INSERT"
    assert trigger["writes"] == ["agents", "audit_log", "tickets"]


def test_query_returns_rows_and_caps_them(db: Path) -> None:
    result = query(
        {"path": str(db), "sql": "SELECT id FROM tickets WHERE product = ?", "args": ["billing"]}
    )
    assert result == {"columns": ["id"], "rows": [[1001]], "truncated": False}

    capped = query({"path": str(db), "sql": "SELECT id FROM agents", "max_rows": 2})
    assert len(capped["rows"]) == 2
    assert capped["truncated"] is True


def test_query_allows_semicolons_inside_literals(db: Path) -> None:
    result = query({"path": str(db), "sql": "SELECT 'a;b' AS x;"})
    assert result["rows"] == [["a;b"]]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM agents",
        "SELECT 1; DROP TABLE agents",
        "PRAGMA writable_schema = 1",
    ],
)
def test_query_refuses_anything_but_one_select(db: Path, sql: str) -> None:
    with pytest.raises(ValueError):
        query({"path": str(db), "sql": sql})


def test_query_connection_is_read_only_even_for_a_writing_cte(db: Path) -> None:
    # Passes the SELECT/WITH prefix check, but SQLite itself refuses the write.
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        query({"path": str(db), "sql": "WITH x AS (SELECT 1) DELETE FROM agents"})
    assert list_tables({"path": str(db)})[0]["rows"] == 5


def test_execute_applies_dml_and_fires_the_trigger(db: Path) -> None:
    result = execute(
        {
            "path": str(db),
            "sql": "INSERT INTO assignments (ticket_id, agent_id) VALUES (?, ?)",
            "args": [1001, 2],
        }
    )
    assert result == {"rows_changed": 1, "table": "assignments"}

    ticket = query({"path": str(db), "sql": "SELECT status, assignee FROM tickets WHERE id=1001"})
    assert ticket["rows"] == [["assigned", 2]]
    audit = query({"path": str(db), "sql": "SELECT note FROM audit_log"})
    assert audit["rows"] == [["assigned to agent 2"]]


@pytest.mark.parametrize(
    "sql",
    ["DROP TABLE agents", "CREATE TABLE x (y)", "SELECT 1", "UPDATE agents SET name='x'; DELETE FROM agents"],
)
def test_execute_refuses_ddl_reads_and_batches(db: Path, sql: str) -> None:
    with pytest.raises(ValueError):
        execute({"path": str(db), "sql": sql})


def test_execute_enforces_foreign_keys(db: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        execute(
            {"path": str(db), "sql": "INSERT INTO assignments VALUES (?, ?)", "args": [1001, 99]}
        )


def test_missing_database_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No database file"):
        list_tables({"path": str(tmp_path / "absent.db")})


def test_dispatch_gates_execute(db: Path) -> None:
    registry = ToolRegistry()
    for spec, handler in sqlite_tools():
        registry.register(spec, handler)
    sql = "UPDATE agents SET open_load = 0"

    blocked = dispatch(registry, ToolRequest(tool="execute", params={"path": str(db), "sql": sql}))
    assert blocked.ok is False
    assert "confirm" in (blocked.error or "")

    allowed = dispatch(
        registry,
        ToolRequest(tool="execute", params={"path": str(db), "sql": sql, "confirm": True}),
    )
    assert allowed.ok is True
    assert allowed.output["rows_changed"] == 5


def test_table_of_reads_the_statement() -> None:
    assert table_of("query", {"sql": "SELECT * FROM tickets t JOIN agents a"}) == "tickets"
    assert table_of("execute", {"sql": "INSERT INTO assignments VALUES (1, 2)"}) == "assignments"
    assert table_of("execute", {"sql": "UPDATE agents SET x = 1"}) == "agents"
    assert table_of("describe_table", {"table": "tickets"}) == "tickets"
    assert table_of("list_tables", {}) == ""


def test_ocel_mapper_relates_database_and_table(db: Path) -> None:
    tap = OcelTap(mapper=SqliteOCEL(qualify_tables=True))
    registry = ToolRegistry(taps=[tap])
    for spec, handler in sqlite_tools():
        registry.register(spec, handler)

    registry.call("query", {"path": str(db), "sql": "SELECT * FROM tickets"})
    registry.call("list_tables", {"path": str(db)})

    first, second = tap.log.events
    assert first.activity == "query:tickets"
    assert second.activity == "list_tables"
    qualifiers = {r.object_id: r.qualifier for r in first.relationships}
    assert qualifiers[f"database:{db}"] == "target"
    assert qualifiers[f"table:{db}:tickets"] == "reads"
    assert {o.type for o in tap.log.objects} == {"database", "table"}


def test_ocel_mapper_records_failures_and_defers_unknown_tools(db: Path) -> None:
    mapper = SqliteOCEL()
    failed = mapper.map("query", {"path": str(db), "sql": "SELECT 1"}, ToolResult.failure("x"))
    assert failed.attributes == {"ok": False, "error": "x"}

    other = mapper.map("something_else", {"path": str(db)}, ToolResult.success(None))
    assert [o.type for o in other.objects] == ["artifact"]
