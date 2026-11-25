#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The generic span model and a small tracer to produce spans.

A span records *one operation over time* — when it started, when it ended, how
it finished, and a bag of attributes describing it. It is the operational,
real-time twin of an OCEL event: the same tapped tool call becomes an event (for
mining, analytical) and a span (for tracing, live). This module is the
domain-agnostic core; it knows nothing about spreadsheets or OpenTelemetry.

Two ways to make a span:

- :class:`Tracer` brackets a block of work with :meth:`Tracer.span`, so the span
  carries a *real* start/end and duration and its status reflects whether the
  block raised. This is the accurate path when a caller owns the execution.
- :class:`~agenticharvest.core.tracing.tap.TracingTap` emits one span per completed tool
  call from the ``core.tools`` tap seam. That seam fires once, after the call, so
  a tap-made span marks completion (``start_time == end_time``); real bracketing
  is what :class:`Tracer` is for.

Both feed a :class:`~agenticharvest.core.tracing.sink.SpanSink`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agenticharvest.core.tracing.sink import SpanSink


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class SpanStatus(str, Enum):
    """How a span finished. ``str`` enum so it serializes as a plain string."""

    OK = "ok"
    ERROR = "error"


class Span(BaseModel):
    """One traced operation: what it was, when it ran, and how it finished.

    Attributes:
        name: What happened — for a tool call, the tool name (e.g.
            ``get_data_flow``). Mirrors an OCEL event's ``activity``.
        start_time: When the operation began (timezone-aware UTC).
        end_time: When it ended. Equal to ``start_time`` for a marker span made
            from the post-hoc tap seam.
        status: ``OK`` or ``ERROR``.
        attributes: Free-form key/value detail (parameters, flags, the error).
        error: The error message when ``status`` is ``ERROR``; ``None`` otherwise.
    """

    name: str
    start_time: datetime = Field(default_factory=_utcnow)
    end_time: datetime = Field(default_factory=_utcnow)
    status: SpanStatus = SpanStatus.OK
    attributes: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        """Elapsed wall-clock time in milliseconds (``0.0`` for a marker span)."""
        return (self.end_time - self.start_time).total_seconds() * 1000.0


class Tracer:
    """Produce spans that bracket real work and export them to a sink.

    Args:
        sink: Where finished spans go. Defaults to a fresh
            :class:`~agenticharvest.core.tracing.sink.InMemorySink`.
        clock: Returns "now"; injectable so tests get deterministic timing.
    """

    def __init__(
        self,
        sink: SpanSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # Imported here to avoid a module-load cycle (sink imports this module).
        from agenticharvest.core.tracing.sink import InMemorySink

        self.sink: SpanSink = sink if sink is not None else InMemorySink()
        self._clock = clock or _utcnow

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[Span]:
        """Bracket a block of work, exporting a span when it exits.

        The span's ``start_time`` is stamped on entry and ``end_time`` on exit,
        so ``duration_ms`` is real. If the block raises, the span is marked
        ``ERROR`` with the exception message and the exception re-raised — tracing
        observes, it never swallows the caller's error.

        Yields the mutable :class:`Span` so the block can add attributes as it
        learns them (``with tracer.span("x") as s: s.attributes[...] = ...``).
        """
        span = Span(
            name=name,
            start_time=self._clock(),
            attributes=dict(attributes or {}),
        )
        try:
            yield span
        except Exception as exc:  # record, then re-raise — tracing never swallows
            span.status = SpanStatus.ERROR
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.end_time = self._clock()
            self.sink.export(span)
