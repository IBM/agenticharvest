#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the gateway dispatch path and the dry-run policy gate."""

from __future__ import annotations

from agenticharvest.core.tools import ToolRegistry, ToolSpec
from agenticharvest.gateway.dispatch import ToolRequest, dispatch


def _read_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="echo", description="echo", effect="read", domain="test"),
        lambda p: p.get("value"),
    )
    return registry


def _write_registry() -> tuple[ToolRegistry, list[dict]]:
    """A registry with one write-effect tool that records what it ran with."""
    ran: list[dict] = []

    def apply(params: dict) -> str:
        ran.append(params)
        return "applied"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="apply", description="apply", effect="write", domain="test"),
        apply,
    )
    return registry, ran


def test_dispatch_happy_path() -> None:
    result = dispatch(_read_registry(), ToolRequest(tool="echo", params={"value": 7}))
    assert result.ok is True
    assert result.output == 7


def test_dispatch_unknown_tool_fails() -> None:
    result = dispatch(_read_registry(), ToolRequest(tool="missing", params={}))
    assert result.ok is False
    assert "Unknown tool" in result.error


def test_dry_run_gate_blocks_unconfirmed_write() -> None:
    registry, ran = _write_registry()

    result = dispatch(registry, ToolRequest(tool="apply", params={"x": 1}))

    assert result.ok is False
    assert "requires" in result.error
    assert ran == []  # the handler never ran


def test_dry_run_gate_allows_confirmed_write() -> None:
    registry, ran = _write_registry()

    result = dispatch(
        registry, ToolRequest(tool="apply", params={"x": 1, "confirm": True})
    )

    assert result.ok is True
    assert result.output == "applied"
    assert ran == [{"x": 1, "confirm": True}]


def test_read_tools_are_never_gated() -> None:
    # A read tool with no confirm flag runs straight through.
    result = dispatch(_read_registry(), ToolRequest(tool="echo", params={"value": 1}))
    assert result.ok is True
