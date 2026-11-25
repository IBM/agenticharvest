#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for flattening an OCEL into a case-centric log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agenticharvest.core.events.model import Event, Object, OcelLog, Relationship
from agenticharvest.mining.log import flatten, infer_case_type

_T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _session_log() -> OcelLog:
    """A one-workbook session: three tool calls, each touching the workbook.

    Mirrors the shape ``ExcelOCEL`` produces: every event targets the workbook,
    later events also relate to sheets. The workbook is the natural case.
    """
    log = OcelLog()
    log.ensure_object(Object(id="workbook:book.xlsm", type="workbook"))
    log.ensure_object(Object(id="sheet:book.xlsm:Summary", type="sheet"))
    activities = ["list_sheets", "get_call_graph", "get_data_flow"]
    for i, activity in enumerate(activities):
        rels = [Relationship(object_id="workbook:book.xlsm", qualifier="target")]
        if activity == "get_data_flow":
            rels.append(Relationship(object_id="sheet:book.xlsm:Summary", qualifier="writes"))
        log.add_event(
            Event(
                id=f"e{i + 1}",
                activity=activity,
                time=_T0 + timedelta(minutes=i),
                relationships=rels,
            )
        )
    return log


def test_infer_case_type_picks_the_most_related_type() -> None:
    # The workbook is related by all three events; the sheet by only one.
    assert infer_case_type(_session_log()) == "workbook"


def test_infer_case_type_empty_log() -> None:
    assert infer_case_type(OcelLog()) == ""


def test_flatten_projects_events_onto_the_case() -> None:
    rows = flatten(_session_log())

    assert [r.activity for r in rows] == ["list_sheets", "get_call_graph", "get_data_flow"]
    assert {r.case_id for r in rows} == {"workbook:book.xlsm"}
    # Timestamps are carried through, in order.
    assert rows[0].timestamp == _T0
    assert rows[-1].timestamp == _T0 + timedelta(minutes=2)


def test_flatten_explicit_case_type_overrides_inference() -> None:
    # Keying on the sheet keeps only the one event that touched a sheet.
    rows = flatten(_session_log(), case_type="sheet")

    assert [r.activity for r in rows] == ["get_data_flow"]
    assert rows[0].case_id == "sheet:book.xlsm:Summary"


def test_flatten_emits_a_row_per_related_case_object() -> None:
    # One event that touches two workbooks becomes two rows (one per case).
    log = OcelLog()
    log.ensure_object(Object(id="workbook:a", type="workbook"))
    log.ensure_object(Object(id="workbook:b", type="workbook"))
    log.add_event(
        Event(
            id="e1",
            activity="compare",
            time=_T0,
            relationships=[
                Relationship(object_id="workbook:a", qualifier="target"),
                Relationship(object_id="workbook:b", qualifier="target"),
            ],
        )
    )

    rows = flatten(log, case_type="workbook")
    assert sorted(r.case_id for r in rows) == ["workbook:a", "workbook:b"]


def test_flatten_drops_events_with_no_case_object() -> None:
    log = OcelLog()
    log.ensure_object(Object(id="workbook:a", type="workbook"))
    log.add_event(Event(id="e1", activity="ping", time=_T0))  # relates to nothing

    assert flatten(log, case_type="workbook") == []


def test_flatten_records_resource_when_requested() -> None:
    log = OcelLog()
    log.ensure_object(Object(id="workbook:a", type="workbook"))
    log.ensure_object(Object(id="user:ann", type="user"))
    log.add_event(
        Event(
            id="e1",
            activity="edit",
            time=_T0,
            relationships=[
                Relationship(object_id="workbook:a", qualifier="target"),
                Relationship(object_id="user:ann", qualifier="performed_by"),
            ],
        )
    )

    rows = flatten(log, case_type="workbook", resource_type="user")
    assert rows[0].resource == "user:ann"
