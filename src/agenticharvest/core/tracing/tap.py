#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The tracing tap — a tool call becomes a span.

``TracingTap`` implements the ``core.tools.Tap`` protocol, so attaching one to a
registry turns every tool call into a span, live, at dispatch time — the tracing
sibling of ``OcelTap``. Both consume the same one-method seam: one call, two
observers — mining reads the event log, dashboards watch the spans.

The ``core.tools`` tap seam fires *once, after* a call completes, so a span made
here marks the completed call (``start_time == end_time``) and carries its
status and parameters. When a caller needs a real duration it brackets the work
with :class:`~agenticharvest.core.tracing.span.Tracer` instead; the tap is the
zero-wiring path that traces every call without touching handler code. The tap
never raises: observation must not break the call it observes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from agenticharvest.core.tools.result import ToolResult
from agenticharvest.core.tracing.sink import InMemorySink, SpanSink
from agenticharvest.core.tracing.span import Span, SpanStatus


class TracingTap:
    """A tap that exports one span per tool call to a sink.

    Args:
        sink: Where spans go. Defaults to a fresh :class:`InMemorySink`.
        clock: Returns the time to stamp on each span; injectable for tests.
        attribute_prefix: Namespace for the parameter attributes (default
            ``"tool"``), so a call's params surface as ``tool.param.<key>``.
    """

    def __init__(
        self,
        sink: SpanSink | None = None,
        clock: Callable[[], datetime] | None = None,
        attribute_prefix: str = "tool",
    ) -> None:
        self.sink: SpanSink = sink if sink is not None else InMemorySink()
        self._clock = clock
        self._prefix = attribute_prefix

    def record(self, tool: str, params: dict, result: ToolResult) -> None:
        """Observe one completed tool call, exporting a span for it.

        Any failure to build or export the span is swallowed: a tap observes, it
        must not turn its own bug into a failed tool call.
        """
        try:
            now = self._clock() if self._clock is not None else None
            attributes: dict[str, Any] = {f"{self._prefix}.name": tool}
            attributes[f"{self._prefix}.ok"] = result.ok
            for key, value in params.items():
                attributes[f"{self._prefix}.param.{key}"] = value
            if not result.ok and result.error:
                attributes[f"{self._prefix}.error"] = result.error

            span = Span(
                name=tool,
                status=SpanStatus.OK if result.ok else SpanStatus.ERROR,
                error=None if result.ok else result.error,
                attributes=attributes,
                **({"start_time": now, "end_time": now} if now is not None else {}),
            )
            self.sink.export(span)
        except Exception:  # noqa: BLE001 - a tap must never break the call it observes
            return

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"TracingTap(sink={self.sink!r})"
