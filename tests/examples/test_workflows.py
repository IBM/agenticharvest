#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Keep the runnable examples honest: run their observe -> mine -> compile stages.

Each example ships a ``workflow.py`` with the stages as functions and a
``run.py`` that prints them. These tests import the former and assert what the
latter claims — the reviewer/triager decisions follow the rule, the process mines
into one variant per sample run, and the compiled plan names the right steps and
gates the write. The live-LLM stage is covered by the opt-in ``real_llm`` test at
the bottom.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agenticharvest.core.events import OcelLog
from agenticharvest.mining import VariantMiner, check_trace
from agenticharvest.orchestration.compile import compile_plan

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _load(example: str) -> ModuleType:
    """Import ``examples/<example>/workflow.py`` under a unique module name."""
    name = f"_example_{example}_workflow"
    spec = importlib.util.spec_from_file_location(name, _EXAMPLES / example / "workflow.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------
# Expense review (Excel domain)
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def expense() -> ModuleType:
    pytest.importorskip("openpyxl", reason="the expense example needs the 'excel' extra")
    pytest.importorskip("oletools", reason="the expense example needs the 'excel' extra")
    return _load("expense_review")


def test_expense_reviewer_applies_the_policy(expense: ModuleType, tmp_path: Path) -> None:
    log = OcelLog()
    outcomes = expense.observe(tmp_path, log)

    decisions = {cid: d for batch in outcomes.values() for cid, d, _r in batch}
    assert decisions["C-101"] == "approved"  # 42.50 Meals, limit 75
    assert decisions["C-102"] == "flagged"  # 640 Travel, limit 500
    assert decisions["C-301"] == "flagged"  # 275 Lodging, limit 250
    # What the reviewer decided is really on disk.
    assert expense.decisions_on_disk(tmp_path / "claims-2026-08.xlsm") == {
        "C-201": "approved",
        "C-202": "flagged",
        "C-203": "approved",
        "C-204": "flagged",
    }


def test_expense_process_mines_and_compiles(expense: ModuleType, tmp_path: Path) -> None:
    from agenticharvest.domains.excel.tools import excel_tools

    log = OcelLog()
    expense.observe(tmp_path, log)
    model = expense.mine(log, VariantMiner(case_type=expense.CASE_TYPE))

    assert model.n_cases == 3
    assert [(v.sequence, v.frequency) for v in model.variants] == [
        (["read_range:Claims", "read_range:Policy", "get_data_flow", "write_cell:Decisions"], 3)
    ]

    plan = compile_plan(model, [s for s, _ in excel_tools()])
    assert plan.coverage == 1.0
    assert plan.tools == ["read_range", "get_data_flow", "write_cell"]
    assert [s.requires_approval for s in plan.steps] == [False, False, False, True]

    for trace in expense.case_traces(log).values():
        assert check_trace(model, trace).conforms


def test_expense_decide_rule() -> None:
    expense = _load("expense_review")
    assert expense.decide("Meals", 75.0)[0] == "approved"  # at the limit
    assert expense.decide("Meals", 75.01)[0] == "flagged"
    assert expense.decide("Gifts", 1.0)[0] == "flagged"  # no policy


# ----------------------------------------------------------------------
# Ticket triage (SQLite domain)
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def triage() -> ModuleType:
    return _load("ticket_triage")


def test_triage_routes_by_skill_load_and_priority(triage: ModuleType) -> None:
    agents = [(1, "billing", 2), (2, "billing", 0), (3, "api", 1)]
    tickets = [(10, "billing", "low"), (11, "billing", "high"), (12, "api", "normal")]
    # High priority goes first and takes the idle agent; the next billing ticket
    # then sees agent 2 at load 1, still below agent 1's 2.
    assert triage.route(tickets, agents) == {11: 2, 10: 2, 12: 3}
    # A product nobody covers stays unassigned.
    assert triage.route([(13, "mobile", "high")], agents) == {}


def test_triage_process_mines_and_compiles(triage: ModuleType, tmp_path: Path) -> None:
    from agenticharvest.domains.sqlite.tools import sqlite_tools

    log = OcelLog()
    plans = triage.observe(tmp_path, log)
    assert plans["monday"] == {1001: 2, 1002: 5, 1003: 3}
    assert triage.assignments_on_disk(tmp_path / "support-monday.db") == plans["monday"]

    model = triage.mine(log, VariantMiner(case_type=triage.CASE_TYPE))
    assert model.n_cases == 3
    assert [(v.sequence, v.frequency) for v in model.variants] == [
        (["query:tickets", "query:agents", "execute:assignments", "query:tickets"], 3)
    ]

    plan = compile_plan(model, [s for s, _ in sqlite_tools()])
    assert plan.tools == ["query", "execute"]
    assert [s.requires_approval for s in plan.steps] == [False, False, True, False]


# ----------------------------------------------------------------------
# The live agent stage (opt-in)
# ----------------------------------------------------------------------


@pytest.mark.real_llm
def test_triage_agent_follows_the_mined_process(triage: ModuleType, tmp_path: Path) -> None:
    pytest.importorskip("httpx", reason="needs the 'openai_compatible' extra")
    from agenticharvest.domains.sqlite.samples import AGENTS, build_support_db
    from agenticharvest.domains.sqlite.tools import sqlite_tools
    from agenticharvest.orchestration.agents.adapters import registry_tools
    from agenticharvest.orchestration.agents.base import AgentConfig
    from agenticharvest.orchestration.agents.factory import AgentFactory, Backend
    from agenticharvest.orchestration.compile import restrict_tools
    from agenticharvest.utils.llm import LLMConfig, LLMConfigError

    try:
        llm = LLMConfig.from_env()
    except LLMConfigError as exc:
        pytest.skip(f"No LLM endpoint configured: {exc}")

    log = OcelLog()
    triage.observe(tmp_path / "observed", log)
    model = triage.mine(log)
    plan = compile_plan(model, [s for s, _ in sqlite_tools()], name="ticket triage")

    queue = [(9001, "api", "high", "Auth tokens expire early"), (9002, "billing", "low", "Receipt")]
    db = build_support_db(tmp_path / "new.db", queue)
    agent_log = OcelLog()
    registry = triage.observed_registry(agent_log, clock=triage.steady_clock())
    agent = AgentFactory.create(
        Backend.OPENAI_COMPATIBLE,
        AgentConfig(model=llm.model, system_prompt=plan.system_prompt(triage.GOAL),
                    max_tokens=2048, max_retries=20),
        tools=restrict_tools(registry_tools(registry), plan),
        llm_config=llm,
    )

    async def go() -> None:
        try:
            await agent.run(f"Triage the open tickets in the database at '{db}'.")
        finally:
            await agent.close()

    asyncio.run(go())

    expected = triage.route(
        [(t, p, pr) for t, p, pr, _s in queue], [(a, s, ld) for a, _n, s, ld in AGENTS]
    )
    assert triage.assignments_on_disk(db) == expected
    (trace,) = triage.case_traces(agent_log).values()
    assert check_trace(model, trace).fitness >= 0.5
