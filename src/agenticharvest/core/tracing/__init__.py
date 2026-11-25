#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tracing — one tool call = one span.

Tracing is the operational, real-time view of a tapped tool call; the OCEL event
log (``core.events``) is the analytical one. Both consume the same one-method tap
seam in ``core.tools``: one call, two observers. Mining reads the event log;
dashboards watch the spans.

The model here is domain-agnostic and dependency-free. A :class:`Span` is one
operation over time; a :class:`Tracer` brackets real work to produce spans with
true durations; a :class:`TracingTap` emits one span per completed tool call from
the tap seam with no handler changes. Where spans land is a
:class:`SpanSink`: :class:`InMemorySink` (always available, for tests and local
inspection) or :class:`OtelSink`, which bridges to OpenTelemetry when the
optional ``tracing`` extra is installed.

Public API:
    Span, SpanStatus, Tracer              the span model and the bracketing tracer
    SpanSink, InMemorySink, OtelSink      the export seam and its two sinks
    TracingTap                            the Tap that records a span per call
"""

from agenticharvest.core.tracing.sink import InMemorySink, OtelSink, SpanSink
from agenticharvest.core.tracing.span import Span, SpanStatus, Tracer
from agenticharvest.core.tracing.tap import TracingTap

__all__ = [
    "InMemorySink",
    "OtelSink",
    "Span",
    "SpanSink",
    "SpanStatus",
    "Tracer",
    "TracingTap",
]
