#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the MCP channel, driven by a real in-memory MCP client."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp", reason="MCP channel tests need the 'mcp' extra")

import anyio
from mcp.client import Client

from agenticharvest.core.tools import InMemoryTap, ToolRegistry, ToolSpec
from agenticharvest.core.tools.spec import Effect
from agenticharvest.gateway.channels.mcp import create_server


def _registry(tap: InMemoryTap) -> ToolRegistry:
    registry = ToolRegistry(taps=[tap])
    registry.register(
        ToolSpec(
            name="echo",
            description="Echo the value back.",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
        ),
        lambda p: {"echoed": p["value"]},
    )
    registry.register(
        ToolSpec(name="save", description="Save.", effect=Effect.WRITE),
        lambda p: {"saved": True},
    )
    return registry


def test_mcp_lists_tools_with_effect_hints() -> None:
    async def go() -> None:
        async with Client(create_server(_registry(InMemoryTap()))) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"echo", "save"}
        assert tools["echo"].annotations.read_only_hint is True
        assert tools["save"].annotations.destructive_hint is True
        assert tools["echo"].input_schema["properties"] == {"value": {"type": "string"}}

    anyio.run(go)


def test_mcp_call_goes_through_dispatch_and_the_tap() -> None:
    tap = InMemoryTap()

    async def go() -> None:
        async with Client(create_server(_registry(tap))) as client:
            ok = await client.call_tool("echo", {"value": "hi"})
            blocked = await client.call_tool("save", {})
            confirmed = await client.call_tool("save", {"confirm": True})

        assert ok.is_error is False
        assert json.loads(ok.content[0].text) == {"echoed": "hi"}
        assert ok.structured_content == {"echoed": "hi"}
        # The dry-run gate applies over MCP exactly as over REST.
        assert blocked.is_error is True
        assert "confirm" in blocked.content[0].text
        assert confirmed.is_error is False

    anyio.run(go)
    # The gated call never reached the handler, so only two calls were tapped.
    assert [r.tool for r in tap.records] == ["echo", "save"]


def test_mcp_unknown_tool_is_a_tool_error() -> None:
    async def go() -> None:
        async with Client(create_server(_registry(InMemoryTap()))) as client:
            result = await client.call_tool("nope", {})
        assert result.is_error is True
        assert "Unknown tool" in result.content[0].text

    anyio.run(go)
