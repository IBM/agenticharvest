#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The observability seam.

Every tool call passes through the registry, and the registry feeds each of its
taps one record per call. A tap is where a call becomes a span (tracing) and an
event (OCEL) once ``core.tracing`` and ``core.events`` land — this module owns
only the seam, not those subsystems.

``InMemoryTap`` is the trivial reference tap: it appends each call to a list. It
is what tests use to assert that a tool actually ran, and it doubles as a worked
example of the one-method ``Tap`` protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from agenticharvest.core.tools.result import ToolResult


class TapRecord(BaseModel):
    """One observed tool call: what was called, with what, and what came back.

    Attributes:
        tool: The tool's name.
        params: The parameters the tool was called with.
        result: The result the tool returned.
    """

    tool: str
    params: dict
    result: ToolResult


@runtime_checkable
class Tap(Protocol):
    """A hook the registry calls once per tool invocation.

    Implementations turn a call into whatever observation they own (a span, an
    event log entry, a metric). The registry never inspects the return value, so
    a tap must not raise — it observes, it does not gate.
    """

    def record(self, tool: str, params: dict, result: ToolResult) -> None:
        """Observe one completed tool call."""
        ...


class InMemoryTap:
    """A tap that keeps every record in a list. For tests and local inspection."""

    def __init__(self) -> None:
        self.records: list[TapRecord] = []

    def record(self, tool: str, params: dict, result: ToolResult) -> None:
        """Append the call to the in-memory record list."""
        self.records.append(TapRecord(tool=tool, params=params, result=result))

    def calls_to(self, tool: str) -> list[TapRecord]:
        """Return the records for calls to ``tool`` (convenience for assertions)."""
        return [r for r in self.records if r.tool == tool]

    def last(self) -> TapRecord | None:
        """Return the most recent record, or ``None`` if nothing was recorded."""
        return self.records[-1] if self.records else None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"InMemoryTap({len(self.records)} records)"
