#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the generic span model, the tracer, the sinks, and the tap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agenticharvest.core.tools import ToolRegistry, ToolSpec
from agenticharvest.core.tracing import (
    InMemorySink,
    Span,
    SpanStatus,
    Tracer,
    TracingTap,
)


def _spec(name: str, effect: str = "read") -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool", effect=effect, domain="test")


# ----------------------------------------------------------------------
# The span model
# ----------------------------------------------------------------------


def test_duration_ms_reflects_elapsed_time() -> None:
    start = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    span = Span(name="x", start_time=start, end_time=start + timedelta(milliseconds=250))

    assert span.duration_ms == 250.0


def test_marker_span_has_zero_duration() -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    span = Span(name="x", start_time=now, end_time=now)

    assert span.duration_ms == 0.0
    assert span.status is SpanStatus.OK


# ----------------------------------------------------------------------
# The tracer (real bracketed timing)
# ----------------------------------------------------------------------


def test_tracer_span_records_real_duration() -> None:
    # A clock that advances 10ms on each read: enter, then exit.
    base = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    ticks = iter([base, base + timedelta(milliseconds=10)])
    sink = InMemorySink()
    tracer = Tracer(sink=sink, clock=lambda: next(ticks))

    with tracer.span("work", attributes={"n": 1}) as span:
        span.attributes["seen"] = True

    recorded = sink.last()
    assert recorded.name == "work"
    assert recorded.duration_ms == 10.0
    assert recorded.status is SpanStatus.OK
    assert recorded.attributes == {"n": 1, "seen": True}


def test_tracer_span_marks_error_and_reraises() -> None:
    sink = InMemorySink()
    tracer = Tracer(sink=sink)

    with pytest.raises(ValueError), tracer.span("work"):
        raise ValueError("boom")

    span = sink.last()
    assert span.status is SpanStatus.ERROR
    assert span.error == "ValueError: boom"


def test_tracer_defaults_to_in_memory_sink() -> None:
    tracer = Tracer()
    with tracer.span("x"):
        pass

    assert isinstance(tracer.sink, InMemorySink)
    assert len(tracer.sink.spans) == 1


# ----------------------------------------------------------------------
# The in-memory sink
# ----------------------------------------------------------------------


def test_in_memory_sink_named_and_last() -> None:
    sink = InMemorySink()
    sink.export(Span(name="a"))
    sink.export(Span(name="b"))
    sink.export(Span(name="a"))

    assert [s.name for s in sink.named("a")] == ["a", "a"]
    assert sink.last().name == "a"


def test_in_memory_sink_last_is_none_when_empty() -> None:
    assert InMemorySink().last() is None


# ----------------------------------------------------------------------
# The tap
# ----------------------------------------------------------------------


def test_tracing_tap_records_one_span_per_call() -> None:
    sink = InMemorySink()
    registry = ToolRegistry(taps=[TracingTap(sink=sink)])
    registry.register(_spec("echo"), lambda p: p.get("value"))

    registry.call("echo", {"path": "a.txt", "value": 1})
    registry.call("echo", {"path": "b.txt", "value": 2})

    assert [s.name for s in sink.spans] == ["echo", "echo"]
    first = sink.spans[0]
    assert first.status is SpanStatus.OK
    assert first.attributes["tool.name"] == "echo"
    assert first.attributes["tool.ok"] is True
    assert first.attributes["tool.param.path"] == "a.txt"
    assert first.attributes["tool.param.value"] == 1


def test_tracing_tap_marks_failed_calls() -> None:
    sink = InMemorySink()
    registry = ToolRegistry(taps=[TracingTap(sink=sink)])

    def boom(_p: dict) -> None:
        raise RuntimeError("nope")

    registry.register(_spec("boom"), boom)
    registry.call("boom", {"path": "a.txt"})

    span = sink.last()
    assert span.status is SpanStatus.ERROR
    assert span.attributes["tool.ok"] is False
    assert "nope" in span.attributes["tool.error"]
    assert "nope" in (span.error or "")


def test_tracing_tap_uses_injected_clock() -> None:
    fixed = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    sink = InMemorySink()
    registry = ToolRegistry(taps=[TracingTap(sink=sink, clock=lambda: fixed)])
    registry.register(_spec("echo"), lambda p: None)

    registry.call("echo", {"path": "a.txt"})

    span = sink.last()
    assert span.start_time == fixed
    assert span.end_time == fixed
    assert span.duration_ms == 0.0


def test_tracing_tap_defaults_to_own_in_memory_sink() -> None:
    tap = TracingTap()
    registry = ToolRegistry(taps=[tap])
    registry.register(_spec("echo"), lambda p: None)

    registry.call("echo", {"path": "a.txt"})

    assert isinstance(tap.sink, InMemorySink)
    assert len(tap.sink.spans) == 1


# ----------------------------------------------------------------------
# The OpenTelemetry sink (only when the optional `tracing` extra is installed)
# ----------------------------------------------------------------------


def test_otel_sink_emits_a_real_span() -> None:
    pytest.importorskip("opentelemetry.sdk", reason="needs the 'tracing' extra")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from agenticharvest.core.tracing import OtelSink

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sink = OtelSink(tracer=provider.get_tracer("test"))

    start = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    sink.export(
        Span(
            name="get_data_flow",
            start_time=start,
            end_time=start + timedelta(milliseconds=5),
            status=SpanStatus.ERROR,
            error="boom",
            attributes={"tool.name": "get_data_flow", "tool.ok": False},
        )
    )

    finished = exporter.get_finished_spans()
    assert [s.name for s in finished] == ["get_data_flow"]
    assert finished[0].attributes["tool.name"] == "get_data_flow"
    assert finished[0].status.status_code.name == "ERROR"
