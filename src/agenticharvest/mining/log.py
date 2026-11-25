#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Flatten an object-centric log into the case-centric log a miner consumes.

Most process-mining engines — bupaR among them — are *case-centric*: every event
belongs to exactly one case, identified by a single case id. An OCEL is
*object-centric*: one event relates to many typed objects at once. To mine an
OCEL with such an engine we first pick one object *type* to serve as the case
notion and project each event onto the objects of that type it touched.

For the Excel domain the natural case is the ``workbook`` (one case per file, its
trace the sequence of tool calls against it). ``infer_case_type`` picks the type
that the most events relate to, so the flattening works for any domain without
being told which type is the case; a caller that knows better can pass one.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from agenticharvest.core.events.model import OcelLog


class ClassicEvent(BaseModel):
    """One row of a flattened, case-centric event log.

    Attributes:
        case_id: The object id serving as the case (e.g. a workbook's id).
        activity: The event's activity (the tool/event name).
        timestamp: When the event happened.
        resource: An optional resource that performed the activity, when a
            resource object type was chosen.
    """

    case_id: str
    activity: str
    timestamp: datetime
    resource: str | None = None


def infer_case_type(log: OcelLog) -> str:
    """Return the object type the most events relate to (the natural case).

    Ties break toward the first such type seen, so the result is deterministic.
    Returns ``""`` when the log has no objects to key on.
    """
    type_by_id = {obj.id: obj.type for obj in log.objects}
    counts: dict[str, int] = {}
    for event in log.events:
        # Ordered dedup, not a set: string hashing is randomized per process,
        # so set order would make the tie-break below vary from run to run.
        types = dict.fromkeys(
            type_by_id[rel.object_id]
            for rel in event.relationships
            if rel.object_id in type_by_id
        )
        for object_type in types:
            counts[object_type] = counts.get(object_type, 0) + 1
    if not counts:
        return ""
    # dict preserves first-seen insertion order, so max() with a plain key
    # returns the earliest type among any tied for the highest count.
    return max(counts, key=lambda t: counts[t])


def flatten(
    log: OcelLog,
    case_type: str | None = None,
    resource_type: str | None = None,
) -> list[ClassicEvent]:
    """Project an OCEL onto a case-centric event log.

    Each event is emitted once per related object of ``case_type`` (usually one),
    so a single event that touched two case objects becomes two rows — the same
    activity in two cases. Events touching no object of the case type are dropped:
    they cannot be placed on any case.

    Args:
        log: The object-centric log to flatten.
        case_type: The object type to treat as the case. Defaults to
            :func:`infer_case_type`.
        resource_type: An optional object type whose related object is recorded
            as the event's resource (the first one, when several are related).

    Returns:
        The flattened rows, in the order the events occurred.
    """
    chosen = case_type or infer_case_type(log)
    if not chosen:
        return []
    type_by_id = {obj.id: obj.type for obj in log.objects}

    rows: list[ClassicEvent] = []
    for event in log.events:
        case_ids = [
            rel.object_id
            for rel in event.relationships
            if type_by_id.get(rel.object_id) == chosen
        ]
        if not case_ids:
            continue
        resource = None
        if resource_type is not None:
            resource = next(
                (
                    rel.object_id
                    for rel in event.relationships
                    if type_by_id.get(rel.object_id) == resource_type
                ),
                None,
            )
        for case_id in case_ids:
            rows.append(
                ClassicEvent(
                    case_id=case_id,
                    activity=event.activity,
                    timestamp=event.time,
                    resource=resource,
                )
            )
    return rows


def collapse_repeats(log: OcelLog, case_type: str | None = None) -> OcelLog:
    """Return a copy of ``log`` with consecutive repeats on each case removed.

    Work over a collection repeats one step per item: reviewing four claims
    writes four decisions, reviewing two writes two. The *process* is the same —
    "record the decisions" — so folding a run of the same activity into its
    first event lets cases of any size share one variant. An event kept on any
    case is kept in the result.

    Args:
        log: The object-centric log.
        case_type: The object type to treat as the case. Defaults to
            :func:`infer_case_type`.
    """
    chosen = case_type or infer_case_type(log)
    type_by_id = {obj.id: obj.type for obj in log.objects}
    last_activity: dict[str, str] = {}
    keep: set[str] = set()
    for event in sorted(log.events, key=lambda e: e.time):
        for rel in event.relationships:
            if type_by_id.get(rel.object_id) != chosen:
                continue
            if last_activity.get(rel.object_id) != event.activity:
                keep.add(event.id)
            last_activity[rel.object_id] = event.activity
    return OcelLog(
        events=[e for e in log.events if e.id in keep],
        objects=list(log.objects),
    )


def traces(log: OcelLog, case_type: str | None = None) -> dict[str, list[str]]:
    """Return each case's trace: its activities in time order.

    Built on :func:`flatten`, so the case notion is chosen the same way. Events
    are ordered by timestamp within a case; events sharing a timestamp keep the
    order they were logged in.

    Args:
        log: The object-centric log.
        case_type: The object type to treat as the case. Defaults to
            :func:`infer_case_type`.

    Returns:
        Case id -> ordered activity list, cases in first-seen order.
    """
    by_case: dict[str, list[ClassicEvent]] = {}
    for row in flatten(log, case_type=case_type):
        by_case.setdefault(row.case_id, []).append(row)
    return {
        case_id: [r.activity for r in sorted(rows, key=lambda r: r.timestamp)]
        for case_id, rows in by_case.items()
    }
