#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The OCEL tap — a tool call becomes an event.

``OcelTap`` implements the ``core.tools.Tap`` protocol, so attaching one to a
registry turns every tool call into an OCEL event, live, at dispatch time. This
is the ``core.events`` consumer of the observability seam that ``InMemoryTap``
prototyped: the same one-method hook, now writing an object-centric log that
``agenticharvest.mining`` can consume.

The tap owns the mechanics every event needs regardless of domain — a unique id,
a timestamp, registering the objects the call touched — and delegates the
domain-specific question of *which* objects to an :class:`OcelMapper`. It never
raises: observation must not break the call it observes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from agenticharvest.core.events.mapping import GenericOcelMapper, OcelMapper
from agenticharvest.core.events.model import Event, OcelLog
from agenticharvest.core.tools.result import ToolResult


class OcelTap:
    """A tap that appends one OCEL event per tool call.

    Args:
        mapper: Decides which objects each call touches. Defaults to the
            generic mapper; a domain passes its own (e.g. ``ExcelOCEL``) to get
            typed objects.
        log: An existing log to append to. Defaults to a fresh one.
        clock: Returns the time to stamp on each event. Injectable so tests get
            deterministic timestamps.
        id_prefix: Prefix for generated event ids (``e1``, ``e2``, …).
    """

    def __init__(
        self,
        mapper: OcelMapper | None = None,
        log: OcelLog | None = None,
        clock: Callable[[], datetime] | None = None,
        id_prefix: str = "e",
    ) -> None:
        self.log = log if log is not None else OcelLog()
        self._mapper: OcelMapper = mapper or GenericOcelMapper()
        self._clock = clock
        self._id_prefix = id_prefix
        self._seq = 0

    def record(self, tool: str, params: dict, result: ToolResult) -> None:
        """Observe one completed tool call, appending an event to the log.

        Any failure to map or record is swallowed: a tap observes, it must not
        turn its own bug into a failed tool call.
        """
        try:
            mapping = self._mapper.map(tool, params, result)
            for obj in mapping.objects:
                self.log.ensure_object(obj)
            self._seq += 1
            event = Event(
                id=f"{self._id_prefix}{self._seq}",
                activity=mapping.activity,
                relationships=mapping.relationships,
                attributes=mapping.attributes,
                **({"time": self._clock()} if self._clock is not None else {}),
            )
            self.log.add_event(event)
        except Exception:  # noqa: BLE001 - a tap must never break the call it observes
            return

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"OcelTap({len(self.log.events)} events, "
            f"{len(self.log.objects)} objects)"
        )
