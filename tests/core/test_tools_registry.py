#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the tool registry, result envelope, and tap seam."""

from __future__ import annotations

import pytest

from agenticharvest.core.tools import InMemoryTap, ToolRegistry, ToolResult, ToolSpec


def _spec(name: str, effect: str = "read") -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool", effect=effect, domain="test")


def test_register_get_and_list() -> None:
    registry = ToolRegistry()
    registry.register(_spec("echo"), lambda p: p.get("value"))

    assert registry.has("echo")
    assert registry.get("echo").name == "echo"
    assert [s.name for s in registry.list_specs()] == ["echo"]
    assert registry.get("missing") is None


def test_register_rejects_duplicate() -> None:
    registry = ToolRegistry()
    registry.register(_spec("echo"), lambda p: None)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_spec("echo"), lambda p: None)


def test_call_returns_success_envelope() -> None:
    registry = ToolRegistry()
    registry.register(_spec("echo"), lambda p: {"echoed": p["value"]})

    result = registry.call("echo", {"value": 42})

    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.output == {"echoed": 42}
    assert result.error is None


def test_call_catches_handler_exception() -> None:
    registry = ToolRegistry()

    def boom(_: dict) -> None:
        raise RuntimeError("kaboom")

    registry.register(_spec("boom"), boom)
    result = registry.call("boom", {})

    assert result.ok is False
    assert "kaboom" in result.error
    assert "RuntimeError" in result.error


def test_call_unknown_tool_is_a_failure_not_a_raise() -> None:
    result = ToolRegistry().call("nope", {})
    assert result.ok is False
    assert "Unknown tool" in result.error


def test_tap_records_every_call() -> None:
    tap = InMemoryTap()
    registry = ToolRegistry(taps=[tap])
    registry.register(_spec("echo"), lambda p: p["value"])

    registry.call("echo", {"value": "a"})
    registry.call("echo", {"value": "b"})

    assert len(tap.records) == 2
    assert [r.result.output for r in tap.calls_to("echo")] == ["a", "b"]
    assert tap.last().params == {"value": "b"}


def test_tap_records_failures_too() -> None:
    tap = InMemoryTap()
    registry = ToolRegistry(taps=[tap])
    registry.register(_spec("boom"), lambda p: 1 / 0)

    registry.call("boom", {})

    assert tap.last().result.ok is False


def test_add_tap_after_construction() -> None:
    registry = ToolRegistry()
    registry.register(_spec("echo"), lambda p: 1)
    tap = InMemoryTap()
    registry.add_tap(tap)

    registry.call("echo", {})

    assert len(tap.records) == 1
