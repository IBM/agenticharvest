#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Hermetic tests for the OpenAI-compatible backend.

Covers the pure conversion/mapping helpers, and one end-to-end tool-use loop
driven by an httpx MockTransport so the standard request/response handling is
exercised without any network.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import pytest

pytest.importorskip("httpx", reason="openai_compatible backend needs the extra")

import httpx

from agenticharvest.orchestration.agents.backends.openai_compatible import (
    OpenAICompatibleAgent,
    _messages_to_openai,
    _openai_stop_reason,
    _parse_arguments,
    _sse_content,
    _tool_to_openai_schema,
)
from agenticharvest.orchestration.agents.base import (
    AgentConfig,
    Message,
    Role,
    StopReason,
    Tool,
)
from agenticharvest.utils.llm import LLMConfig

# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def test_stop_reason_mapping() -> None:
    assert _openai_stop_reason("stop") == StopReason.END_TURN
    assert _openai_stop_reason("tool_calls") == StopReason.TOOL_USE
    assert _openai_stop_reason("length") == StopReason.MAX_TOKENS
    # Unknown / missing falls back to END_TURN, never an invalid enum.
    assert _openai_stop_reason("something_new") == StopReason.END_TURN
    assert _openai_stop_reason(None) == StopReason.END_TURN


def test_tool_schema_wraps_in_function() -> None:
    class Echo(Tool):
        name = "echo"
        description = "Echo it."
        input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

        def run(self, arguments: dict) -> str:
            return ""

    schema = _tool_to_openai_schema(Echo())
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert schema["function"]["parameters"] == {"type": "object", "properties": {}}


def test_messages_conversion_maps_tool_role() -> None:
    converted = _messages_to_openai(
        [
            Message(role=Role.USER, content="q"),
            Message(role=Role.ASSISTANT, content="a"),
            Message(role=Role.TOOL, content="r", tool_call_id="c1"),
        ]
    )
    assert converted == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
    ]


def test_parse_arguments_handles_json_string_and_garbage() -> None:
    assert _parse_arguments('{"a": 1}') == {"a": 1}
    assert _parse_arguments({"a": 1}) == {"a": 1}
    assert _parse_arguments("") == {}
    assert _parse_arguments("not json") == {}
    assert _parse_arguments("[1, 2]") == {}  # non-object JSON -> empty dict


def test_sse_content_extraction() -> None:
    line = 'data: {"choices":[{"delta":{"content":"hi"}}]}'
    assert _sse_content(line) == "hi"
    assert _sse_content("data: [DONE]") is None
    assert _sse_content(": keep-alive") is None
    assert _sse_content("") is None


# ----------------------------------------------------------------------
# Full tool-use loop over a mock transport
# ----------------------------------------------------------------------


class _EchoTool(Tool):
    name = "echo"
    description = "Echo the value."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def run(self, arguments: dict) -> str:
        return f"echoed:{arguments['value']}"


def _mock_transport(responses: list[dict]) -> httpx.MockTransport:
    """Serve the given JSON bodies in order, one per request."""
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = responses[calls["i"]]
        calls["i"] += 1
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def _agent_with_transport(
    transport: httpx.MockTransport,
) -> OpenAICompatibleAgent:
    """Build the agent, then swap its client for a mock-backed one (no socket)."""
    llm = LLMConfig(api_base="http://test/v1", api_key="k", model="m")
    agent = OpenAICompatibleAgent(
        AgentConfig(model="m", system_prompt="Use the tools."),
        tools=[_EchoTool()],
        llm_config=llm,
    )
    await agent._client.aclose()  # discard the real client the constructor made
    agent._client = httpx.AsyncClient(
        base_url=llm.api_base.rstrip("/"), transport=transport
    )
    return agent


def test_run_executes_tool_then_returns_final_text() -> None:
    tool_call_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": json.dumps({"value": "hi"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    final_response = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "done: hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3},
    }

    async def go() -> None:
        agent = await _agent_with_transport(
            _mock_transport([tool_call_response, final_response])
        )
        result = await agent.run("echo hi please")
        assert result.content == "done: hi"
        assert result.stop_reason == StopReason.END_TURN
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "echo"
        assert result.tool_calls[0].arguments == {"value": "hi"}
        assert result.tool_calls[0].result == "echoed:hi"
        # Token usage is summed across both round trips.
        assert result.input_tokens == 18
        assert result.output_tokens == 8
        await agent.close()

    asyncio.run(go())
