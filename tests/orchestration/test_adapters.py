#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for adapting gateway tools onto the agent ``Tool`` interface."""

from __future__ import annotations

import json

import pytest

from agenticharvest.core.tools import InMemoryTap, ToolRegistry, ToolSpec
from agenticharvest.core.tools.spec import Effect
from agenticharvest.orchestration.agents.adapters import registry_tools, rest_tools


def _registry(tap: InMemoryTap | None = None) -> ToolRegistry:
    registry = ToolRegistry(taps=[tap] if tap else None)
    registry.register(
        ToolSpec(name="echo", description="Echo.", input_schema={"type": "object"}),
        lambda p: {"echoed": p.get("value")},
    )
    registry.register(
        ToolSpec(name="save", description="Save.", effect=Effect.WRITE),
        lambda p: {"saved": True},
    )
    return registry


def test_registry_tools_dispatch_and_render_json() -> None:
    tap = InMemoryTap()
    tools = {t.name: t for t in registry_tools(_registry(tap))}

    assert set(tools) == {"echo", "save"}
    assert tools["echo"].description == "Echo."
    assert json.loads(tools["echo"].run({"value": "hi"})) == {"echoed": "hi"}
    # The call went through the registry, so the tap observed it.
    assert [r.tool for r in tap.records] == ["echo"]


def test_registry_tools_apply_the_dry_run_gate() -> None:
    save = next(t for t in registry_tools(_registry()) if t.name == "save")
    assert save.run({}).startswith("error:")
    assert json.loads(save.run({"confirm": True})) == {"saved": True}


def test_rest_tools_round_trip_over_the_rest_channel() -> None:
    testclient = pytest.importorskip("fastapi.testclient", reason="needs the 'rest' extra")
    from agenticharvest.gateway.channels.rest import create_app

    client = testclient.TestClient(create_app(_registry()))
    tools = {t.name: t for t in rest_tools(client)}

    assert json.loads(tools["echo"].run({"value": 7})) == {"echoed": 7}
    assert "confirm" in tools["save"].run({})
