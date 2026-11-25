#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""LangGraph agent backend.

Builds a ``StateGraph`` with two nodes — ``agent`` and ``tools`` — connected
by a conditional edge that loops through tool calls until the model produces a
final text response. This is the standard LangGraph ReAct pattern.

Tools are ``Tool`` instances from ``base``; a manual dispatch node executes
them so no LangChain ``BaseTool`` dependency is required.
"""

import operator
from collections.abc import AsyncIterator
from typing import Annotated, Any, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, StateGraph

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


class _GraphState(TypedDict):
    """Internal state threaded through every node in the ReAct graph.

    Attributes:
        messages: Accumulated LangChain messages for the current turn. The
            ``operator.add`` reducer appends new messages rather than replacing
            the list, so each node only needs to return the messages it adds.
    """

    messages: Annotated[list[BaseMessage], operator.add]


def _messages_to_langchain(
    context: list[Message] | None,
) -> list[BaseMessage]:
    """Convert ``Message`` objects to LangChain ``BaseMessage`` instances.

    ``TOOL`` role messages are converted to ``ToolMessage`` using the stored
    ``tool_call_id`` so LangGraph's tool-result tracking works correctly.

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
            # for the ToolMessages that answer them; an orphaned tool_result is a
            # hard API error on the next call.
            tool_calls = [
                {"id": tc.id, "name": tc.name, "args": tc.arguments}
                for tc in m.tool_calls
            ]
            result.append(AIMessage(content=m.content, tool_calls=tool_calls))
        elif m.role == Role.TOOL:
            result.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id))
    return result


class LangGraphAgent(AbstractAgent):
    """Agent backend using a LangGraph ReAct ``StateGraph``.

    The compiled graph loops between two nodes:

    - ``agent``: calls the LLM with the current message list.
    - ``tools``: executed only when the LLM response contains tool calls;
      results are appended to the state before the next ``agent`` call.

    The graph terminates when the LLM produces a response without tool calls.
    If no tools are configured the ``tools`` node is omitted and the graph runs
    as a single-node pass-through.

    Args:
        config: Agent configuration (model, skills, sampling parameters).
        tools: ``Tool`` instances made available to the model.
            An empty list produces a plain LLM graph with no tools.
    """

    def __init__(self, config: AgentConfig, tools: list[Tool]) -> None:
        super().__init__(config, tools)
        llm = ChatAnthropic(
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        # bind_tools accepts Anthropic-style schema dicts directly.
        self._llm_with_tools = llm.bind_tools([t.schema() for t in tools]) if tools else llm
        # Build a lookup dict for fast dispatch in the tools node.
        self._tool_map = {t.name: t for t in tools}
        # Compile the graph once at construction — compilation is expensive and
        # the topology does not change between runs.
        self._graph = self._build_graph()

    @property
    def backend_name(self) -> str:
        return "langgraph"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AgentResult:
        """Run the ReAct graph until the model produces a final text response.

        Invokes the compiled ``StateGraph`` with the system prompt, prior
        context, and the new prompt as the initial message list. The graph
        handles the tool-call loop internally; this method extracts the final
        text and all tool calls from the finished state.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Returns:
            An ``AgentResult`` containing the final text, summarized message
            history, all tool calls made during the graph run, token usage from
            the last model call, and stop reason.
        """
        # Build the initial state: system instruction first, then prior context,
        # then the new prompt. LangGraph threads this list through every node.
        initial_messages: list[BaseMessage] = [
            SystemMessage(content=self._build_system_prompt()),
            *_messages_to_langchain(context),
            HumanMessage(content=prompt),
        ]

        # ainvoke runs the graph to completion and returns the final state dict.
        final_state = await self._graph.ainvoke({"messages": initial_messages})
        # The "messages" key in the final state holds ALL messages accumulated
        # across every node visit — including intermediate tool-call turns.
        graph_messages: list[BaseMessage] = final_state["messages"]

        # Walk the full graph message list to collect every tool call and find
        # the last assistant reply that has no tool_calls (the final text turn).
        accumulated_tool_calls: list[ToolCall] = []
        final_text = ""
        for msg in graph_messages:
            if isinstance(msg, AIMessage):
                for tc_dict in (msg.tool_calls or []):
                    accumulated_tool_calls.append(
                        ToolCall(
                            id=tc_dict["id"],
                            name=tc_dict["name"],
                            arguments=tc_dict["args"],
                        )
                    )
                # Only the AIMessage without tool_calls is the final reply.
                if msg.content and not msg.tool_calls:
                    final_text = str(msg.content)

        # Reconstruct a clean message list — callers expect context + new prompt
        # + assistant reply, not the raw graph state with tool turns.
        out_messages = list(context or [])
        out_messages.append(Message(role=Role.USER, content=prompt))
        out_messages.append(Message(role=Role.ASSISTANT, content=final_text))

        # Token usage lives on the last AIMessage in the graph; earlier turns
        # (tool-call AIMessages) may also carry usage but we only report the
        # final call to avoid double-counting across loop iterations.
        last_ai = next(
            (m for m in reversed(graph_messages) if isinstance(m, AIMessage)), None
        )
        usage = (last_ai.usage_metadata or {}) if last_ai else {}

        return AgentResult(
            content=final_text,
            messages=out_messages,
            tool_calls=accumulated_tool_calls,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            stop_reason=StopReason.END_TURN,
        )

    async def stream(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AsyncIterator[str]:
        """Stream text chunks produced by the ``agent`` node.

        Uses ``astream_events`` with the v2 event schema. Only
        ``on_chat_model_stream`` events are yielded; tool-execution events
        are suppressed so callers receive a clean text stream.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages provided as read-only context.

        Yields:
            Text chunks in the order they are produced by the model.
        """
        initial_messages: list[BaseMessage] = [
            SystemMessage(content=self._build_system_prompt()),
            *_messages_to_langchain(context),
            HumanMessage(content=prompt),
        ]

        # astream_events emits one event per token / node transition.
        # "on_chat_model_stream" fires for each text delta from the LLM;
        # other events (node start/end, tool calls) are ignored here.
        async for event in self._graph.astream_events(
            {"messages": initial_messages}, version="v2"
        ):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
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
        """No persistent resources to release for the LangGraph backend."""

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> Any:
        """Compile the ReAct ``StateGraph`` for this agent instance.

        Creates an ``agent`` node backed by ``self._llm_with_tools`` and, when
        tools are present, a ``tools`` node that dispatches to ``self._tool_map``.
        A conditional edge on the ``agent`` node routes to ``tools`` when the
        latest message contains tool calls, and to ``END`` otherwise.

        Returns:
            A compiled LangGraph state graph ready for ``ainvoke`` and
            ``astream_events``.
        """
        async def agent_node(state: _GraphState) -> dict:
            response = await self._llm_with_tools.ainvoke(state["messages"])
            return {"messages": [response]}

        async def tools_node(state: _GraphState) -> dict:
            last: AIMessage = state["messages"][-1]
            result_messages = []
            for tc in (last.tool_calls or []):
                tool = self._tool_map.get(tc["name"])
                if tool is None:
                    result = f"Unknown tool: {tc['name']}"
                else:
                    try:
                        result = tool(tc["args"])
                    except Exception as exc:  # noqa: BLE001
                        result = f"Tool error: {exc}"
                result_messages.append(
                    ToolMessage(content=result, tool_call_id=tc["id"])
                )
            return {"messages": result_messages}

        def should_continue(state: _GraphState) -> str:
            last = state["messages"][-1]
            if isinstance(last, AIMessage) and last.tool_calls:
                return "tools"
            return str(END)

        graph = StateGraph(_GraphState)
        graph.add_node("agent", agent_node)
        graph.set_entry_point("agent")

        if self._tool_map:
            graph.add_node("tools", tools_node)
            graph.add_conditional_edges(
                "agent", should_continue, {"tools": "tools", END: END}
            )
            graph.add_edge("tools", "agent")
        else:
            graph.add_edge("agent", END)

        return graph.compile()
