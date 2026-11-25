#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""OpenAI-compatible agent backend.

Speaks the standard Chat Completions HTTP shape over any base URL — OpenAI
itself, a LiteLLM proxy, a vLLM server, an Azure-compatible gateway. It calls
``POST {api_base}/chat/completions`` with ``httpx`` directly rather than a vendor
SDK, so "generic" is structural: there is no provider package to import, only the
request/response format every OpenAI-compatible endpoint implements. The concrete
model and endpoint arrive as an ``LLMConfig`` at construction — never hardcoded.

The tool-use loop mirrors the Claude backend: send the thread, and while the
model asks for tools, execute them and append the results before the next call,
until the model returns a final message or the retry budget is exhausted.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agenticharvest.orchestration.agents.base import (
    DEFAULT_HTTP_RETRIES,
    DEFAULT_REFLECT_PROMPT,
    AbstractAgent,
    AgentConfig,
    AgentResult,
    Message,
    Role,
    StopReason,
    Tool,
    ToolCall,
)
from agenticharvest.utils.llm import LLMConfig

# Path appended to the configured base URL. The base already carries any version
# segment (e.g. ``/v1``); this is the standard Chat Completions route.
_CHAT_COMPLETIONS_PATH = "/chat/completions"


def _openai_stop_reason(raw: str | None) -> StopReason:
    """Map an OpenAI ``finish_reason`` to a ``StopReason``.

    Any value not listed (including future or provider-specific additions) is
    treated as ``END_TURN`` when the model produced content, so callers always
    receive a valid enum member.

    Args:
        raw: The ``finish_reason`` from a choice.

    Returns:
        The corresponding ``StopReason``.
    """
    mapping = {
        "stop": StopReason.END_TURN,
        "tool_calls": StopReason.TOOL_USE,
        "function_call": StopReason.TOOL_USE,
        "length": StopReason.MAX_TOKENS,
    }
    return mapping.get(raw or "", StopReason.END_TURN)


def _tool_to_openai_schema(tool: Tool) -> dict[str, Any]:
    """Convert a ``Tool`` to an OpenAI ``tools`` entry (a ``function`` wrapper)."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _messages_to_openai(
    context: list[Message] | None,
) -> list[dict[str, Any]]:
    """Convert ``Message`` objects to OpenAI chat messages.

    A ``TOOL`` message becomes a ``tool`` role entry keyed by ``tool_call_id``,
    which the OpenAI format uses to tie a result back to the assistant's request.

    Args:
        context: Conversation messages to convert. ``None`` is treated as empty.

    Returns:
        A list of OpenAI chat-message dicts.
    """
    out: list[dict[str, Any]] = []
    for m in context or []:
        if m.role == Role.TOOL:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                }
            )
        else:
            role = "assistant" if m.role == Role.ASSISTANT else "user"
            out.append({"role": role, "content": m.content})
    return out


class OpenAICompatibleAgent(AbstractAgent):
    """Agent backend for any OpenAI-compatible Chat Completions endpoint.

    Args:
        config: Agent configuration (model field is informational here; the model
            actually requested comes from ``llm_config.model``).
        tools: ``Tool`` instances made available to the model.
        llm_config: The endpoint connection settings (base URL, key, model).
    """

    def __init__(
        self,
        config: AgentConfig,
        tools: list[Tool],
        llm_config: LLMConfig,
    ) -> None:
        super().__init__(config, tools)
        self._llm = llm_config
        self._tool_schemas = [_tool_to_openai_schema(tool) for tool in self.tools]
        self._client = httpx.AsyncClient(
            base_url=llm_config.api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {llm_config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout_seconds,
            transport=httpx.AsyncHTTPTransport(retries=DEFAULT_HTTP_RETRIES),
        )

    @property
    def backend_name(self) -> str:
        return "openai_compatible"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AgentResult:
        """Execute one agent turn, running any requested tools before returning.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Returns:
            An ``AgentResult`` with the final text, message history, tool calls,
            token usage, and stop reason.
        """
        messages = self._initial_messages(context, prompt)

        accumulated_tool_calls: list[ToolCall] = []
        total_input_tokens = total_output_tokens = 0

        for _ in range(self.config.max_retries + 1):
            data = await self._post(messages)
            usage = data.get("usage") or {}
            total_input_tokens += int(usage.get("prompt_tokens", 0) or 0)
            total_output_tokens += int(usage.get("completion_tokens", 0) or 0)

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            finish_reason = choice.get("finish_reason")
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                final_text = message.get("content") or ""
                out_messages = list(context or [])
                out_messages.append(Message(role=Role.USER, content=prompt))
                out_messages.append(
                    Message(role=Role.ASSISTANT, content=final_text)
                )
                return AgentResult(
                    content=final_text,
                    messages=out_messages,
                    tool_calls=accumulated_tool_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    stop_reason=_openai_stop_reason(finish_reason),
                )

            # --- tool_calls branch ---
            # Append the assistant turn verbatim so the tool results that follow
            # reference valid tool_call ids, then run each requested tool.
            messages.append(message)
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                arguments = _parse_arguments(fn.get("arguments"))
                result_text = self._invoke_tool(name, arguments)
                accumulated_tool_calls.append(
                    ToolCall(
                        id=call.get("id", ""),
                        name=name,
                        arguments=arguments,
                        result=result_text,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result_text,
                    }
                )

        # Retry budget exhausted without a final text response.
        return AgentResult(
            content="",
            tool_calls=accumulated_tool_calls,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            stop_reason=StopReason.ERROR,
        )

    async def stream(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AsyncIterator[str]:
        """Stream response text chunks via server-sent events.

        Tool calls are not handled while streaming; use ``run()`` when tools are
        required.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Yields:
            Text chunks in the order the model produces them.
        """
        messages = self._initial_messages(context, prompt)
        body = self._request_body(messages, stream=True)
        async with self._client.stream(
            "POST", _CHAT_COMPLETIONS_PATH, json=body
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                chunk = _sse_content(line)
                if chunk:
                    yield chunk

    async def reflect(self, previous: AgentResult) -> AgentResult:
        """Critique and improve a previous result by re-running with it as context."""
        return await self.run(DEFAULT_REFLECT_PROMPT, context=previous.messages)

    async def close(self) -> None:
        """Close the underlying HTTP client and release connections."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _initial_messages(
        self, context: list[Message] | None, prompt: str
    ) -> list[dict[str, Any]]:
        """Assemble the starting message list: system + context + new prompt."""
        messages: list[dict[str, Any]] = []
        system_prompt = self._build_system_prompt()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(_messages_to_openai(context))
        messages.append({"role": "user", "content": prompt})
        return messages

    def _request_body(
        self, messages: list[dict[str, Any]], *, stream: bool = False
    ) -> dict[str, Any]:
        """Build the Chat Completions request body."""
        body: dict[str, Any] = {
            "model": self._llm.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self._tool_schemas:
            body["tools"] = self._tool_schemas
        if stream:
            body["stream"] = True
        return body

    async def _post(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """POST one Chat Completions request and return the parsed JSON."""
        response = await self._client.post(
            _CHAT_COMPLETIONS_PATH, json=self._request_body(messages)
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError(f"expected a JSON object from the endpoint, got {type(data).__name__}")
        return data

    def _invoke_tool(self, name: str, arguments: dict) -> str:
        """Find and call a tool by name, returning its output as a string.

        Args:
            name: The tool name the model requested.
            arguments: The parsed arguments the model passed.

        Returns:
            The tool's string output, or an error message string when the tool is
            unknown or raises — so the model can self-correct.
        """
        for tool in self.tools:
            if tool.name == name:
                try:
                    return tool(arguments)
                except Exception as exc:  # noqa: BLE001
                    return f"Tool error: {exc}"
        return f"Unknown tool: {name}"


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Parse a tool call's ``arguments`` (a JSON string) into a dict.

    OpenAI sends arguments as a JSON-encoded string. A malformed or empty value
    yields an empty dict rather than raising, so the loop can proceed and let the
    tool report the missing parameters.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sse_content(line: str) -> str | None:
    """Extract the text delta from one server-sent-events line, if any.

    Args:
        line: A raw line from the streamed response.

    Returns:
        The content delta, or ``None`` for keep-alives, the ``[DONE]`` sentinel,
        and lines that carry no text.
    """
    if not line or not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choice = (data.get("choices") or [{}])[0]
    return (choice.get("delta") or {}).get("content")
