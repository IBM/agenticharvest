#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Cross-system correlation: routing calls to domain mappers, relating business keys.

The calls are real: a SQLite database driven through the SQLite tools, plus a
second domain registered from plain handlers, so one registry spans two systems
the way a cross-system process does.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agenticharvest.core.events import (
    CompositeMapper,
    Correlator,
    KeyRule,
    OcelLog,
    OcelTap,
    ValueRule,
    suggest_keys,
)
from agenticharvest.core.events.correlate import walk
from agenticharvest.core.tools import InMemoryTap, ToolRegistry
from agenticharvest.core.tools.spec import Effect, ToolSpec
from agenticharvest.domains.sqlite.ocel import SqliteOCEL
from agenticharvest.domains.sqlite.tools import sqlite_tools
from agenticharvest.gateway.dispatch import ToolRequest, dispatch
from agenticharvest.mining import traces


def _shipping_tools() -> list[tuple[ToolSpec, object]]:
    """A second system: a 'shipping' domain keeping bookings in a dict."""
    bookings: dict[str, dict] = {}

    def book(params: dict) -> dict:
        ref = f"S-{len(bookings) + 1}"
        bookings[ref] = dict(params["body"])
        return {"ref": ref, "booking": bookings[ref]}

    def lookup(params: dict) -> dict:
        return {"rows": [{"ref": r, **b} for r, b in bookings.items()
                         if b["order_id"] == params["order_id"]]}

    return [
        (ToolSpec(name="book", description="Book.", effect=Effect.WRITE,
                  input_schema={"type": "object"}, domain="shipping"), book),
        (ToolSpec(name="lookup", description="Look up.", input_schema={"type": "object"},
                  domain="shipping"), lookup),
    ]


class _ShippingOCEL:
    """The shipping domain's mapper: every call targets the one shipping system."""

    def map(self, tool, params, result):  # type: ignore[no-untyped-def]
        from agenticharvest.core.events import EventMapping, Object, Relationship

        return EventMapping(
            activity=tool,
            attributes={"ok": result.ok},
            objects=[Object(id="system:shipping", type="system")],
            relationships=[Relationship(object_id="system:shipping", qualifier="target")],
        )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "orders.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE orders (id TEXT PRIMARY KEY, customer TEXT, ref TEXT);"
        "INSERT INTO orders VALUES ('O-1001', 'Acme', NULL), ('O-1002', 'Bolt', NULL);"
    )
    connection.commit()
    connection.close()
    return path


RULES = [
    KeyRule(object_type="order", tools=["query"], result=["rows"], pattern=r"\bO-\d{4}\b"),
    KeyRule(object_type="order", tools=["execute"], param="args", pattern=r"\bO-\d{4}\b"),
    KeyRule(object_type="order", tools=["book"], param="body.order_id"),
    KeyRule(object_type="order", tools=["lookup"], param="order_id"),
]


def _registry(log: OcelLog, mapper: object, records: InMemoryTap | None = None) -> ToolRegistry:
    taps: list = [OcelTap(mapper=mapper, log=log)]  # type: ignore[arg-type]
    if records is not None:
        taps.append(records)
    registry = ToolRegistry(taps=taps)
    for spec, handler in [*sqlite_tools(), *_shipping_tools()]:
        registry.register(spec, handler)  # type: ignore[arg-type]
    return registry


def _mapper(**kwargs: object) -> Correlator:
    domains = {s.name: "sqlite" for s, _ in sqlite_tools()}
    domains.update({s.name: "shipping" for s, _ in _shipping_tools()})
    inner = CompositeMapper({"sqlite": SqliteOCEL(qualify_tables=True),
                             "shipping": _ShippingOCEL()}, domains)
    return Correlator(inner, RULES, **kwargs)  # type: ignore[arg-type]


def _call(registry: ToolRegistry, tool: str, **params: object) -> object:
    result = dispatch(registry, ToolRequest(tool=tool, params=params))
    assert result.ok, result.error
    return result.output


def _fulfill(registry: ToolRegistry, db: Path) -> None:
    rows = _call(registry, "query", path=str(db), sql="SELECT id, customer FROM orders")["rows"]
    for order_id, customer in rows:
        booked = _call(registry, "book", body={"order_id": order_id, "customer": customer},
                       confirm=True)
        found = _call(registry, "lookup", order_id=order_id)
        assert found["rows"][0]["ref"] == booked["ref"]
        _call(registry, "execute", path=str(db), sql="UPDATE orders SET ref = ? WHERE id = ?",
              args=[booked["ref"], order_id], confirm=True)


def test_composite_mapper_routes_each_call_to_its_domain(db: Path) -> None:
    log = OcelLog()
    _fulfill(_registry(log, _mapper()), db)
    assert {"database", "table", "system", "order"} <= set(log.object_types())
    domains = [e.attributes["domain"] for e in log.events]
    assert domains[:3] == ["sqlite", "shipping", "shipping"]


def test_correlator_puts_every_call_on_the_order_it_names(db: Path) -> None:
    log = OcelLog()
    _fulfill(_registry(log, _mapper()), db)
    by_order = traces(log, case_type="order")
    # The query names both orders, so both traces start with it.
    assert by_order == {
        "order:O-1001": ["query:orders", "book", "lookup", "execute:orders"],
        "order:O-1002": ["query:orders", "book", "lookup", "execute:orders"],
    }
    # A confirmed call relates as a write, any other as a read.
    book = next(e for e in log.events if e.activity == "book")
    assert {r.qualifier for r in book.relationships if r.object_id.startswith("order:")} == {
        "writes"
    }


def test_links_join_two_keys_seen_in_one_call(db: Path) -> None:
    rules = [*RULES, KeyRule(object_type="shipment", tools=["book"], result=["ref"])]
    domains = {s.name: "sqlite" for s, _ in sqlite_tools()}
    domains.update({s.name: "shipping" for s, _ in _shipping_tools()})
    inner = CompositeMapper({"sqlite": SqliteOCEL(), "shipping": _ShippingOCEL()}, domains)
    log = OcelLog()
    registry = _registry(log, Correlator(inner, rules, links=[("shipment", "fulfills", "order")]))
    _fulfill(registry, db)
    shipment = log.object("shipment:S-1")
    assert shipment is not None
    assert [(r.object_id, r.qualifier) for r in shipment.relationships] == [
        ("order:O-1001", "fulfills")
    ]


def test_value_rules_record_what_was_read_and_written(db: Path) -> None:
    values = [
        ValueRule(field="row", direction="read", tools=["query"], result=["rows.*.*"]),
        ValueRule(field="customer", direction="write", tools=["book"], param="body.customer"),
    ]
    log = OcelLog()
    _fulfill(_registry(log, _mapper(values=values)), db)
    query, book = log.events[0], log.events[1]
    assert query.attributes["read_values"] == {"row": ["O-1001", "Acme", "O-1002", "Bolt"]}
    assert book.attributes["written_values"] == {"customer": ["Acme"]}


def test_inherit_lends_held_keys_to_a_keyless_call(db: Path) -> None:
    log = OcelLog()
    registry = _registry(log, _mapper(inherit=["database"]))
    # The write names the order; the follow-up read names none, but is about
    # what the database now holds.
    _call(registry, "execute", path=str(db), sql="UPDATE orders SET ref = 'x' WHERE id = ?",
          args=["O-1001"], confirm=True)
    _call(registry, "list_tables", path=str(db))
    last = log.events[-1]
    assert [(r.object_id, r.qualifier) for r in last.relationships if
            r.object_id.startswith("order:")] == [("order:O-1001", "context")]


def test_suggest_keys_finds_values_that_cross_systems(db: Path) -> None:
    records = InMemoryTap()
    _fulfill(_registry(OcelLog(), _mapper(), records), db)
    suggestions = suggest_keys(records.records, _mapper(), artifact_types=["database", "system"])
    patterns = [s.pattern for s in suggestions]
    assert r"\bO\-\d+\b" in patterns
    order = next(s for s in suggestions if s.pattern == r"\bO\-\d+\b")
    assert len(order.systems) == 2 and "O-1001" in order.examples


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.b", [1]),
        ("rows.*.0", ["x", "y"]),
        ("rows.-1.1", [2]),
        ("data.*.id", [7, 8]),
        ("missing.path", []),
        ("list", [1, 2]),
    ],
)
def test_walk(path: str, expected: list) -> None:
    node = {"a": {"b": 1}, "rows": [["x", 1], ["y", 2]], "data": [{"id": 7}, {"id": 8}],
            "list": [1, 2]}
    assert list(walk(node, path)) == expected
