#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Gemini agent backend.

Uses ``google-genai`` (``google.genai``) to call the Gemini API asynchronously.
Tool calls follow Gemini's ``FunctionCall`` / ``FunctionResponse`` turn format
and are executed in a loop until the model produces a final text response or
the retry budget is exhausted.
"""

from collections.abc import AsyncIterator

from google import genai
from google.genai import types as genai_types

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


def _messages_to_gemini(
    context: list[Message] | None,
) -> list[genai_types.Content]:
    """Convert ``Message`` objects to Gemini ``Content`` instances.

    ``TOOL`` role messages have no direct Gemini equivalent and are mapped to
    ``user`` role so the conversation history remains well-formed. For proper
    function-response turns, build ``FunctionResponse`` parts directly inside
    ``run()`` rather than passing them through context.

    Args:
        context: Conversation messages to convert. ``None`` is treated as empty.

    Returns:
        A list of ``genai_types.Content`` instances in the same order as the
        input, ready to prepend before the current prompt content.
    """
    # Gemini uses "model" for assistant turns, not "assistant".
    # TOOL has no dedicated role — map it to "user" so the history stays valid.
    role_map = {Role.USER: "user", Role.ASSISTANT: "model", Role.TOOL: "user"}
    return [
        genai_types.Content(
            role=role_map[m.role],
            parts=[genai_types.Part(text=m.content)],
        )
        for m in (context or [])
    ]


class GeminiAgent(AbstractAgent):
    """Agent backend that calls the Google Gemini API via ``google-genai``.

    Wraps ``genai.Client`` and handles the function-call loop internally: after
    each API response the agent executes any requested function calls, appends
    ``FunctionResponse`` parts as a new user turn, and calls the API again until
    the model returns a plain text response or the retry budget is exhausted.

    Args:
        config: Agent configuration. The ``model`` field must be a Gemini model
            ID such as ``gemini-2.0-flash`` or ``gemini-2.5-pro``.
        tools: ``Tool`` instances made available to the model.
    """

    def __init__(self, config: AgentConfig, tools: list[Tool]) -> None:
        super().__init__(config, tools)
        self._client = genai.Client()
        # Build tool declarations once — they do not change between calls.
        self._gemini_tools = self._build_gemini_tools()

    @property
    def backend_name(self) -> str:
        return "gemini"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AgentResult:
        """Execute one agent turn, handling function calls before returning.

        Calls ``generate_content`` in a loop. When the model requests function
        calls the agent executes them, wraps results in ``FunctionResponse``
        parts, and appends them as a ``user`` turn before the next call. The
        loop ends when the model produces a response without function calls or
        the retry budget (``config.max_retries``) is exhausted.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Returns:
            An ``AgentResult`` containing the final text, full message history,
            all tool calls made during the turn, token usage, and stop reason.
        """
        # Start with prior context converted to Gemini Content, then append the
        # new user prompt as the final turn before the first API call.
        contents = _messages_to_gemini(context)
        contents.append(
            genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])
        )

        accumulated_tool_calls: list[ToolCall] = []
        # Track tokens across all loop iterations so the final AgentResult
        # reflects the total cost of the entire turn, not just the last call.
        total_input_tokens = total_output_tokens = 0

        # GenerateContentConfig is built once outside the loop — the same
        # parameters are reused every iteration; only `contents` grows.
        generate_config = genai_types.GenerateContentConfig(
            system_instruction=self._build_system_prompt(),
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_tokens,
            tools=self._gemini_tools,
        )

        for _ in range(self.config.max_retries + 1):
            response = await self._client.aio.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=generate_config,
            )

            if response.usage_metadata:
                total_input_tokens += response.usage_metadata.prompt_token_count or 0
                total_output_tokens += response.usage_metadata.candidates_token_count or 0

            # Flatten function_call parts from all candidates into a single list.
            # In practice Gemini returns one candidate, but the API allows more.
            function_calls = [
                part.function_call
                for candidate in response.candidates
                for part in candidate.content.parts
                if part.function_call
            ]

            # No function calls means the model produced a final text response.
            if not function_calls:
                final_text = response.text or ""
                out_messages = list(context or [])
                out_messages.append(Message(role=Role.USER, content=prompt))
                out_messages.append(Message(role=Role.ASSISTANT, content=final_text))
                return AgentResult(
                    content=final_text,
                    messages=out_messages,
                    tool_calls=accumulated_tool_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    stop_reason=StopReason.END_TURN,
                )

            # --- function_calls branch ---
            # Append the model's turn (which contains the FunctionCall parts)
            # before adding the corresponding FunctionResponse parts. Gemini
            # requires the model turn to precede its own tool results.
            contents.append(response.candidates[0].content)

            function_response_parts = []
            for fc in function_calls:
                result_text = self._invoke_tool(fc.name, dict(fc.args))
                accumulated_tool_calls.append(
                    ToolCall(
                        id=fc.name,
                        name=fc.name,
                        arguments=dict(fc.args),
                        result=result_text,
                    )
                )
                # Wrap the result in a FunctionResponse Part so Gemini can match
                # it back to the FunctionCall by name on the next generate call.
                function_response_parts.append(
                    genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=fc.name,
                            response={"result": result_text},
                        )
                    )
                )

            # All function responses travel back as a single user-role Content
            # turn; Gemini does not support a dedicated "tool" role.
            contents.append(
                genai_types.Content(role="user", parts=function_response_parts)
            )

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

        Function calls are not handled during streaming; use ``run()`` when
        tool execution is required.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Yields:
            Text chunks in the order they are produced by the model.
        """
        contents = _messages_to_gemini(context)
        contents.append(
            genai_types.Content(role="user", parts=[genai_types.Part(text=prompt)])
        )

        # Omit tools from the streaming config — function calls cannot be
        # executed mid-stream, so including them would only waste tokens.
        generate_config = genai_types.GenerateContentConfig(
            system_instruction=self._build_system_prompt(),
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_tokens,
        )

        # generate_content_stream returns an async iterable of response chunks;
        # each chunk carries a text fragment via chunk.text.
        async for chunk in await self._client.aio.models.generate_content_stream(
            model=self.config.model,
            contents=contents,
            config=generate_config,
        ):
            if chunk.text:
                yield chunk.text

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
        """No persistent resources to release for the Gemini backend."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_gemini_tools(self) -> list[genai_types.Tool] | None:
        """Build a Gemini ``Tool`` list from the configured tools.

        Each tool is represented as a ``FunctionDeclaration`` using the tool's
        ``name``, ``description``, and ``input_schema``. If no tools are
        configured ``None`` is returned, which omits the ``tools`` field from
        the generation request entirely.

        Returns:
            A single-element list containing one ``genai_types.Tool`` with all
            function declarations, or ``None`` if ``self.tools`` is empty.
        """
        if not self.tools:
            return None
        declarations = [
            genai_types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                # Gemini calls this field "parameters"; it accepts the same
                # JSON Schema dict that we store as input_schema.
                parameters=tool.input_schema,
            )
            for tool in self.tools
        ]
        # Gemini expects all declarations wrapped in a single Tool object rather
        # than a flat list of FunctionDeclaration objects.
        return [genai_types.Tool(function_declarations=declarations)]

    def _invoke_tool(self, name: str, arguments: dict) -> str:
        """Find and call a tool by name, returning its output as a string.

        Args:
            name: The function name requested by the model.
            arguments: Key-value arguments the model passed to the function.

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
