#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the generic OCEL model, mapper, and tap."""

from __future__ import annotations

from datetime import datetime, timezone

from agenticharvest.core.events import (
    Event,
    GenericOcelMapper,
    Object,
    ObjectAttribute,
    OcelLog,
    OcelTap,
    Relationship,
)
from agenticharvest.core.tools import ToolRegistry, ToolResult, ToolSpec


def _spec(name: str, effect: str = "read") -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool", effect=effect, domain="test")


# ----------------------------------------------------------------------
# The model
# ----------------------------------------------------------------------


def test_ensure_object_deduplicates_by_id() -> None:
    log = OcelLog()
    first = log.ensure_object(Object(id="o1", type="artifact"))
    second = log.ensure_object(Object(id="o1", type="artifact"))

    assert first is second
    assert len(log.objects) == 1


def test_ensure_object_first_registration_wins() -> None:
    log = OcelLog()
    log.ensure_object(
        Object(id="o1", type="artifact", attributes=[ObjectAttribute(name="rows", value=3)])
    )
    # A later bare reference must not wipe the attributes captured on first sight.
    log.ensure_object(Object(id="o1", type="artifact"))

    assert log.object("o1").attributes[0].value == 3


def test_type_listings_are_first_seen_order() -> None:
    log = OcelLog()
    log.ensure_object(Object(id="w", type="workbook"))
    log.ensure_object(Object(id="s", type="sheet"))
    log.ensure_object(Object(id="s2", type="sheet"))
    log.add_event(Event(id="e1", activity="list_sheets"))
    log.add_event(Event(id="e2", activity="get_data_flow"))

    assert log.object_types() == ["workbook", "sheet"]
    assert log.event_types() == ["list_sheets", "get_data_flow"]


def test_to_ocel2_shape_is_standard() -> None:
    log = OcelLog()
    log.ensure_object(Object(id="workbook:book.xlsm", type="workbook"))
    log.add_event(
        Event(
            id="e1",
            activity="get_data_flow",
            time=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            attributes={"ok": True},
            relationships=[
                Relationship(object_id="workbook:book.xlsm", qualifier="target")
            ],
        )
    )

    doc = log.to_ocel2()

    assert set(doc) == {"objectTypes", "eventTypes", "events", "objects"}
    event = doc["events"][0]
    assert event["type"] == "get_data_flow"  # activity serializes as OCEL "type"
    assert event["time"] == "2026-01-02T03:04:05Z"
    assert event["attributes"] == [{"name": "ok", "value": True}]
    assert event["relationships"] == [
        {"objectId": "workbook:book.xlsm", "qualifier": "target"}
    ]
    assert doc["eventTypes"] == [
        {"name": "get_data_flow", "attributes": [{"name": "ok", "type": "boolean"}]}
    ]


def test_object_attribute_type_inference() -> None:
    log = OcelLog()
    log.ensure_object(
        Object(
            id="o1",
            type="metric",
            attributes=[
                ObjectAttribute(name="count", value=3),
                ObjectAttribute(name="ratio", value=1.5),
                ObjectAttribute(name="label", value="x"),
            ],
        )
    )

    declared = {a["name"]: a["type"] for a in log.to_ocel2()["objectTypes"][0]["attributes"]}
    assert declared == {"count": "integer", "ratio": "float", "label": "string"}


# ----------------------------------------------------------------------
# The generic mapper
# ----------------------------------------------------------------------


def test_generic_mapper_attaches_artifact_from_path() -> None:
    mapping = GenericOcelMapper().map(
        "list_sheets", {"path": "book.xlsm"}, ToolResult.success([])
    )

    assert mapping.activity == "list_sheets"
    assert mapping.attributes == {"ok": True}
    assert [o.type for o in mapping.objects] == ["artifact"]
    assert mapping.objects[0].id == "artifact:book.xlsm"
    assert mapping.relationships[0].qualifier == "target"


def test_generic_mapper_records_error() -> None:
    mapping = GenericOcelMapper().map(
        "list_sheets", {"path": "book.xlsm"}, ToolResult.failure("boom")
    )

    assert mapping.attributes == {"ok": False, "error": "boom"}


def test_generic_mapper_without_path_has_no_objects() -> None:
    mapping = GenericOcelMapper().map("ping", {}, ToolResult.success(None))

    assert mapping.objects == []
    assert mapping.relationships == []


# ----------------------------------------------------------------------
# The tap
# ----------------------------------------------------------------------


def test_ocel_tap_records_one_event_per_call() -> None:
    tap = OcelTap()
    registry = ToolRegistry(taps=[tap])
    registry.register(_spec("echo"), lambda p: p.get("value"))

    registry.call("echo", {"path": "a.txt", "value": 1})
    registry.call("echo", {"path": "b.txt", "value": 2})

    assert [e.id for e in tap.log.events] == ["e1", "e2"]
    assert [e.activity for e in tap.log.events] == ["echo", "echo"]
    assert {o.id for o in tap.log.objects} == {"artifact:a.txt", "artifact:b.txt"}


def test_ocel_tap_uses_injected_clock() -> None:
    fixed = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    tap = OcelTap(clock=lambda: fixed)
    registry = ToolRegistry(taps=[tap])
    registry.register(_spec("echo"), lambda p: None)

    registry.call("echo", {"path": "a.txt"})

    assert tap.log.events[0].time == fixed


def test_ocel_tap_never_raises_on_mapper_failure() -> None:
    class Boom(GenericOcelMapper):
        def map(self, tool, params, result):  # type: ignore[override]
            raise RuntimeError("mapper bug")

    tap = OcelTap(mapper=Boom())
    registry = ToolRegistry(taps=[tap])
    registry.register(_spec("echo"), lambda p: None)

    # The call must still succeed even though the tap's mapper is broken.
    result = registry.call("echo", {"path": "a.txt"})
    assert result.ok is True
    assert tap.log.events == []
