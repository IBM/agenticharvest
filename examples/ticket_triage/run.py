#!/usr/bin/env python
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
"""Observe -> mine -> agentify, on support-ticket triage over a SQLite database.

Run it from the repo root (no extra needed — SQLite is in the standard library)::

    uv run python examples/ticket_triage/run.py            # stages 1-3, no LLM
    uv run python examples/ticket_triage/run.py --agent    # + stage 4, live LLM

The same four stages as ``examples/expense_review``, over a different domain:

    1. OBSERVE   a triager routes three mornings of tickets through the SQLite
                 tools; a trigger maintains loads and the audit trail.
    2. MINE      the log mines into a process: one case per database (queue).
    3. COMPILE   the dominant variant becomes an agent plan and toolset.
    4. AGENTIFY  (--agent) a live LLM triages a new queue; its assignments are
                 checked against the routing rule and its trace for conformance.
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
    assignments_on_disk,
    case_traces,
    mine,
    observe,
    observed_registry,
    route,
    steady_clock,
)

from agenticharvest.core.events import OcelLog
from agenticharvest.domains.sqlite.samples import AGENTS, build_support_db
from agenticharvest.domains.sqlite.tools import sqlite_tools
from agenticharvest.mining import check_trace
from agenticharvest.orchestration.compile import compile_plan, restrict_tools

# A queue the triager never saw — the agent's job in stage 4.
NEW_QUEUE = [
    (4001, "api", "normal", "Rate-limit headers missing"),
    (4002, "billing", "high", "Card declined but order went through"),
    (4003, "mobile", "normal", "Push notifications arrive twice"),
    (4004, "billing", "normal", "Need a VAT invoice"),
]


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("ticket_triage_out"))
    parser.add_argument("--agent", action="store_true", help="run stage 4 with a live LLM")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    _banner("1. OBSERVE — a triager routes three mornings of tickets")
    log = OcelLog()
    for day, plan in observe(out, log).items():
        routed = ", ".join(f"#{t}->agent {a}" for t, a in plan.items())
        print(f"  {day:9}  {routed}")
    (out / "observed.ocel.json").write_text(json.dumps(log.to_ocel2(), indent=2))
    print(f"\n  {len(log.events)} tool calls recorded -> {out / 'observed.ocel.json'}")

    _banner("2. MINE — the event log becomes a process model")
    model = mine(log)
    (out / "process_model.json").write_text(model.model_dump_json(indent=2))
    print(f"  miner: {model.miner} · cases: {model.n_cases} · events: {model.n_events}")
    for variant in model.variants:
        print(f"  variant x{variant.frequency}: {' -> '.join(variant.sequence)}")
    if model.bpmn is not None:
        print(f"  BPMN: {len(model.bpmn.nodes)} nodes, {len(model.bpmn.flows)} flows")

    _banner("3. COMPILE — the process becomes an agent plan")
    specs = [spec for spec, _ in sqlite_tools()]
    plan = compile_plan(model, specs, name="ticket triage")
    prompt = plan.system_prompt(GOAL)
    (out / "agent_prompt.txt").write_text(prompt)
    print(prompt)
    print(f"\n  tools granted: {plan.tools}  (of {len(specs)} available)")

    if not args.agent:
        print("\nRe-run with --agent to hand the plan to a live LLM (stage 4).")
        return 0

    _banner("4. AGENTIFY — a live agent triages a new queue, observed and checked")
    from agenticharvest.orchestration.agents.adapters import registry_tools
    from agenticharvest.orchestration.agents.base import AgentConfig
    from agenticharvest.orchestration.agents.factory import AgentFactory, Backend
    from agenticharvest.utils.llm import LLMConfig, LLMConfigError

    try:
        llm = LLMConfig.from_env()
    except LLMConfigError as exc:
        print(f"  no LLM endpoint configured ({exc}); skipping stage 4.")
        return 1

    db = build_support_db(out / "support-thursday.db", NEW_QUEUE)
    agent_log = OcelLog()
    registry = observed_registry(agent_log, clock=steady_clock())
    agent = AgentFactory.create(
        Backend.OPENAI_COMPATIBLE,
        AgentConfig(model=llm.model, system_prompt=prompt, max_tokens=2048, max_retries=20),
        tools=restrict_tools(registry_tools(registry), plan),
        llm_config=llm,
    )

    async def go() -> str:
        try:
            result = await agent.run(f"Triage the open tickets in the database at '{db}'.")
        finally:
            await agent.close()
        return result.content

    print(asyncio.run(go()))

    expected = route(
        [(t, product, priority) for t, product, priority, _s in NEW_QUEUE],
        [(a, skill, load) for a, _n, skill, load in AGENTS],
    )
    got = assignments_on_disk(db)
    print("\n  assignments on disk vs. routing rule:")
    for ticket_id, want in expected.items():
        mark = "ok " if got.get(ticket_id) == want else "MISMATCH"
        print(f"    {mark} #{ticket_id}: agent {got.get(ticket_id)}, rule says agent {want}")

    for trace in case_traces(agent_log).values():
        report = check_trace(model, trace)
        print(f"\n  agent trace: {' -> '.join(trace)}")
        print(f"  conforms: {report.conforms} · fitness: {report.fitness:.2f}")
        if report.unseen_transitions:
            print(f"  deviations: {report.unseen_transitions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
