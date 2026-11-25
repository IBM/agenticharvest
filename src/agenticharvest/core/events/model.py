#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The generic OCEL data model.

An OCEL (Object-Centric Event Log) records *events* — things that happened — and
the *objects* those events touched, joined by qualified relationships. Unlike a
classic event log, which forces every event onto a single case id, an
object-centric log lets one event relate to many objects of many types (a tool
call touches a workbook, a sheet, and a macro at once). That is what lets mining
reason about how an artifact is actually used, rather than about one flat case.

This module is the domain-agnostic core: ``Event``, ``Object``, their qualified
relationships, and the ``OcelLog`` that holds them and serializes to the OCEL 2.0
JSON shape. It knows nothing about spreadsheets — a domain (see
``agenticharvest.domains.excel.ocel.ExcelOCEL``) decides which typed objects a given tool
call relates to; the model here just stores and serializes the result.

The models are Pydantic v2 so a log round-trips to JSON losslessly. ``to_ocel2``
emits the standard OCEL 2.0 document (``objectTypes`` / ``eventTypes`` /
``events`` / ``objects``) that conformant miners consume.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

# Object and event *types* are plain strings, not a closed enum — the core fixes
# none of them, and each domain contributes its own (a workbook's "sheet" and
# "macro", a database's "table" and "query"). This keeps the log open for new
# domains without a core change, exactly as the IR graph keeps node kinds open.
OBJ_ARTIFACT = "artifact"


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    """Format a datetime as an OCEL 2.0 ISO-8601 string (UTC, trailing ``Z``)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ocel_type(value: Any) -> str:
    """Map a Python attribute value to an OCEL 2.0 attribute type name."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, datetime):
        return "time"
    return "string"


class Relationship(BaseModel):
    """A qualified reference from one entity to an object.

    Used both for event-to-object relationships (which objects an event touched,
    and in what role) and object-to-object relationships (how objects relate).

    Attributes:
        object_id: The ``Object.id`` this relationship points at.
        qualifier: The role the object plays, e.g. ``"target"``, ``"reads"``,
            ``"writes"``, ``"calls"``. Free-form so each domain names its own
            roles; mining groups relationships by qualifier.
    """

    object_id: str
    qualifier: str = ""


class ObjectAttribute(BaseModel):
    """One attribute value on an object, optionally time-stamped.

    OCEL 2.0 object attributes may vary over time (a workbook's row count changes
    across edits), so each value carries an optional ``time``. A value with no
    ``time`` is treated as holding from the start of the log.

    Attributes:
        name: The attribute's name.
        value: Its value (any JSON-serializable scalar).
        time: When the value took effect, if it is time-varying.
    """

    name: str
    value: Any = None
    time: datetime | None = None


class Event(BaseModel):
    """One thing that happened, relating to zero or more objects.

    Attributes:
        id: Unique identifier within the log.
        activity: The event type — *what happened*, e.g. a tool name like
            ``get_data_flow``. Serialized as OCEL 2.0's ``type``. Mining groups
            events by activity to discover variants.
        time: When it happened (timezone-aware UTC).
        attributes: Static, event-scoped attributes (e.g. ``ok``, ``error``).
        relationships: The objects this event touched, each with a qualifier
            naming the object's role in the event (event-to-object, "E2O").
    """

    id: str
    activity: str
    time: datetime = Field(default_factory=_utcnow)
    attributes: dict[str, Any] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)


class Object(BaseModel):
    """A thing events happen to — a workbook, a sheet, a macro, a file.

    Attributes:
        id: Unique identifier within the log. A domain derives it
            deterministically from the object's natural key so the same object
            seen across many tool calls collapses to one entry.
        type: The object type, e.g. ``workbook`` or ``sheet``. Open string,
            contributed by the domain.
        attributes: Object-scoped attribute values (possibly time-varying).
        relationships: Links to other objects, each with a qualifier
            (object-to-object, "O2O"), e.g. a sheet ``"belongs_to"`` a workbook.
    """

    id: str
    type: str
    attributes: list[ObjectAttribute] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


class OcelLog(BaseModel):
    """An object-centric event log: events, and the objects they relate to.

    Objects are deduplicated by id — ``ensure_object`` registers an object the
    first time it is seen and returns the existing one afterwards, so many events
    can reference the same workbook without duplicating it.

    Attributes:
        events: Every recorded event, in the order they occurred.
        objects: Every distinct object referenced by an event.
    """

    events: list[Event] = Field(default_factory=list)
    objects: list[Object] = Field(default_factory=list)

    def add_event(self, event: Event) -> Event:
        """Append an event to the log and return it."""
        self.events.append(event)
        return event

    def ensure_object(self, obj: Object) -> Object:
        """Register ``obj`` if its id is new; return the object now in the log.

        The first registration wins: a later call with the same id does not
        overwrite the stored object, so a domain can attach an object's full
        attributes on first sight and reference it by bare id thereafter.
        """
        for existing in self.objects:
            if existing.id == obj.id:
                return existing
        self.objects.append(obj)
        return obj

    def object(self, object_id: str) -> Object | None:
        """Return the object with ``object_id``, or ``None`` if not present."""
        return next((o for o in self.objects if o.id == object_id), None)

    def object_types(self) -> list[str]:
        """Return the distinct object types present, in first-seen order."""
        return list(dict.fromkeys(o.type for o in self.objects))

    def event_types(self) -> list[str]:
        """Return the distinct event activities present, in first-seen order."""
        return list(dict.fromkeys(e.activity for e in self.events))

    # ------------------------------------------------------------------
    # OCEL 2.0 serialization
    # ------------------------------------------------------------------

    def to_ocel2(self) -> dict[str, Any]:
        """Return this log as an OCEL 2.0 JSON document (a plain dict).

        The shape follows the OCEL 2.0 standard: top-level ``objectTypes``,
        ``eventTypes``, ``events``, and ``objects``. Attribute *type* declarations
        are inferred from the values actually present.
        """
        return {
            "objectTypes": self._type_declarations(
                self.object_types(),
                lambda t: [
                    (a.name, a.value)
                    for o in self.objects
                    if o.type == t
                    for a in o.attributes
                ],
            ),
            "eventTypes": self._type_declarations(
                self.event_types(),
                lambda t: [
                    (name, value)
                    for e in self.events
                    if e.activity == t
                    for name, value in e.attributes.items()
                ],
            ),
            "events": [self._event_to_ocel2(e) for e in self.events],
            "objects": [self._object_to_ocel2(o) for o in self.objects],
        }

    @staticmethod
    def _type_declarations(
        type_names: Iterable[str],
        attrs_for: Callable[[str], list[tuple[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Build ``[{name, attributes: [{name, type}]}]`` for a set of types."""
        declarations = []
        for type_name in type_names:
            seen: dict[str, str] = {}
            for attr_name, value in attrs_for(type_name):
                seen.setdefault(attr_name, _ocel_type(value))
            declarations.append(
                {
                    "name": type_name,
                    "attributes": [
                        {"name": n, "type": t} for n, t in seen.items()
                    ],
                }
            )
        return declarations

    @staticmethod
    def _event_to_ocel2(event: Event) -> dict[str, Any]:
        """Render one event in OCEL 2.0 shape (``type`` is the activity)."""
        return {
            "id": event.id,
            "type": event.activity,
            "time": _iso(event.time),
            "attributes": [
                {"name": name, "value": value}
                for name, value in event.attributes.items()
            ],
            "relationships": [
                {"objectId": r.object_id, "qualifier": r.qualifier}
                for r in event.relationships
            ],
        }

    @staticmethod
    def _object_to_ocel2(obj: Object) -> dict[str, Any]:
        """Render one object in OCEL 2.0 shape (time-varying attributes)."""
        attributes = []
        for attr in obj.attributes:
            entry: dict[str, Any] = {"name": attr.name, "value": attr.value}
            if attr.time is not None:
                entry["time"] = _iso(attr.time)
            attributes.append(entry)
        return {
            "id": obj.id,
            "type": obj.type,
            "attributes": attributes,
            "relationships": [
                {"objectId": r.object_id, "qualifier": r.qualifier}
                for r in obj.relationships
            ],
        }
