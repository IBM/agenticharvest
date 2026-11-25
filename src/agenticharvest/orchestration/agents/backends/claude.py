#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Claude agent backend.

Uses ``anthropic.AsyncAnthropic`` to call the Anthropic Messages API directly.
Tool calls are executed in a loop: when the model requests a tool the caller-
supplied tool list is searched for a matching callable, invoked, and the result
is appended before the next API call. The loop continues until the model returns
a final text response or the retry budget is exhausted.
"""

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from agenticharvest.orchestration.agents.base import (
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


def _anthropic_stop_reason(raw: str) -> StopReason:
    """Map an Anthropic API stop reason string to a ``StopReason``.

    Any value not listed in the mapping (including future Anthropic additions)
    is treated as ``ERROR`` so callers always receive a valid enum member.

    Args:
        raw: The ``stop_reason`` string from an Anthropic ``Message`` response.

    Returns:
        The corresponding ``StopReason`` enum member.
    """
    mapping = {
        "end_turn":   StopReason.END_TURN,
        "tool_use":   StopReason.TOOL_USE,
        "max_tokens": StopReason.MAX_TOKENS,
    }
    return mapping.get(raw, StopReason.ERROR)


def _messages_to_anthropic(
    context: list[Message] | None,
) -> list[MessageParam]:
    """Convert ``Message`` objects to Anthropic API ``MessageParam`` dicts.

    ``TOOL`` role messages are mapped to ``user`` role because the Anthropic API
    represents tool results as user-turn content blocks rather than a distinct
    role.

    Args:
        context: Conversation messages to convert. ``None`` is treated as empty.

    Returns:
        A list of ``MessageParam`` dicts ready to pass to
        ``AsyncAnthropic.messages.create(messages=...)``.
    """
    role_map = {Role.USER: "user", Role.ASSISTANT: "assistant", Role.TOOL: "user"}
    return [{"role": role_map[m.role], "content": m.content} for m in (context or [])]


class ClaudeAgent(AbstractAgent):
    """Agent backend that calls the Anthropic Messages API directly.

    Wraps ``AsyncAnthropic`` and handles the tool-use loop internally: after
    each API response the agent executes any requested tools, appends results to
    the conversation, and calls the API again until it receives a final text turn.

    Args:
        config: Agent configuration (model, skills, sampling parameters, retry
            budget).
        tools: ``Tool`` instances made available to the model.
    """

    def __init__(self, config: AgentConfig, tools: list[Tool]) -> None:
        super().__init__(config, tools)
        self._client = AsyncAnthropic()
        # Build tool schemas once at construction — schemas don't change per call.
        self._tool_schemas = [tool.schema() for tool in self.tools]

    @property
    def backend_name(self) -> str:
        return "claude"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AgentResult:
        """Execute one agent turn, handling tool calls before returning.

        Calls ``messages.create`` in a loop. When the model requests tools the
        agent executes them and appends the results before the next call. The
        loop ends when the model produces a non-tool response or the retry
        budget (``config.max_retries``) is exhausted.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Returns:
            An ``AgentResult`` containing the final text, full message history,
            all tool calls made during the turn, token usage, and stop reason.
        """
        # Start with prior context converted to Anthropic format, then append
        # the new user prompt as the last message in the thread.
        messages: list[MessageParam] = _messages_to_anthropic(context)
        messages.append({"role": "user", "content": prompt})

        accumulated_tool_calls: list[ToolCall] = []
        # Track tokens across all loop iterations so the final AgentResult
        # reflects the total cost of the entire turn, not just the last call.
        total_input_tokens = total_output_tokens = 0

        # Loop up to max_retries + 1 times. Each iteration is either:
        #   (a) a final response → return immediately, or
        #   (b) a tool-use response → execute tools, append results, continue.
        for _ in range(self.config.max_retries + 1):
            response = await self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=self._build_system_prompt(),
                messages=messages,
                # Pass an empty list rather than omitting tools entirely;
                # omitting the field raises a validation error when schemas exist.
                tools=self._tool_schemas if self._tool_schemas else [],
                temperature=self.config.temperature,
            )

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Any stop reason other than "tool_use" means the model is done.
            if response.stop_reason != "tool_use":
                # Concatenate all text blocks — the model may split output across
                # multiple content blocks when tool use and text are interleaved.
                final_text = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                # Rebuild the output message list from the original context plus
                # the new user prompt and assistant reply so the caller gets the
                # full thread without needing to re-assemble it.
                out_messages = list(context or [])
                out_messages.append(Message(role=Role.USER, content=prompt))
                out_messages.append(Message(role=Role.ASSISTANT, content=final_text))
                return AgentResult(
                    content=final_text,
                    messages=out_messages,
                    tool_calls=accumulated_tool_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    stop_reason=_anthropic_stop_reason(response.stop_reason),
                )

            # --- tool_use branch ---
            # Collect every tool_use block from the response, execute each one,
            # and wrap the results as tool_result content blocks.
            tool_result_blocks = []
            for block in response.content:
                if block.type != "tool_use":
                    continue  # text blocks alongside tool_use are discarded mid-loop
                result_text = self._invoke_tool(block.name, block.input)
                accumulated_tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                        result=result_text,
                    )
                )
                # Each tool_result must reference the tool_use block by id so
                # the model can match results back to its requests.
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            # Append the full assistant turn (including tool_use blocks) so the
            # next call sees the unmodified model output, then append tool results
            # as a new user turn — this is the Anthropic multi-turn tool format.
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_result_blocks})

        # Retry budget exhausted without reaching a final text response.
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
        """Stream response text chunks as they are produced by the model.

        Uses the Anthropic streaming API. Tool calls are not handled during
        streaming; use ``run()`` when tool execution is required.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Yields:
            Text chunks in the order they are produced by the model.
        """
        messages: list[MessageParam] = _messages_to_anthropic(context)
        messages.append({"role": "user", "content": prompt})

        # Open a streaming context manager; text_stream yields decoded strings
        # as the model produces them, without waiting for the full response.
        async with self._client.messages.stream(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=self._build_system_prompt(),
            messages=messages,
            temperature=self.config.temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def reflect(self, previous: AgentResult) -> AgentResult:
        """Critique and improve a previous result.

        Re-uses ``run()`` with the previous messages as context so the model
        sees its own output before being asked to critique and improve it.

        Args:
            previous: The ``AgentResult`` to reflect on.

        Returns:
            A new ``AgentResult`` containing the improved response.
        """
        return await self.run(DEFAULT_REFLECT_PROMPT, context=previous.messages)

    async def close(self) -> None:
        """Close the underlying Anthropic HTTP client and release connections."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _invoke_tool(self, name: str, arguments: dict) -> str:
        """Find and call a tool by name, returning its output as a string.

        Args:
            name: The tool name requested by the model.
            arguments: Key-value arguments the model passed to the tool.

        Returns:
            The string result of calling the tool, or an error message string
            if the tool is not found or raises an exception.
        """
        for tool in self.tools:
            if tool.name == name:
                try:
                    return tool(arguments)
                except Exception as exc:  # noqa: BLE001
                    return f"Tool error: {exc}"
        return f"Unknown tool: {name}"
