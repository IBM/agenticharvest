#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Analyze a process that crosses systems: where it hands off, waits, and breaks rules.

Discovery (``miners``) says *what* the process is. These functions say where it
can be improved, over the same object-centric log, without knowing what any
system is:

- :func:`handoffs` counts the transitions where a case moves from one system to
  the next. Every handoff is a place someone re-enters or re-reads data.
- :func:`performance` gives each directly-follows transition its waiting time
  (median and mean), so the slow handoffs stand out.
- :func:`object_centric_dfg` builds one directly-follows graph per object type
  and the activities they share, instead of forcing every event onto a single
  case notion (which duplicates or drops events when objects relate 1:N).
- :func:`retyped_values` finds values written into one system that were read
  out of another earlier in the same case: data carried by hand.
- :func:`check_rules` evaluates ordering and timing rules ("A before B",
  "B within 48h of A") on every case and lists the violations.

A *system* is an object type that names an artifact (``workbook``, ``database``,
``webapp``); the caller says which types those are. The case is any object type
(``order``, ``employee``).
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable
from datetime import timedelta
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, Field

from agenticharvest.core.events.model import Event, OcelLog

# ======================================================================
# Case views
# ======================================================================


def case_events(log: OcelLog, case_type: str) -> dict[str, list[Event]]:
    """Each object of ``case_type`` and its events, in time order."""
    type_by_id = {o.id: o.type for o in log.objects}
    by_case: dict[str, list[Event]] = {}
    for event in log.events:
        for rel in event.relationships:
            if type_by_id.get(rel.object_id) == case_type:
                cases = by_case.setdefault(rel.object_id, [])
                if not cases or cases[-1] is not event:
                    cases.append(event)
    return {case: sorted(events, key=lambda e: e.time) for case, events in by_case.items()}


def system_of(event: Event, log: OcelLog, system_types: Iterable[str]) -> str:
    """The system an event happened in: its first related artifact object."""
    types = set(system_types)
    type_by_id = {o.id: o.type for o in log.objects}
    return next((r.object_id for r in event.relationships if type_by_id.get(r.object_id) in types), "")


# ======================================================================
# Handoffs
# ======================================================================


class Handoff(BaseModel):
    """Cases moving from one system to another.

    Attributes:
        source: The system the case left.
        target: The system it entered.
        count: How many times this happened, over all cases.
        cases: How many distinct cases did it.
    """

    source: str
    target: str
    count: int = 0
    cases: int = 0


def handoffs(log: OcelLog, case_type: str, system_types: Iterable[str]) -> list[Handoff]:
    """Count the system-to-system handoffs across every case, most frequent first."""
    types = list(system_types)
    type_by_id = {o.id: o.type for o in log.objects}
    counts: Counter[tuple[str, str]] = Counter()
    case_sets: dict[tuple[str, str], set[str]] = {}
    for case, events in case_events(log, case_type).items():
        systems = [
            next((r.object_id for r in e.relationships if type_by_id.get(r.object_id) in types), "")
            for e in events
        ]
        for source, target in pairwise(s for s in systems if s):
            if source != target:
                counts[(source, target)] += 1
                case_sets.setdefault((source, target), set()).add(case)
    return [
        Handoff(source=s, target=t, count=n, cases=len(case_sets[(s, t)]))
        for (s, t), n in counts.most_common()
    ]


# ======================================================================
# Waiting time
# ======================================================================


class EdgePerformance(BaseModel):
    """How long cases wait between two activities.

    Attributes:
        source: The earlier activity.
        target: The activity that directly followed it.
        count: How often the transition happened.
        median_seconds: The median wait.
        mean_seconds: The mean wait.
        max_seconds: The longest wait.
    """

    source: str
    target: str
    count: int
    median_seconds: float
    mean_seconds: float
    max_seconds: float


def performance(log: OcelLog, case_type: str) -> list[EdgePerformance]:
    """Waiting time per directly-follows transition, slowest median first."""
    waits: dict[tuple[str, str], list[float]] = {}
    for events in case_events(log, case_type).values():
        for before, after in pairwise(events):
            seconds = (after.time - before.time).total_seconds()
            waits.setdefault((before.activity, after.activity), []).append(seconds)
    edges = [
        EdgePerformance(
            source=source,
            target=target,
            count=len(values),
            median_seconds=round(statistics.median(values), 3),
            mean_seconds=round(statistics.fmean(values), 3),
            max_seconds=round(max(values), 3),
        )
        for (source, target), values in waits.items()
    ]
    return sorted(edges, key=lambda e: (-e.median_seconds, e.source, e.target))


# ======================================================================
# Object-centric directly-follows graph
# ======================================================================


class TypedEdge(BaseModel):
    """A directly-follows edge in one object type's view of the process."""

    object_type: str
    source: str
    target: str
    count: int


class ObjectCentricDfg(BaseModel):
    """One directly-follows graph per object type, joined by shared activities.

    Attributes:
        object_types: The types included, with how many objects each has.
        activities: Activity -> the object types its events relate to. An
            activity related to several types is where their lifecycles meet.
        edges: Every type's directly-follows edges.
        starts: Object type -> activity -> how many objects began with it.
        ends: Object type -> activity -> how many objects ended with it.
    """

    object_types: dict[str, int] = Field(default_factory=dict)
    activities: dict[str, list[str]] = Field(default_factory=dict)
    edges: list[TypedEdge] = Field(default_factory=list)
    starts: dict[str, dict[str, int]] = Field(default_factory=dict)
    ends: dict[str, dict[str, int]] = Field(default_factory=dict)

    def edges_of(self, object_type: str) -> list[TypedEdge]:
        """The edges in one object type's view."""
        return [e for e in self.edges if e.object_type == object_type]


def object_centric_dfg(log: OcelLog, object_types: Iterable[str]) -> ObjectCentricDfg:
    """Build the object-centric DFG over ``object_types``.

    Each object's events, in time order, contribute directly-follows edges to
    its type's graph. Unlike flattening onto one case type, an event related to
    two requisitions and one employee counts once per object, in the right
    view, and nothing is dropped for lacking the chosen case object.
    """
    dfg = ObjectCentricDfg()
    for object_type in object_types:
        per_object = case_events(log, object_type)
        if not per_object:
            continue
        dfg.object_types[object_type] = len(per_object)
        follows: Counter[tuple[str, str]] = Counter()
        starts: Counter[str] = Counter()
        ends: Counter[str] = Counter()
        for events in per_object.values():
            names = [e.activity for e in events]
            starts[names[0]] += 1
            ends[names[-1]] += 1
            follows.update(pairwise(names))
            for name in names:
                types = dfg.activities.setdefault(name, [])
                if object_type not in types:
                    types.append(object_type)
        dfg.edges += [
            TypedEdge(object_type=object_type, source=s, target=t, count=n)
            for (s, t), n in follows.most_common()
        ]
        dfg.starts[object_type] = dict(starts.most_common())
        dfg.ends[object_type] = dict(ends.most_common())
    return dfg


# ======================================================================
# Values carried by hand
# ======================================================================


class RetypedValue(BaseModel):
    """A value read out of one system and later written into another.

    Attributes:
        field: The attribute name it was written under.
        source: The system it was read from.
        target: The system it was written into.
        source_activity: The read.
        target_activity: The write.
        cases: How many cases carried it this way.
    """

    field: str
    source: str
    target: str
    source_activity: str
    target_activity: str
    cases: int


def retyped_values(
    log: OcelLog,
    case_type: str,
    system_types: Iterable[str],
    *,
    read_values: str = "read_values",
    written_values: str = "written_values",
) -> list[RetypedValue]:
    """Find values written into one system after being read from another.

    Each event may carry what it read and what it wrote as attributes
    (``read_values`` and ``written_values`` by default), each mapping a field
    name to a value or a list of values — the shape
    :class:`~agenticharvest.core.events.correlate.ValueRule` records. A value written
    into system *B* that an earlier event in the same case read out of a
    different system *A* was carried across by hand — exactly the step an
    integration removes.

    Returns:
        Each (field, source, target) carry, most cases first.
    """
    types = list(system_types)
    carried: dict[tuple[str, str, str, str, str], set[str]] = {}
    for case, events in case_events(log, case_type).items():
        # Value -> every (system, activity) that read it, oldest first. The
        # carry is credited to the latest read from another system: a short
        # value (``18``) can match an unrelated earlier read too.
        seen: dict[str, list[tuple[str, str]]] = {}
        for event in events:
            system = system_of(event, log, types)
            for field, value in _pairs(event.attributes.get(written_values)):
                origins = [o for o in seen.get(_key(value), []) if o[0] != system]
                if origins:
                    source, activity = origins[-1]
                    entry = (field, source, system, activity, event.activity)
                    carried.setdefault(entry, set()).add(case)
            for _field, value in _pairs(event.attributes.get(read_values)):
                seen.setdefault(_key(value), []).append((system, event.activity))
    rows = [
        RetypedValue(field=f, source=s, target=t, source_activity=sa, target_activity=ta,
                     cases=len(cases))
        for (f, s, t, sa, ta), cases in carried.items()
    ]
    return sorted(rows, key=lambda r: (-r.cases, r.field))


def _pairs(values: Any) -> list[tuple[str, Any]]:
    """``(field, value)`` for every value recorded, flattening lists."""
    if not isinstance(values, dict):
        return []
    pairs: list[tuple[str, Any]] = []
    for field, value in values.items():
        for item in value if isinstance(value, list) else [value]:
            if item not in (None, ""):
                pairs.append((field, item))
    return pairs


def _key(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip().lower()


# ======================================================================
# Ordering and timing rules
# ======================================================================


class Rule(BaseModel):
    """An ordering or timing rule every case should satisfy.

    ``kind`` is one of:

    - ``precedes``: if ``then`` occurs, ``first`` occurred before it.
    - ``responds``: if ``first`` occurs, ``then`` occurs after it.
    - ``within``: ``then`` occurs no later than ``max_hours`` after ``first``.
    - ``exists``: ``first`` occurs at least once.
    - ``absent``: ``first`` never occurs.

    Activities match exactly, or by prefix when they end in ``*``
    (``send_operation:post_users*``).

    Attributes:
        name: A short label for reports.
        kind: The rule kind.
        first: The activity the rule starts from.
        then: The activity it constrains (unused by ``exists``/``absent``).
        max_hours: The limit for ``within``.
    """

    name: str
    kind: str
    first: str
    then: str = ""
    max_hours: float = 0.0


class Violation(BaseModel):
    """One case breaking one rule."""

    rule: str
    case: str
    detail: str


class RuleReport(BaseModel):
    """How every case did against the rules.

    Attributes:
        cases: How many cases were checked.
        violations: Every violation found.
        passed: Rule name -> how many cases satisfied it.
    """

    cases: int = 0
    violations: list[Violation] = Field(default_factory=list)
    passed: dict[str, int] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether no case broke any rule."""
        return not self.violations


def check_rules(log: OcelLog, case_type: str, rules: Iterable[Rule]) -> RuleReport:
    """Evaluate ``rules`` on every case of ``case_type``."""
    cases = case_events(log, case_type)
    report = RuleReport(cases=len(cases))
    for rule in rules:
        report.passed[rule.name] = 0
        for case, events in cases.items():
            problem = _evaluate(rule, events)
            if problem:
                report.violations.append(Violation(rule=rule.name, case=case, detail=problem))
            else:
                report.passed[rule.name] += 1
    return report


def _matches(pattern: str, activity: str) -> bool:
    return activity.startswith(pattern[:-1]) if pattern.endswith("*") else activity == pattern


def _evaluate(rule: Rule, events: list[Event]) -> str:
    firsts = [e for e in events if _matches(rule.first, e.activity)]
    thens = [e for e in events if rule.then and _matches(rule.then, e.activity)]
    if rule.kind == "exists":
        return "" if firsts else f"{rule.first} never happened"
    if rule.kind == "absent":
        return f"{rule.first} happened {len(firsts)} time(s)" if firsts else ""
    if rule.kind == "precedes":
        if thens and not any(f.time <= thens[0].time and f is not thens[0] for f in firsts):
            return f"{thens[0].activity} at {thens[0].time:%Y-%m-%d %H:%M} with no {rule.first} before it"
        return ""
    if rule.kind == "responds":
        if firsts and not any(t.time >= firsts[0].time and t is not firsts[0] for t in thens):
            return f"{firsts[0].activity} at {firsts[0].time:%Y-%m-%d %H:%M} never followed by {rule.then}"
        return ""
    if rule.kind == "within":
        if not firsts:
            return ""
        start = firsts[0].time
        limit = start + timedelta(hours=rule.max_hours)
        after = [t for t in thens if t.time >= start]
        if not after:
            return f"no {rule.then} after {rule.first}"
        if after[0].time > limit:
            hours = (after[0].time - start).total_seconds() / 3600
            return f"{rule.then} came {hours:.1f}h after {rule.first} (limit {rule.max_hours:g}h)"
        return ""
    raise ValueError(f"Unknown rule kind '{rule.kind}' in rule '{rule.name}'.")


__all__ = [
    "EdgePerformance",
    "Handoff",
    "ObjectCentricDfg",
    "RetypedValue",
    "Rule",
    "RuleReport",
    "TypedEdge",
    "Violation",
    "case_events",
    "check_rules",
    "handoffs",
    "object_centric_dfg",
    "performance",
    "retyped_values",
    "system_of",
]
