#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The agent layer — provider-agnostic agents over AgenticHarvest's tools.

A small, backend-neutral agent framework:

    base        the AbstractAgent interface + Message / ToolCall / Tool / Skill
                / AgentConfig / AgentResult data models
    factory     create an agent for a chosen backend (lazy imports)
    runner      compose agents by pattern (sequential / parallel /
                orchestrator-worker / reflection)
    backends    concrete provider adapters (claude, langchain, langgraph, gemini)
    adapters    wrap gateway tools as agent ``Tool``s, in-process
                (``registry_tools``) or over REST (``rest_tools``)

The agent-facing ``Tool`` here is the model-callable capability. AgenticHarvest domain
tools (from ``agenticharvest.core.tools``) adapt onto it via ``adapters``, so an agent can drive any
domain — Excel today, anything registered tomorrow — through the same loop. The
whole layer is provider-agnostic: pick a backend at ``AgentFactory.create``.
"""
