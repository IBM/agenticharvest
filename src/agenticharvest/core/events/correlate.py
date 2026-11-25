#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Join events from many systems onto the business objects they share.

Each domain's mapper relates a call to the *artifact* it touched: a workbook, a
database, a web app. A business process crosses artifacts — an order is read
from a database, priced in a workbook, and shipped through a web app — so to
mine it end to end every event must also relate to the *business object* it
was about. That object is the case.

This module does that without any domain knowing about it:

- :class:`CompositeMapper` routes each tool call to the mapper of the domain
  that owns the tool, so one registry and one log span every system.
- :class:`Correlator` wraps any mapper. Declarative :class:`KeyRule` s say where
  a business key appears in a call — a parameter, a path into the result, or a
  regular expression over either — and every value found becomes an object of
  the rule's type (``order:O-17``), related to the event with a qualifier.
- :func:`suggest_keys` reads an existing log and proposes correlation keys from
  values that leave one system and enter another, for when nobody knows the
  join key yet.

The result is a plain OCEL whose business objects any miner can use as the case
(``flatten(log, case_type="order")``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from agenticharvest.core.events.mapping import EventMapping, GenericOcelMapper, OcelMapper
from agenticharvest.core.events.model import Object, Relationship
from agenticharvest.core.tools.result import ToolResult
from agenticharvest.core.tools.tap import TapRecord

# ======================================================================
# Routing calls to their domain's mapper
# ======================================================================


class CompositeMapper:
    """Map each call with the mapper of the domain that owns the tool.

    Args:
        mappers: Domain name -> that domain's mapper.
        tool_domains: Tool name -> owning domain name, e.g. from
            ``{spec.name: spec.domain for spec in registry.list_specs()}``.
        fallback: The mapper for tools in no listed domain. Defaults to the
            generic mapper.
    """

    def __init__(
        self,
        mappers: Mapping[str, OcelMapper],
        tool_domains: Mapping[str, str],
        fallback: OcelMapper | None = None,
    ) -> None:
        self._mappers = dict(mappers)
        self._domains = dict(tool_domains)
        self._fallback = fallback or GenericOcelMapper()

    def map(self, tool: str, params: dict, result: ToolResult) -> EventMapping:
        """Delegate to the owning domain's mapper, or the fallback."""
        mapper = self._mappers.get(self._domains.get(tool, ""), self._fallback)
        mapping = mapper.map(tool, params, result)
        domain = self._domains.get(tool)
        if domain:
            mapping.attributes.setdefault("domain", domain)
        return mapping


# ======================================================================
# Business keys
# ======================================================================


class KeyRule(BaseModel):
    """Where one business key appears in a tool call.

    A rule applies to a call when its ``tools`` (and ``when``) match. It then
    collects values from ``param`` and from each path in ``result``; with a
    ``pattern``, each collected text is searched and every match (group 1 if the
    pattern has a group) is a value. With only a ``pattern``, the call's whole
    parameters and output are searched.

    Paths address into nested JSON with dots; ``*`` fans out over a list or the
    values of an object: ``body.data.*.invoice_id``, ``rows.*.0``.

    Attributes:
        object_type: The business object type, e.g. ``order``.
        tools: The tools the rule applies to. Empty means every tool.
        when: Extra parameter conditions, e.g. ``{"sheet": "Orders"}``.
        param: A parameter path holding the key.
        result: Result paths holding the key.
        pattern: A regular expression a key matches.
        qualifier: The relationship's role. Defaults to ``"reads"`` for a read
            call and ``"writes"`` for a ``confirm=true`` call.
        transform: How to normalize a value: ``""`` (as is), ``"upper"``,
            ``"lower"``, or ``"strip"``.
    """

    object_type: str
    tools: list[str] = Field(default_factory=list)
    when: dict[str, Any] = Field(default_factory=dict)
    param: str = ""
    result: list[str] = Field(default_factory=list)
    pattern: str = ""
    qualifier: str = ""
    transform: str = ""

    def applies(self, tool: str, params: dict) -> bool:
        """Whether this rule looks at a call to ``tool`` with ``params``."""
        if self.tools and tool not in self.tools:
            return False
        return all(params.get(key) == value for key, value in self.when.items())

    def values(self, params: dict, output: Any) -> list[str]:
        """The normalized key values this rule finds in one call."""
        found: list[Any] = []
        if self.param:
            found += list(walk(params, self.param))
        for path in self.result:
            found += list(walk(output, path))
        if self.pattern and not (self.param or self.result):
            found = [params, output]
        texts = [_text(v) for v in found if v not in (None, "", [], {})]
        if self.pattern:
            regex = re.compile(self.pattern)
            matches: list[str] = []
            for text in texts:
                for match in regex.finditer(text):
                    matches.append(match.group(1) if regex.groups else match.group(0))
            texts = matches
        return list(dict.fromkeys(_normalize(t, self.transform) for t in texts if t))


def object_id(object_type: str, key: str) -> str:
    """The OCEL object id of business object ``key`` of ``object_type``."""
    return f"{object_type}:{key}"


class ValueRule(BaseModel):
    """A value to keep on the event, for finding data carried between systems.

    Values a call read land in ``attributes["read_values"][field]`` and values
    it wrote in ``attributes["written_values"][field]``, as lists.
    :func:`agenticharvest.mining.analysis.retyped_values` matches the two.

    Attributes:
        field: The name to record the values under.
        direction: ``read`` or ``write``.
        tools: The tools the rule applies to. Empty means every tool.
        when: Extra parameter conditions.
        param: A parameter path holding the values.
        result: Result paths holding the values.
    """

    field: str
    direction: str
    tools: list[str] = Field(default_factory=list)
    when: dict[str, Any] = Field(default_factory=dict)
    param: str = ""
    result: list[str] = Field(default_factory=list)

    def applies(self, tool: str, params: dict) -> bool:
        """Whether this rule looks at a call to ``tool`` with ``params``."""
        if self.tools and tool not in self.tools:
            return False
        return all(params.get(key) == value for key, value in self.when.items())


#: The most values kept per field on one event.
MAX_VALUES = 200


class Correlator:
    """A mapper that adds business objects to another mapper's events.

    Args:
        inner: The mapper that knows the artifacts (often a
            :class:`CompositeMapper`).
        rules: Where business keys appear.
        links: Object-to-object links to record when two keys appear in one
            call, as ``(from_type, qualifier, to_type)``: with
            ``("payment", "settles", "invoice")`` a call that names both a
            payment and an invoice links them.
        inherit: Artifact object types (``workbook``) whose calls carry the
            business objects last written into them. A macro run or a cell
            write that names no key is still *about* the orders the workbook
            holds, so it joins those orders' traces. Keys found in the call
            itself always win.
        values: Values to record on events, for :func:`retyped_values`.
    """

    def __init__(
        self,
        inner: OcelMapper,
        rules: Iterable[KeyRule],
        links: Iterable[tuple[str, str, str]] = (),
        *,
        inherit: Iterable[str] = (),
        values: Iterable[ValueRule] = (),
    ) -> None:
        self._inner = inner
        self._rules = list(rules)
        self._links = list(links)
        self._inherit = set(inherit)
        self._values = list(values)
        # Artifact object id -> the business objects last written into it.
        self._held: dict[str, list[tuple[str, str]]] = {}

    def map(self, tool: str, params: dict, result: ToolResult) -> EventMapping:
        """Map with the inner mapper, then relate every business key found."""
        mapping = self._inner.map(tool, params, result)
        output = result.output if result.ok else None
        writes = params.get("confirm") is True
        default = "writes" if writes else "reads"
        seen: dict[str, list[str]] = {}
        found: list[tuple[str, str]] = []
        for rule in self._rules:
            if not rule.applies(tool, params):
                continue
            for key in rule.values(params, output):
                oid = object_id(rule.object_type, key)
                if oid in {o.id for o in mapping.objects}:
                    continue
                found.append((oid, rule.object_type))
                mapping.objects.append(Object(id=oid, type=rule.object_type))
                mapping.relationships.append(
                    Relationship(object_id=oid, qualifier=rule.qualifier or default)
                )
                seen.setdefault(rule.object_type, []).append(oid)
        self._carry(mapping, found, writes)
        self._record_values(mapping, tool, params, output)
        for source_type, qualifier, target_type in self._links:
            for source in seen.get(source_type, []):
                obj = next(o for o in mapping.objects if o.id == source)
                for target in seen.get(target_type, []):
                    if all(r.object_id != target for r in obj.relationships):
                        obj.relationships.append(
                            Relationship(object_id=target, qualifier=qualifier)
                        )
        return mapping

    def _carry(
        self, mapping: EventMapping, found: list[tuple[str, str]], writes: bool
    ) -> None:
        """Remember what a write put into an artifact; lend it to keyless calls."""
        artifacts = [o.id for o in mapping.objects if o.type in self._inherit]
        if not artifacts:
            return
        if found:
            if writes:
                for artifact in artifacts:
                    held = self._held.setdefault(artifact, [])
                    held.extend(item for item in found if item not in held)
            return
        for artifact in artifacts:
            for oid, object_type in self._held.get(artifact, []):
                if oid in {o.id for o in mapping.objects}:
                    continue
                mapping.objects.append(Object(id=oid, type=object_type))
                mapping.relationships.append(Relationship(object_id=oid, qualifier="context"))

    def release(self, artifact_id: str) -> None:
        """Forget the business objects held by ``artifact_id`` (e.g. a cleared sheet)."""
        self._held.pop(artifact_id, None)

    def _record_values(
        self, mapping: EventMapping, tool: str, params: dict, output: Any
    ) -> None:
        for rule in self._values:
            if not rule.applies(tool, params):
                continue
            found: list[Any] = list(walk(params, rule.param)) if rule.param else []
            for path in rule.result:
                found += list(walk(output, path))
            kept = [v for v in found if isinstance(v, (str, int, float)) and v != ""]
            if not kept:
                continue
            slot = "written_values" if rule.direction == "write" else "read_values"
            bucket = mapping.attributes.setdefault(slot, {}).setdefault(rule.field, [])
            bucket.extend(kept[: max(MAX_VALUES - len(bucket), 0)])


# ======================================================================
# Suggesting keys from an existing log
# ======================================================================


class KeySuggestion(BaseModel):
    """A value shape that flows between systems: a candidate business key.

    Attributes:
        pattern: A regular expression matching the values seen.
        examples: Some of the values.
        systems: The artifacts (object ids) the values were seen in.
        events: How many events carried such a value.
    """

    pattern: str
    examples: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    events: int = 0


_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9-_]+\b|\b[A-Z]{2,}\d{2,}\b")


def suggest_keys(
    records: Iterable[TapRecord],
    mapper: OcelMapper,
    *,
    artifact_types: Iterable[str],
    min_systems: int = 2,
) -> list[KeySuggestion]:
    """Propose correlation keys: value shapes seen in more than one system.

    Business identifiers have a recognizable shape (``O-1042``, ``EMP_7``,
    ``REQ2026``) and, unlike a row number, they survive being copied from one
    system into another. This scans every recorded call's parameters and output
    for identifier-like tokens, groups them by shape, and keeps shapes that
    appear against at least ``min_systems`` different artifacts.

    Args:
        records: Recorded calls, e.g. an ``InMemoryTap``'s ``records`` kept
            alongside the ``OcelTap`` while observing.
        mapper: Decides which artifact each call touched (the same mapper the
            log was recorded with).
        artifact_types: The object types that are systems (``workbook``,
            ``database``, ``webapp``).
        min_systems: How many systems a shape must appear in.

    Returns:
        Suggestions, the most widely shared first.
    """
    systems_by_type = set(artifact_types)
    shapes: dict[str, KeySuggestion] = {}
    for record in records:
        mapping = mapper.map(record.tool, record.params, record.result)
        systems = [o.id for o in mapping.objects if o.type in systems_by_type]
        if not systems:
            continue
        texts = [json.dumps(record.params, default=str)]
        if record.result.ok:
            texts.append(json.dumps(record.result.output, default=str))
        tokens = {t for text in texts for t in _TOKEN.findall(text)}
        for shape, token in {(_shape(t), t) for t in tokens}:
            entry = shapes.setdefault(shape, KeySuggestion(pattern=shape))
            if token not in entry.examples and len(entry.examples) < 5:
                entry.examples.append(token)
            for system in systems:
                if system not in entry.systems:
                    entry.systems.append(system)
            entry.events += 1
    keep = [s for s in shapes.values() if len(s.systems) >= min_systems]
    return sorted(keep, key=lambda s: (-len(s.systems), -s.events, s.pattern))


def _shape(token: str) -> str:
    """A regular expression for ``token``'s shape: letters stay, digits generalize."""
    out = re.escape(re.sub(r"\d+", "\0", token))
    return r"\b" + out.replace("\x00", r"\d+") + r"\b"


# ======================================================================
# Paths into JSON
# ======================================================================


def walk(node: Any, path: str) -> Iterable[Any]:
    """Yield every value at ``path`` in ``node`` (dots; ``*`` fans out)."""
    parts = [p for p in path.split(".") if p]
    current: list[Any] = [node]
    for part in parts:
        following: list[Any] = []
        for value in current:
            if part == "*":
                if isinstance(value, list):
                    following.extend(value)
                elif isinstance(value, dict):
                    following.extend(value.values())
            elif isinstance(value, dict) and part in value:
                following.append(value[part])
            elif isinstance(value, list) and part.lstrip("-").isdigit():
                index = int(part)
                if -len(value) <= index < len(value):
                    following.append(value[index])
        current = following
    for value in current:
        if isinstance(value, list):
            yield from value
        else:
            yield value


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return value if isinstance(value, str) else str(value)


def _normalize(text: str, transform: str) -> str:
    text = text.strip()
    if transform == "upper":
        return text.upper()
    if transform == "lower":
        return text.lower()
    return text


__all__ = [
    "MAX_VALUES",
    "CompositeMapper",
    "Correlator",
    "KeyRule",
    "KeySuggestion",
    "ValueRule",
    "object_id",
    "suggest_keys",
    "walk",
]
