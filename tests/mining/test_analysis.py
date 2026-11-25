#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Cross-system analysis: handoffs, waits, the object-centric DFG, carries, and rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agenticharvest.core.events import Event, Object, OcelLog, Relationship
from agenticharvest.mining.analysis import (
    Rule,
    case_events,
    check_rules,
    handoffs,
    object_centric_dfg,
    performance,
    retyped_values,
)

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


def _log(cases: dict[str, list[tuple[str, str, float, dict]]]) -> OcelLog:
    """Build a log: case -> [(activity, system, minutes from T0, attributes)]."""
    log = OcelLog()
    n = 0
    for case, steps in cases.items():
        log.ensure_object(Object(id=case, type="order"))
        for activity, system, minutes, attributes in steps:
            log.ensure_object(Object(id=system, type="system"))
            n += 1
            log.add_event(Event(
                id=f"e{n}", activity=activity, time=T0 + timedelta(minutes=minutes),
                attributes=attributes,
                relationships=[Relationship(object_id=system), Relationship(object_id=case)],
            ))
    return log


@pytest.fixture
def log() -> OcelLog:
    return _log({
        "order:1": [
            ("pull", "system:db", 0, {"read_values": {"row": ["O-1", "Acme"]}}),
            ("book", "system:web", 10, {"written_values": {"customer": ["Acme"]},
                                        "read_values": {"ref": [77]}}),
            ("close", "system:db", 12, {"written_values": {"ref": ["77"]}}),
        ],
        "order:2": [
            ("pull", "system:db", 0, {"read_values": {"row": ["O-2", "Bolt"]}}),
            ("book", "system:web", 30, {"written_values": {"customer": ["Bolt"]}}),
            ("close", "system:db", 31, {}),
        ],
    })


def test_case_events_orders_each_case_by_time(log: OcelLog) -> None:
    cases = case_events(log, "order")
    assert [e.activity for e in cases["order:1"]] == ["pull", "book", "close"]


def test_handoffs_count_system_changes(log: OcelLog) -> None:
    result = [(h.source, h.target, h.count, h.cases) for h in handoffs(log, "order", ["system"])]
    assert result == [("system:db", "system:web", 2, 2), ("system:web", "system:db", 2, 2)]


def test_performance_ranks_the_slowest_wait_first(log: OcelLog) -> None:
    edges = performance(log, "order")
    assert (edges[0].source, edges[0].target) == ("pull", "book")
    assert edges[0].median_seconds == 20 * 60  # (10 + 30) / 2 minutes
    assert edges[0].max_seconds == 30 * 60


def test_retyped_values_find_carries_between_systems(log: OcelLog) -> None:
    carries = [(c.field, c.source, c.target, c.source_activity, c.target_activity, c.cases)
               for c in retyped_values(log, "order", ["system"])]
    assert carries == [
        ("customer", "system:db", "system:web", "pull", "book", 2),
        ("ref", "system:web", "system:db", "book", "close", 1),  # 77 matched "77"
    ]


def test_a_value_written_back_to_its_own_system_is_not_a_carry() -> None:
    log = _log({"order:1": [
        ("read", "system:db", 0, {"read_values": {"x": ["v"]}}),
        ("write", "system:db", 1, {"written_values": {"x": ["v"]}}),
    ]})
    assert retyped_values(log, "order", ["system"]) == []


def test_object_centric_dfg_keeps_each_types_view() -> None:
    log = OcelLog()
    for oid, otype in (("req:1", "requisition"), ("cand:a", "candidate"),
                       ("cand:b", "candidate")):
        log.ensure_object(Object(id=oid, type=otype))
    steps = [("open", ["req:1"]), ("apply", ["req:1", "cand:a"]),
             ("apply", ["req:1", "cand:b"]), ("hire", ["req:1", "cand:a"]),
             ("reject", ["cand:b"])]
    for n, (activity, objects) in enumerate(steps):
        log.add_event(Event(id=f"e{n}", activity=activity, time=T0 + timedelta(minutes=n),
                            relationships=[Relationship(object_id=o) for o in objects]))
    dfg = object_centric_dfg(log, ["requisition", "candidate"])
    assert dfg.object_types == {"requisition": 1, "candidate": 2}
    req = {(e.source, e.target): e.count for e in dfg.edges_of("requisition")}
    assert req == {("open", "apply"): 1, ("apply", "apply"): 1, ("apply", "hire"): 1}
    cand = {(e.source, e.target): e.count for e in dfg.edges_of("candidate")}
    assert cand == {("apply", "hire"): 1, ("apply", "reject"): 1}
    assert dfg.activities["apply"] == ["requisition", "candidate"]
    assert dfg.starts["candidate"] == {"apply": 2}
    assert dfg.ends["candidate"] == {"hire": 1, "reject": 1}


def test_rules(log: OcelLog) -> None:
    rules = [
        Rule(name="pull first", kind="precedes", first="pull", then="book"),
        Rule(name="booked then closed", kind="responds", first="book", then="close"),
        Rule(name="booked within 15m", kind="within", first="pull", then="book",
             max_hours=0.25),
        Rule(name="has close", kind="exists", first="close"),
        Rule(name="never cancel", kind="absent", first="cancel"),
        Rule(name="prefix match", kind="exists", first="cl*"),
    ]
    report = check_rules(log, "order", rules)
    assert report.cases == 2
    assert report.passed == {"pull first": 2, "booked then closed": 2, "booked within 15m": 1,
                             "has close": 2, "never cancel": 2, "prefix match": 2}
    [violation] = report.violations
    assert violation.case == "order:2" and "30.0h" not in violation.detail
    assert "0.5h after pull" in violation.detail
    assert not report.ok


def test_precedes_and_responds_report_what_is_missing() -> None:
    log = _log({"order:1": [("book", "system:web", 0, {}), ("pull", "system:db", 1, {})]})
    report = check_rules(log, "order", [
        Rule(name="pull first", kind="precedes", first="pull", then="book"),
        Rule(name="pull then close", kind="responds", first="pull", then="close"),
    ])
    details = {v.rule: v.detail for v in report.violations}
    assert "with no pull before it" in details["pull first"]
    assert "never followed by close" in details["pull then close"]


def test_unknown_rule_kind_is_an_error(log: OcelLog) -> None:
    with pytest.raises(ValueError, match="Unknown rule kind"):
        check_rules(log, "order", [Rule(name="x", kind="sometimes", first="pull")])
