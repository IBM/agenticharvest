#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Orchestration — bring-your-own agent framework.

Glue that drives the agent loop from a compiled process model, using tools from
the gateway. AgenticHarvest does not own the orchestrator: agents here run against any
provider backend, and the mined process model can seed the prompts and task
decomposition.

Subpackages and modules:
    agents      a provider-agnostic agent framework:
                    base      AbstractAgent + Message / Tool / Skill / config
                    factory   create an agent for a chosen backend
                    runner    compose agents (sequential / parallel /
                              orchestrator-worker / reflection)
                    backends  claude, langchain, langgraph, gemini adapters
                    adapters  gateway tools -> agent Tools (in-process / REST)
    compile     process model -> ProcessPlan: the happy path, rendered as an
                agent system prompt, plus the least-privilege tool subset
"""
