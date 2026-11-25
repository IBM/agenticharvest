#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Agent factory — single entry point for creating agent instances.

The factory maps a ``Backend`` value to the corresponding ``AbstractAgent``
subclass and returns a ready-to-use instance. Callers never import backend
modules directly; everything goes through ``AgentFactory.create()``.

Backends are resolved lazily so that missing optional dependencies (e.g. the
LangChain or Gemini packages) only raise an error when that specific backend
is requested, not on import.

Usage::

    from agenticharvest.orchestration.agents.factory import AgentFactory, Backend
    from agenticharvest.orchestration.agents.base import AgentConfig

    config = AgentConfig(
        model="claude-sonnet-4-6",
        system_prompt="Answer questions about the workbook.",
    )

    agent = AgentFactory.create(Backend.CLAUDE, config, tools=[])

    result = await agent.run("Summarize the Q3 sheet.")
    await agent.close()
"""

from enum import Enum
from typing import TYPE_CHECKING

from agenticharvest.orchestration.agents.base import AbstractAgent, AgentConfig, Tool

if TYPE_CHECKING:
    from agenticharvest.utils.llm import LLMConfig


class Backend(str, Enum):
    """Supported agent backends.

    Values:
        CLAUDE: Anthropic Claude via the Anthropic Messages API.
        LANGCHAIN: LangChain ChatAnthropic with tool binding.
        LANGGRAPH: LangGraph ReAct StateGraph (agent + tools loop).
        GEMINI: Google Gemini via the ``google-genai`` SDK.
        OPENAI_COMPATIBLE: Any OpenAI-compatible Chat Completions endpoint
            (OpenAI, a LiteLLM proxy, a local server) over raw HTTP.
    """

    CLAUDE            = "claude"
    LANGCHAIN         = "langchain"
    LANGGRAPH         = "langgraph"
    GEMINI            = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"


class AgentFactory:
    """Creates ``AbstractAgent`` instances from a backend identifier and config.

    All backend modules are imported lazily inside ``create()`` so that packages
    for unused backends do not need to be installed unless that backend is
    actually requested.
    """

    @staticmethod
    def create(
        backend: Backend,
        config: AgentConfig,
        tools: list[Tool],
        callbacks: list | None = None,
        llm_config: "LLMConfig | None" = None,
    ) -> AbstractAgent:
        """Instantiate and return an agent for the given backend.

        Args:
            backend: Which backend implementation to use.
            config: Configuration shared by all backends (model, system prompt,
                skills, sampling parameters).
            tools: Tools to make available to the agent.
            callbacks: Optional backend callbacks (currently used by LangChain).
            llm_config: Endpoint connection settings, required by the
                ``OPENAI_COMPATIBLE`` backend and ignored by the others.

        Returns:
            A concrete ``AbstractAgent`` instance backed by the requested backend.

        Raises:
            ImportError: If the backend's package dependencies are not installed.
            ValueError: If ``backend`` is not a recognized ``Backend`` value, or a
                backend is missing configuration it requires.
        """
        if backend == Backend.CLAUDE:
            from agenticharvest.orchestration.agents.backends.claude import ClaudeAgent
            return ClaudeAgent(config, tools)

        if backend == Backend.LANGCHAIN:
            from agenticharvest.orchestration.agents.backends.langchain import LangChainAgent
            return LangChainAgent(config, tools, callbacks=callbacks)

        if backend == Backend.LANGGRAPH:
            from agenticharvest.orchestration.agents.backends.langgraph import LangGraphAgent
            return LangGraphAgent(config, tools)

        if backend == Backend.GEMINI:
            from agenticharvest.orchestration.agents.backends.gemini import GeminiAgent
            return GeminiAgent(config, tools)

        if backend == Backend.OPENAI_COMPATIBLE:
            if llm_config is None:
                raise ValueError(
                    "The 'openai_compatible' backend requires an llm_config "
                    "(e.g. LLMConfig.from_env())."
                )
            from agenticharvest.orchestration.agents.backends.openai_compatible import (
                OpenAICompatibleAgent,
            )
            return OpenAICompatibleAgent(config, tools, llm_config)

        raise ValueError(f"Unknown backend: '{backend}'.")
