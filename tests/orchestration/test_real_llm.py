#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Opt-in end-to-end test against a live LLM endpoint.

Deselected by default (see ``addopts`` in pyproject); run it explicitly with::

    pytest tests/orchestration/test_real_llm.py -m real_llm -v -s

It drives the whole stack for real: build the orders workbook, boot the REST app,
expose its tools to an ``OpenAICompatibleAgent`` configured from ``.env``, and ask
a question that can only be answered by calling tools. It asserts the model
actually used a tool and reached the right answer — and prints the transcript so
the run can be seen, not just trusted.

The endpoint, model, and credentials all come from ``.env`` / the environment; if
none is configured the test skips rather than fails.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi.testclient", reason="needs the 'rest' extra")
pytest.importorskip("httpx", reason="needs the 'openai_compatible' extra")
pytest.importorskip("openpyxl", reason="needs the 'excel' extra")

from fastapi.testclient import TestClient

from agenticharvest.core.tools import default_registry
from agenticharvest.domains.excel.samples import build_orders_workbook
from agenticharvest.gateway.channels.rest import create_app
from agenticharvest.orchestration.agents.adapters import rest_tools
from agenticharvest.orchestration.agents.base import AgentConfig
from agenticharvest.orchestration.agents.factory import AgentFactory, Backend
from agenticharvest.utils.llm import LLMConfig

pytestmark = pytest.mark.real_llm


def test_real_agent_uses_tools_to_answer(
    llm_config: LLMConfig, tmp_path: Path
) -> None:
    workbook = build_orders_workbook(tmp_path / "orders.xlsm")
    client = TestClient(create_app(default_registry()))
    tools = rest_tools(client)

    agent = AgentFactory.create(
        Backend.OPENAI_COMPATIBLE,
        AgentConfig(
            model=llm_config.model,
            system_prompt=(
                "You inspect a spreadsheet workbook through the provided tools. "
                "Use them to answer, then state the answer plainly."
            ),
            max_tokens=1024,
        ),
        tools=tools,
        llm_config=llm_config,
    )

    question = (
        f"The workbook is at '{workbook}'. Which sheet does the WriteTotals macro "
        f"write to, and which macro does it call to compute the value? "
        f"Use the tools to find out."
    )

    async def go() -> None:
        try:
            result = await agent.run(question)
        finally:
            await agent.close()

        print("\n--- tool calls ---")
        for call in result.tool_calls:
            print(f"  {call.name}({call.arguments}) -> {call.result}")
        print("--- final answer ---")
        print(result.content)

        assert result.tool_calls, "model answered without calling any tool"
        assert "Summary" in result.content
        assert "ComputeTotal" in result.content

    asyncio.run(go())
