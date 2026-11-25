#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""LangChain agent backend.

Uses ``langchain_anthropic.ChatAnthropic`` with LangChain's tool-calling
interface. Tools are bound as Anthropic-style schema dicts via ``bind_tools``.

The tool loop is handled manually: the agent calls ``ainvoke``, detects
``tool_calls`` on the response, executes them, appends ``ToolMessage`` results,
and calls ``ainvoke`` again until the model produces a plain text response.

An optional LiteLLM proxy is supported via ``LITELLM_API_BASE`` /
``LITELLM_API_KEY`` environment variables.
"""

from collections.abc import AsyncIterator

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

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


def _langchain_to_message(lc_msg: BaseMessage) -> Message:
    """Convert a LangChain ``BaseMessage`` to a ``Message``.

    ``SystemMessage`` has no direct equivalent and is mapped to ``USER`` so
    callers can safely round-trip any message list through this function
    without losing entries.

    Args:
        lc_msg: A LangChain message produced by the model or tool layer.

    Returns:
        The equivalent ``Message`` with role and content populated. For
        ``ToolMessage`` the originating ``tool_call_id`` is also preserved.
    """
    if isinstance(lc_msg, HumanMessage):
        return Message(role=Role.USER, content=str(lc_msg.content))
    if isinstance(lc_msg, AIMessage):
        # Preserve any tool_call requests so a later ToolMessage's tool_call_id
        # still has its matching tool_use block when this message is re-sent as
        # context; dropping them produces an orphaned tool_result (API 400).
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                arguments=tc.get("args", {}) or {},
            )
            for tc in (lc_msg.tool_calls or [])
        ]
        return Message(
            role=Role.ASSISTANT,
            content=str(lc_msg.content),
            tool_calls=tool_calls,
        )
    if isinstance(lc_msg, ToolMessage):
        return Message(
            role=Role.TOOL,
            content=str(lc_msg.content),
            tool_call_id=lc_msg.tool_call_id,
        )
    # SystemMessage and any future LangChain message types fall back to USER
    # so the list is never shortened unexpectedly.
    return Message(role=Role.USER, content=str(lc_msg.content))


def _messages_to_langchain(
    context: list[Message] | None,
) -> list[BaseMessage]:
    """Convert ``Message`` objects to LangChain ``BaseMessage`` instances.

    ``TOOL`` role messages are converted to ``ToolMessage`` using the stored
    ``tool_call_id`` so LangChain's tool-result tracking works correctly.

    Args:
        context: Conversation messages to convert. ``None`` is treated as empty.

    Returns:
        A list of LangChain ``BaseMessage`` instances in the same order as the
        input, ready to prepend before a new ``HumanMessage``.
    """
    result: list[BaseMessage] = []
    for m in (context or []):
        if m.role == Role.USER:
            result.append(HumanMessage(content=m.content))
        elif m.role == Role.ASSISTANT:
            # Re-attach any tool_call requests so the tool_use blocks are present
            # for the ToolMessages that answer them; otherwise the API rejects the
            # follow-up with an orphaned tool_result error.
            tool_calls = [
                {"id": tc.id, "name": tc.name, "args": tc.arguments}
                for tc in m.tool_calls
            ]
            result.append(AIMessage(content=m.content, tool_calls=tool_calls))
        elif m.role == Role.TOOL:
            result.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id))
    return result


class LangChainAgent(AbstractAgent):
    """Agent backend using LangChain's ``ChatAnthropic`` with tool binding.

    Tools are bound to the LLM at construction time via ``bind_tools``. During
    ``run()`` the agent detects tool calls on each response and executes them in
    a loop until the model returns a plain text response or the retry budget is
    exhausted.

    Args:
        config: Agent configuration (model, skills, sampling parameters, retry
            budget).
        tools: ``Tool`` instances made available to the model.
        callbacks: Optional LangChain callbacks list.
    """

    def __init__(
        self,
        config: AgentConfig,
        tools: list[Tool],
        callbacks: list | None = None,
    ) -> None:
        super().__init__(config, tools)
        import os
        # Support a LiteLLM proxy: if LITELLM_API_BASE is set, route through it.
        # ChatAnthropic exposes anthropic_api_url / anthropic_api_key for this.
        kwargs: dict = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            # Bound each HTTP request so a stalled proxy connection cannot hang the
            # run forever, and let the SDK retry transient timeouts / 429 / 5xx.
            "timeout": config.timeout_seconds,
            "max_retries": DEFAULT_HTTP_RETRIES,
        }
        api_base = os.environ.get("LITELLM_API_BASE", "").strip()
        api_key = os.environ.get("LITELLM_API_KEY", "").strip()
        if api_base:
            kwargs["anthropic_api_url"] = api_base
        if api_key:
            kwargs["anthropic_api_key"] = api_key
        if callbacks:
            kwargs["callbacks"] = callbacks
        llm = ChatAnthropic(**kwargs)
        # bind_tools accepts Anthropic-style schema dicts directly, so we pass
        # the schema() output rather than BaseTool objects.
        self._llm = llm.bind_tools([t.schema() for t in tools]) if tools else llm

    @property
    def backend_name(self) -> str:
        return "langchain"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AgentResult:
        """Execute one agent turn, handling tool calls before returning.

        Builds a LangChain message list from the system prompt, prior context,
        and the new prompt, then calls ``ainvoke`` in a loop. When the model
        requests tools they are executed and their results are appended as
        ``ToolMessage`` entries before the next ``ainvoke`` call. The loop ends
        when the model produces a response without tool calls or the retry
        budget is exhausted.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Returns:
            An ``AgentResult`` containing the final text, message history
            (excluding the ``SystemMessage``), all tool calls made during the
            turn, token usage, and stop reason.
        """
        # SystemMessage is always first — LangChain keeps it separate from the
        # conversation turns so it is not overwritten by tool-loop appends.
        lc_messages: list[BaseMessage] = [
            SystemMessage(content=self._build_system_prompt()),
            *_messages_to_langchain(context),
            HumanMessage(content=prompt),
        ]

        accumulated_tool_calls: list[ToolCall] = []
        # Ids of ToolMessages in the most recently dispatched batch. Only the
        # current batch is kept in full; previous batches are truncated before
        # the next LLM call so history does not grow linearly with tool turns.
        _current_batch_ids: set = set()

        for _ in range(self.config.max_retries + 1):
            # Truncate tool results from all prior batches. The model already
            # acted on those results; re-sending them in full inflates input
            # tokens on every subsequent turn.
            for msg in lc_messages:
                if (
                    isinstance(msg, ToolMessage)
                    and msg.tool_call_id not in _current_batch_ids
                    and len(msg.content) > 120
                ):
                    msg.content = msg.content[:120] + " …[truncated]"

            response: AIMessage = await self._llm.ainvoke(lc_messages)

            # No tool_calls on the response means the model is done — extract
            # the final text and build the output message list.
            if not response.tool_calls:
                # lc_messages[1:] skips the leading SystemMessage, which has no
                # Role equivalent, before converting back to Messages.
                out_messages = [_langchain_to_message(m) for m in lc_messages[1:]]
                final_text = self._extract_text(response)
                out_messages.append(
                    Message(role=Role.ASSISTANT, content=final_text)
                )
                usage = response.usage_metadata or {}
                return AgentResult(
                    content=final_text,
                    messages=out_messages,
                    tool_calls=accumulated_tool_calls,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    stop_reason=StopReason.END_TURN,
                )

            # --- tool_calls branch ---
            # Append the AIMessage that contains the tool_call requests so the
            # subsequent ToolMessages can reference them by id.
            lc_messages.append(response)
            _current_batch_ids = {tc["id"] for tc in response.tool_calls}
            for tc_dict in response.tool_calls:
                result_text = self._invoke_tool(tc_dict["name"], tc_dict["args"])
                accumulated_tool_calls.append(
                    ToolCall(
                        id=tc_dict["id"],
                        name=tc_dict["name"],
                        arguments=tc_dict["args"],
                        result=result_text,
                    )
                )
                lc_messages.append(
                    ToolMessage(content=result_text, tool_call_id=tc_dict["id"])
                )

        # Tool-call budget exhausted without a final text response. Return the
        # conversation gathered so far so callers can re-prompt against it rather
        # than restart from scratch.
        out_messages = [_langchain_to_message(m) for m in lc_messages[1:]]
        return AgentResult(
            content="",
            messages=out_messages,
            tool_calls=accumulated_tool_calls,
            stop_reason=StopReason.ERROR,
        )

    async def stream(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AsyncIterator[str]:
        """Stream response text chunks as they are produced by the model.

        Tool calls are not handled during streaming; use ``run()`` when tool
        execution is required.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Yields:
            Text chunks in the order they are produced by the model.
        """
        lc_messages: list[BaseMessage] = [
            SystemMessage(content=self._build_system_prompt()),
            *_messages_to_langchain(context),
            HumanMessage(content=prompt),
        ]
        # astream yields AIMessageChunk objects; chunk.content holds the text
        # fragment for that delta — skip empty chunks from metadata-only deltas.
        async for chunk in self._llm.astream(lc_messages):
            if chunk.content:
                yield str(chunk.content)

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
        """No persistent resources to release for the LangChain backend."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(response: "AIMessage") -> str:
        """Return the assistant's text, tolerant of langchain content shapes.

        langchain-core 1.x may deliver ``response.content`` as a list of content
        blocks (``[{"type": "text", "text": "..."}, ...]``) rather than a plain
        string. Calling ``str()`` on that list would yield a Python-repr, not the
        text the caller needs, so prefer the normalized ``.text`` property when it
        is available and fall back to joining any text blocks by hand.

        Args:
            response: The AIMessage returned by ``ainvoke``.

        Returns:
            The concatenated text content, or an empty string if none is present.
        """
        # ``.text`` (property in 1.x) already flattens content blocks to a
        # string. In 1.x it is a str subclass that is *also* callable for
        # back-compat, so check for a string first and use it directly — calling
        # it would emit a deprecation warning on every turn.
        text = getattr(response, "text", None)
        if isinstance(text, str):
            if text:
                return text
        elif callable(text):  # 0.3.x exposed text() as a method only
            called = text()
            if isinstance(called, str) and called:
                return called
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "".join(parts)
        return ""

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
