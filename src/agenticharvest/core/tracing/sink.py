#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Where spans go — the export seam.

A :class:`SpanSink` is a one-method hook that receives each finished
:class:`~agenticharvest.core.tracing.span.Span`. It is the tracing analogue of the OCEL
log: the tap builds spans, the sink decides where they land.

Two sinks ship:

- :class:`InMemorySink` keeps spans in a list. It is what tests assert against
  and what local inspection uses — no dependency, always available.
- :class:`OtelSink` forwards each span to OpenTelemetry, so an OTLP exporter (a
  Collector, Jaeger, Langfuse, …) configured through the OpenTelemetry SDK
  receives it. It is optional: it imports ``opentelemetry`` lazily and raises a
  clear error if the ``tracing`` extra is not installed, so nothing here forces
  the dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agenticharvest.core.tracing.span import Span


@runtime_checkable
class SpanSink(Protocol):
    """A hook that receives each finished span. Must not raise into the caller."""

    def export(self, span: Span) -> None:
        """Take one finished span and route it wherever this sink sends spans."""
        ...


class InMemorySink:
    """A sink that keeps every span in a list. For tests and local inspection."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        """Append the span to the in-memory list."""
        self.spans.append(span)

    def named(self, name: str) -> list[Span]:
        """Return the spans whose ``name`` matches (convenience for assertions)."""
        return [s for s in self.spans if s.name == name]

    def last(self) -> Span | None:
        """Return the most recent span, or ``None`` if none have been recorded."""
        return self.spans[-1] if self.spans else None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"InMemorySink({len(self.spans)} spans)"


class OtelSink:
    """Forward spans to OpenTelemetry (optional; needs the ``tracing`` extra).

    The sink converts each :class:`~agenticharvest.core.tracing.span.Span` into an
    OpenTelemetry span on the supplied tracer, carrying its start/end time,
    attributes, and status. Where those spans ultimately go (OTLP, console, a
    file) is standard OpenTelemetry SDK configuration the application owns — this
    sink only bridges AgenticHarvest's span into the OTel API.

    Args:
        tracer: An OpenTelemetry ``Tracer``. Defaults to
            ``opentelemetry.trace.get_tracer("agenticharvest")``; with no SDK provider
            configured that is a no-op tracer, so importing costs nothing until
            the application wires an exporter.

    Raises:
        RuntimeError: If ``opentelemetry`` is not installed.
    """

    def __init__(self, tracer: Any | None = None) -> None:
        try:
            from opentelemetry import trace
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "OtelSink needs OpenTelemetry — install the 'tracing' extra: "
                "pip install 'agenticharvest[tracing]'."
            ) from exc
        self._trace = trace
        self._tracer = tracer if tracer is not None else trace.get_tracer("agenticharvest")

    def export(self, span: Span) -> None:
        """Emit ``span`` as an OpenTelemetry span on the configured tracer."""
        from agenticharvest.core.tracing.span import SpanStatus

        start_ns = int(span.start_time.timestamp() * 1e9)
        end_ns = int(span.end_time.timestamp() * 1e9)
        otel_span = self._tracer.start_span(span.name, start_time=start_ns)
        try:
            for key, value in span.attributes.items():
                otel_span.set_attribute(key, _otel_attr(value))
            if span.status is SpanStatus.ERROR:
                status = self._trace.Status(
                    self._trace.StatusCode.ERROR, span.error or ""
                )
            else:
                status = self._trace.Status(self._trace.StatusCode.OK)
            otel_span.set_status(status)
        finally:
            otel_span.end(end_time=end_ns)


def _otel_attr(value: Any) -> Any:
    """Coerce an attribute value to a type OpenTelemetry accepts.

    OTel attributes must be a str, bool, int, float, or a homogeneous sequence of
    those. Anything else (a dict, a nested list) is rendered to its string form.
    """
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(v, (str, bool, int, float)) for v in value
    ):
        return list(value)
    return str(value)
