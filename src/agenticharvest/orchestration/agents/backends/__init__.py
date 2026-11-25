#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Concrete agent backends.

Each module here implements ``AbstractAgent`` against one provider/framework and
is imported lazily by ``AgentFactory`` so its dependency is only required when
that backend is used:

    claude      Anthropic Messages API (direct)
    langchain   LangChain ChatAnthropic with tool binding
    langgraph   LangGraph ReAct StateGraph
    gemini      Google Gemini via google-genai

All four handle the tool-use loop internally and return a uniform ``AgentResult``.
"""
