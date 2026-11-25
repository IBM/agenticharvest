#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Importing a system's own history: records -> events, by declarative mapping."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agenticharvest.core.events import ObjectRef, OcelLog, RecordMapping, import_records
from agenticharvest.core.events.importers import read_csv, read_jsonl
from agenticharvest.mining import traces

# Shaped like a Directus activity feed: nested revisions carry the business key.
ACTIVITY = [
    {"action": "create", "collection": "tasks", "timestamp": "2026-10-05T09:00:00.000Z",
     "revisions": [{"data": {"employee_ref": "ada@example.com"}}]},
    {"action": "update", "collection": "tasks", "timestamp": "2026-10-05T10:30:00.000Z",
     "revisions": [{"data": {"employee_ref": "ada@example.com"}}]},
    {"action": "create", "collection": "other", "timestamp": "2026-10-05T11:00:00.000Z",
     "revisions": []},
]

MAPPING = RecordMapping(
    time="timestamp",
    activity="{action}:{collection}",
    objects=[ObjectRef(type="system", value="tracker", constant=True),
             ObjectRef(type="person", path="revisions.*.data.employee_ref")],
    include={"collection": "tasks"},
    source="tracker activity",
)


def test_records_become_events_on_the_objects_they_name() -> None:
    log = OcelLog()
    assert import_records(log, ACTIVITY, MAPPING, id_prefix="t") == 2
    assert [e.activity for e in log.events] == ["create:tasks", "update:tasks"]
    assert [e.id for e in log.events] == ["t1", "t2"]
    assert log.events[1].time == datetime(2026, 10, 5, 10, 30, tzinfo=timezone.utc)
    assert {e.attributes["source"] for e in log.events} == {"tracker activity"}
    assert traces(log, case_type="person") == {
        "person:ada@example.com": ["create:tasks", "update:tasks"]
    }


def test_two_imports_append_without_colliding() -> None:
    log = OcelLog()
    import_records(log, ACTIVITY, MAPPING, id_prefix="a")
    import_records(log, ACTIVITY, MAPPING, id_prefix="b")
    assert len({e.id for e in log.events}) == 4


def test_epoch_times_patterns_and_lowercase_activities() -> None:
    # Shaped like Keycloak admin events: epoch milliseconds, an id inside a path.
    records = [{"time": 1791190800000, "operationType": "CREATE", "resourceType": "USER",
                "resourcePath": "users/8a1e-42"}]
    mapping = RecordMapping(
        time="time", time_format="epoch-ms",
        activity="{operationType}:{resourceType}", lowercase_activity=True,
        objects=[ObjectRef(type="account", path="resourcePath", pattern=r"users/(.+)")],
    )
    log = OcelLog()
    import_records(log, records, mapping)
    [event] = log.events
    assert event.activity == "create:user"
    assert event.time == datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
    assert log.objects[0].id == "account:8a1e-42"


def test_unparseable_or_incomplete_records_are_skipped() -> None:
    records = [{"t": "not a time", "a": "x"}, {"t": "2026-10-05 09:00:00", "a": ""},
               {"t": "05/10/2026 09:00", "a": "ok"}]
    log = OcelLog()
    added = import_records(log, records, RecordMapping(time="t", activity="{a}"))
    assert added == 0
    added = import_records(
        log, records[2:], RecordMapping(time="t", time_format="%d/%m/%Y %H:%M", activity="{a}"))
    assert added == 1 and log.events[0].time.tzinfo is not None


def test_attributes_prefixes_and_exclude() -> None:
    records = [{"at": "2026-10-05T09:00:00Z", "kind": "change", "row": 7, "by": "u1"},
               {"at": "2026-10-05T09:05:00Z", "kind": "login", "row": 8, "by": "u2"}]
    mapping = RecordMapping(
        time="at", activity="{kind}", exclude={"kind": "login"},
        objects=[ObjectRef(type="row", path="row", prefix="portal:")],
        attributes={"user": "by"},
    )
    log = OcelLog()
    import_records(log, records, mapping)
    [event] = log.events
    assert event.attributes["user"] == "u1"
    assert log.objects[0].id == "row:portal:7"


def test_csv_and_jsonl_readers(tmp_path: Path) -> None:
    csv_file = tmp_path / "log.csv"
    csv_file.write_text("when,what,who\n2026-10-05T09:00:00Z,approve,ada\n", encoding="utf-8")
    jsonl_file = tmp_path / "log.jsonl"
    jsonl_file.write_text(json.dumps({"when": "2026-10-05T09:01:00Z", "what": "ship",
                                      "who": "ada"}) + "\n\n", encoding="utf-8")
    mapping = RecordMapping(time="when", activity="{what}",
                            objects=[ObjectRef(type="person", path="who")])
    log = OcelLog()
    import_records(log, read_csv(csv_file), mapping, id_prefix="c")
    import_records(log, read_jsonl(jsonl_file), mapping, id_prefix="j")
    assert traces(log, case_type="person") == {"person:ada": ["approve", "ship"]}
