#!/usr/bin/env python
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
"""Observe -> mine -> agentify, on a real expense-claim review workflow.

Run it from the repo root::

    uv run python examples/expense_review/run.py            # stages 1-3, no LLM
    uv run python examples/expense_review/run.py --agent    # + stage 4, live LLM

    1. OBSERVE   a reviewer works three monthly claim batches through the real
                 Excel tools. Every call is tapped into an OCEL event log.
    2. MINE      the log mines into a process model: one case per batch, the
                 activities, and the variant(s). bupaR adds a BPMN when R is
                 installed; otherwise the pure-Python variant miner runs.
    3. COMPILE   the dominant variant compiles into a ProcessPlan, rendered as an
                 agent system prompt, with the toolset cut to what it uses. Write
                 steps are flagged for approval.
    4. AGENTIFY  (--agent) a live LLM reviews a *new* batch it has never seen,
                 following the plan. Its own calls are tapped too, and its trace
                 is checked against the mined model for conformance. Needs a
                 ``.env`` with API_BASE / API_KEY / MODEL.

Outputs land in ``--out`` (default ``./expense_review_out``): the batch
workbooks, the OCEL 2.0 log, the process model, and the compiled prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The sibling module, made importable by the sys.path line above.
from workflow import (
    GOAL,
    case_traces,
    mine,
    observe,
    observed_registry,
    steady_clock,
)

from agenticharvest.core.events import OcelLog
from agenticharvest.domains.excel.samples import build_expense_workbook
from agenticharvest.domains.excel.tools import excel_tools
from agenticharvest.mining import check_trace
from agenticharvest.orchestration.compile import compile_plan, restrict_tools

# A batch the reviewer never saw — the agent's job in stage 4.
NEW_BATCH = [
    ("C-401", "Ada", "Travel", 455.00),
    ("C-402", "Lin", "Meals", 88.40),
    ("C-403", "Sam", "Supplies", 64.99),
]


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("expense_review_out"))
    parser.add_argument("--agent", action="store_true", help="run stage 4 with a live LLM")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    # 1. OBSERVE --------------------------------------------------------------
    _banner("1. OBSERVE — a reviewer works three claim batches")
    log = OcelLog()
    outcomes = observe(out, log)
    for batch, decisions in outcomes.items():
        print(f"  batch {batch}:")
        for claim_id, decision, reason in decisions:
            print(f"    {claim_id}  {decision:8}  {reason}")
    (out / "observed.ocel.json").write_text(json.dumps(log.to_ocel2(), indent=2))
    print(f"\n  {len(log.events)} tool calls recorded -> {out / 'observed.ocel.json'}")

    # 2. MINE -----------------------------------------------------------------
    _banner("2. MINE — the event log becomes a process model")
    model = mine(log)
    (out / "process_model.json").write_text(model.model_dump_json(indent=2))
    print(f"  miner: {model.miner} · cases: {model.n_cases} · events: {model.n_events}")
    for variant in model.variants:
        print(f"  variant x{variant.frequency}: {' -> '.join(variant.sequence)}")
    if model.bpmn is not None:
        print(f"  BPMN: {len(model.bpmn.nodes)} nodes, {len(model.bpmn.flows)} flows")

    # 3. COMPILE --------------------------------------------------------------
    _banner("3. COMPILE — the process becomes an agent plan")
    specs = [spec for spec, _ in excel_tools()]
    plan = compile_plan(model, specs, name="expense review")
    prompt = plan.system_prompt(GOAL)
    (out / "agent_prompt.txt").write_text(prompt)
    print(prompt)
    print(f"\n  tools granted: {plan.tools}  (of {len(specs)} available)")

    if not args.agent:
        print("\nRe-run with --agent to hand the plan to a live LLM (stage 4).")
        return 0

    # 4. AGENTIFY -------------------------------------------------------------
    _banner("4. AGENTIFY — a live agent reviews a new batch, observed and checked")
    from agenticharvest.orchestration.agents.adapters import registry_tools
    from agenticharvest.orchestration.agents.base import AgentConfig
    from agenticharvest.orchestration.agents.factory import AgentFactory, Backend
    from agenticharvest.utils.llm import LLMConfig, LLMConfigError

    try:
        llm = LLMConfig.from_env()
    except LLMConfigError as exc:
        print(f"  no LLM endpoint configured ({exc}); skipping stage 4.")
        return 1

    book = build_expense_workbook(out / "claims-2026-10.xlsm", NEW_BATCH)
    agent_log = OcelLog()
    registry = observed_registry(agent_log, clock=steady_clock())
    tools = restrict_tools(registry_tools(registry), plan)
    agent = AgentFactory.create(
        Backend.OPENAI_COMPATIBLE,
        AgentConfig(model=llm.model, system_prompt=prompt, max_tokens=2048, max_retries=20),
        tools=tools,
        llm_config=llm,
    )

    async def go() -> str:
        try:
            result = await agent.run(f"Review the claim batch in the workbook at '{book}'.")
        finally:
            await agent.close()
        return result.content

    answer = asyncio.run(go())
    print(answer)

    from workflow import decide, decisions_on_disk

    expected = {cid: decide(cat, amt)[0] for cid, _e, cat, amt in NEW_BATCH}
    got = decisions_on_disk(book)
    print("\n  decisions on disk vs. policy:")
    for claim_id, want in expected.items():
        mark = "ok " if got.get(claim_id) == want else "MISMATCH"
        print(f"    {mark} {claim_id}: wrote {got.get(claim_id)!r}, policy says {want!r}")

    for trace in case_traces(agent_log).values():
        report = check_trace(model, trace)
        print(f"\n  agent trace: {' -> '.join(trace)}")
        print(f"  conforms: {report.conforms} · fitness: {report.fitness:.2f}")
        if report.unseen_transitions:
            print(f"  deviations: {report.unseen_transitions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
