#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Bring-your-own model client.

A thin, provider-agnostic layer so the agent loop in ``agenticharvest.orchestration`` can
be driven by any OpenAI-compatible provider (OpenAI, a LiteLLM proxy, a local
server, …). AgenticHarvest does not ship a model or mandate a provider; it defines the
connection config here and leaves the choice, and the credentials, to the user.

Living under ``agenticharvest.utils`` (not ``agenticharvest.core``) keeps the process-mining
backbone free of any provider concern: swapping or adding an LLM service is a
utility change, never a core change.

Public API:
    LLMConfig       api_base + api_key + model, loadable from env / .env
    LLMConfigError  raised when required configuration is missing
"""

from agenticharvest.utils.llm.config import LLMConfig, LLMConfigError

__all__ = ["LLMConfig", "LLMConfigError"]
