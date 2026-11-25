#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""End-to-end: an actual Excel file -> a real LLM query -> a generated report.

This is the whole stack exercised the way a user would drive it, against a
genuine macro-enabled workbook:

    .xlsm on disk
      -> Excel domain tools (list_sheets / list_macros / call graph / data flow)
      -> gateway dispatch -> REST channel
      -> OpenAICompatibleAgent (a real model, from .env)
      -> a Markdown report written to disk.

Point it at your own workbook by exporting ``AGENTICHARVEST_TEST_XLSM=/path/to/book.xlsm``;
otherwise it builds the synthesized "orders" demo workbook, which is itself a
real ``.xlsm`` (macros and all). The generated report is written under the
test's ``tmp_path`` and printed, so the run produces a visible artifact rather
than only a pass/fail.

Marked ``real_llm`` and deselected by default; run it explicitly::

    pytest tests/orchestration/test_end_to_end.py -m real_llm -v -s
"""

from __future__ import annotations

import asyncio
import os
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


@pytest.fixture()
def workbook(tmp_path: Path) -> Path:
    """An actual .xlsm to analyze: your own via env, else the demo workbook."""
    override = os.environ.get("AGENTICHARVEST_TEST_XLSM")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            pytest.skip(f"AGENTICHARVEST_TEST_XLSM does not exist: {path}")
        return path
    return build_orders_workbook(tmp_path / "orders.xlsm")


def test_generate_workbook_report(
    llm_config: LLMConfig, workbook: Path, tmp_path: Path
) -> None:
    client = TestClient(create_app(default_registry()))
    tools = rest_tools(client)

    agent = AgentFactory.create(
        Backend.OPENAI_COMPATIBLE,
        AgentConfig(
            model=llm_config.model,
            system_prompt=(
                "You are a spreadsheet analyst. Inspect the workbook only through "
                "the provided tools, then write a concise Markdown report. Do not "
                "invent details that the tools did not return."
            ),
            max_tokens=1500,
        ),
        tools=tools,
        llm_config=llm_config,
    )

    query = (
        f"The workbook is at '{workbook}'. Using the tools, produce a short "
        f"Markdown report covering: (1) each sheet and whether it is an input or "
        f"an output, (2) the macros and which are event handlers, (3) the "
        f"step-by-step workflow starting from Workbook_Open, and (4) which sheet "
        f"the final results are written to. Use only what the tools return."
    )

    report_path = tmp_path / "workbook_report.md"

    async def go() -> None:
        try:
            result = await agent.run(query)
        finally:
            await agent.close()

        report_path.write_text(
            f"# Workbook analysis: {workbook.name}\n\n"
            f"_Model: {llm_config.model} · tools used: "
            f"{', '.join(c.name for c in result.tool_calls) or 'none'}_\n\n"
            f"{result.content}\n",
            encoding="utf-8",
        )

        print("\n=== tool calls the model made ===")
        for call in result.tool_calls:
            print(f"  {call.name}({call.arguments})")
        print(f"\n=== generated report written to {report_path} ===\n")
        print(report_path.read_text(encoding="utf-8"))

        # The model actually used the tools, and a non-trivial report was written.
        assert result.tool_calls, "model answered without inspecting the workbook"
        assert report_path.is_file()
        assert len(report_path.read_text(encoding="utf-8")) > 200
        # When run against the demo workbook, the known facts must appear.
        if os.environ.get("AGENTICHARVEST_TEST_XLSM") is None:
            report = report_path.read_text(encoding="utf-8")
            assert "Summary" in report
            assert "Workbook_Open" in report

    asyncio.run(go())
