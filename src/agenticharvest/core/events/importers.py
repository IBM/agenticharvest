#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Import a system's own history into the event log.

Tapping records work done *through* AgenticHarvest's tools. Most systems already keep
a history of the work done in them — an audit table, an activity feed, an access
log — and mining that history needs no one to redo the work. An importer turns
those records into OCEL events on the same model the taps write, so imported
and tapped events mine together.

A :class:`RecordMapping` says, declaratively, how one record becomes one event:
which field holds the time and how to parse it, how the activity is named, and
which fields name the objects the event relates to. Paths use the same dotted,
``*``-fanning syntax as the correlator (:func:`~agenticharvest.core.events.correlate.walk`),
so nested JSON audit records map as easily as flat CSV rows.

    mapping = RecordMapping(
        time="timestamp",
        activity="{action}:{collection}",
        objects=[ObjectRef(type="webapp", value="portal", constant=True),
                 ObjectRef(type="invoice", path="revisions.*.data.invoice_id")],
    )
    import_records(log, records, mapping)

Records come from anywhere: :func:`read_csv` and :func:`read_jsonl` read files,
and a caller passes any iterable of dicts — the JSON a live audit endpoint
returned, rows from a database query.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agenticharvest.core.events.correlate import walk
from agenticharvest.core.events.model import Event, Object, OcelLog, Relationship

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class ObjectRef(BaseModel):
    """Which object a record's event relates to.

    Attributes:
        type: The object type.
        path: The record path holding the object's key (``*`` fans out, so one
            record can relate to many objects).
        value: A constant key, used instead of ``path`` when ``constant``.
        constant: Use ``value`` as the key for every record.
        prefix: Prepended to the key, to namespace ids that are only unique
            within one system (``"portal:"`` for a row id).
        qualifier: The relationship's role.
        pattern: Keep only keys matching this regular expression (group 1 when
            it has one), e.g. to pull an id out of ``users/8a1e...``.
    """

    type: str
    path: str = ""
    value: str = ""
    constant: bool = False
    prefix: str = ""
    qualifier: str = "target"
    pattern: str = ""

    def keys(self, record: dict[str, Any]) -> list[str]:
        """The object keys this reference finds in ``record``."""
        if self.constant:
            raw = [self.value]
        else:
            raw = [_text(v) for v in walk(record, self.path) if v not in (None, "", False)]
        if self.pattern:
            regex = re.compile(self.pattern)
            matched = []
            for text in raw:
                match = regex.search(text)
                if match:
                    matched.append(match.group(1) if regex.groups else match.group(0))
            raw = matched
        return list(dict.fromkeys(f"{self.prefix}{k}" for k in raw if k))


class RecordMapping(BaseModel):
    """How one source record becomes one event.

    Attributes:
        time: The record path holding the event's time.
        time_format: How to parse it: ``iso`` (default), ``epoch-ms``,
            ``epoch-s``, or a ``strptime`` format. A time without a zone is UTC.
        activity: A template naming the activity, with ``{path}`` placeholders,
            e.g. ``"{operationType}:{resourceType}"``.
        lowercase_activity: Lower-case the rendered activity.
        objects: The objects the event relates to.
        attributes: Event attribute name -> record path, copied onto the event.
        include: Keep only records whose path equals the value.
        exclude: Drop records whose path equals the value.
        source: Recorded on every event as ``attributes["source"]``, so imported
            and tapped events stay distinguishable.
    """

    time: str
    time_format: str = "iso"
    activity: str
    lowercase_activity: bool = False
    objects: list[ObjectRef] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    include: dict[str, Any] = Field(default_factory=dict)
    exclude: dict[str, Any] = Field(default_factory=dict)
    source: str = "import"


def import_records(
    log: OcelLog,
    records: Iterable[dict[str, Any]],
    mapping: RecordMapping,
    *,
    id_prefix: str = "i",
) -> int:
    """Append one event per matching record to ``log``.

    A record whose time cannot be parsed, or whose activity renders empty, is
    skipped rather than guessed at.

    Args:
        log: The log to extend.
        records: The source records.
        mapping: How a record becomes an event.
        id_prefix: Prefix for the generated event ids. Choose one per source so
            ids from different imports do not collide.

    Returns:
        How many events were added.
    """
    added = 0
    start = len(log.events)
    for record in records:
        if not _selected(record, mapping):
            continue
        moment = _parse_time(_first(record, mapping.time), mapping.time_format)
        activity = _render(mapping.activity, record)
        if moment is None or not activity:
            continue
        if mapping.lowercase_activity:
            activity = activity.lower()
        relationships: list[Relationship] = []
        for ref in mapping.objects:
            for key in ref.keys(record):
                oid = f"{ref.type}:{key}"
                log.ensure_object(Object(id=oid, type=ref.type))
                relationships.append(Relationship(object_id=oid, qualifier=ref.qualifier))
        attributes: dict[str, Any] = {"source": mapping.source}
        for name, path in mapping.attributes.items():
            value = _first(record, path)
            if value is not None:
                attributes[name] = value
        log.add_event(
            Event(
                id=f"{id_prefix}{start + added + 1}",
                activity=activity,
                time=moment,
                attributes=attributes,
                relationships=relationships,
            )
        )
        added += 1
    return added


def read_csv(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield each row of a CSV file (with a header row) as a dict."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def read_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield each object of a JSON Lines file."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _selected(record: dict[str, Any], mapping: RecordMapping) -> bool:
    if any(_first(record, path) != value for path, value in mapping.include.items()):
        return False
    return not any(_first(record, path) == value for path, value in mapping.exclude.items())


def _first(record: dict[str, Any], path: str) -> Any:
    return next(iter(walk(record, path)), None)


def _render(template: str, record: dict[str, Any]) -> str:
    missing = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal missing
        value = _first(record, match.group(1))
        if value in (None, ""):
            missing = True
            return ""
        return _text(value)

    rendered = _PLACEHOLDER.sub(substitute, template)
    return "" if missing else rendered


def _parse_time(value: Any, fmt: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if fmt in ("epoch-ms", "epoch-s"):
            seconds = float(value) / (1000 if fmt == "epoch-ms" else 1)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        text = str(value)
        if fmt == "iso":
            moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
        else:
            # A zone-less format is read as UTC just below.
            moment = datetime.strptime(text, fmt)
    except (TypeError, ValueError, OverflowError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return value if isinstance(value, str) else str(value)


__all__ = ["ObjectRef", "RecordMapping", "import_records", "read_csv", "read_jsonl"]
