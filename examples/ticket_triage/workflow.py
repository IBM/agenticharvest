#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The ticket-triage workflow over the SQLite domain.

The same observe -> mine -> compile -> agentify loop as
``examples/expense_review``, over a different artifact entirely: a support
database instead of a workbook. Only the domain import differs — the tap, the
miners, the compiler, the conformance check, and the agent loop are shared.

The triager's process, every morning:

    1. pull the open queue          query:tickets
    2. see who can take what        query:agents
    3. route each ticket            execute:assignments   (gated write; the
                                                           log_assignment trigger
                                                           updates the ticket,
                                                           the agent, the audit)
    4. confirm nothing is left      query:tickets
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Any

from agenticharvest.core.events import OcelLog, OcelTap
from agenticharvest.core.tools import ToolRegistry
from agenticharvest.domains.sqlite.ocel import OBJ_DATABASE, SqliteOCEL
from agenticharvest.domains.sqlite.samples import TICKET_QUEUES, build_support_db
from agenticharvest.domains.sqlite.tools import sqlite_tools
from agenticharvest.gateway.dispatch import ToolRequest, dispatch
from agenticharvest.mining import (
    BupaRMiner,
    Miner,
    ProcessModel,
    VariantMiner,
    collapse_repeats,
    traces,
)

CASE_TYPE = OBJ_DATABASE

GOAL = (
    "You triage the open support tickets in a SQLite database. Assign every open "
    "ticket to the agent whose skill equals the ticket's product; among those, "
    "pick the one with the lowest open_load at the moment you assign (loads "
    "change as you go). Handle high-priority tickets first. Record each "
    "assignment with: INSERT INTO assignments (ticket_id, agent_id) VALUES (?, ?) "
    "— a trigger then updates the ticket and the agent's load. Finish by checking "
    "that no ticket is still open."
)


def observed_registry(log: OcelLog, clock: Callable[[], datetime] | None = None) -> ToolRegistry:
    """A registry over the SQLite tools whose every call is recorded into ``log``."""
    registry = ToolRegistry(
        taps=[OcelTap(mapper=SqliteOCEL(qualify_tables=True), log=log, clock=clock)]
    )
    for spec, handler in sqlite_tools():
        registry.register(spec, handler)
    return registry


def steady_clock() -> Callable[[], datetime]:
    """A clock that ticks one minute per call, so event order is unambiguous."""
    ticks = count()
    origin = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    return lambda: origin + timedelta(minutes=next(ticks))


def _call(registry: ToolRegistry, tool: str, **params: Any) -> Any:
    result = dispatch(registry, ToolRequest(tool=tool, params=params))
    if not result.ok:
        raise RuntimeError(f"{tool} failed: {result.error}")
    return result.output


_PRIORITY = {"high": 0, "normal": 1, "low": 2}


def route(
    tickets: list[tuple[int, str, str]], agents: list[tuple[int, str, int]]
) -> dict[int, int]:
    """The triage rule: high priority first, skill match, then lightest load.

    Args:
        tickets: ``(id, product, priority)`` rows still open.
        agents: ``(id, skill, open_load)`` rows.

    Returns:
        Ticket id -> agent id. Loads update as tickets are assigned.
    """
    load = {agent_id: open_load for agent_id, _skill, open_load in agents}
    skill = {agent_id: s for agent_id, s, _load in agents}
    plan: dict[int, int] = {}
    for ticket_id, product, _priority in sorted(
        tickets, key=lambda t: (_PRIORITY.get(t[2], 9), t[0])
    ):
        candidates = [a for a in load if skill[a] == product]
        if not candidates:
            continue
        chosen = min(candidates, key=lambda a: (load[a], a))
        plan[ticket_id] = chosen
        load[chosen] += 1
    return plan


def triage_by_hand(registry: ToolRegistry, db: Path) -> dict[int, int]:
    """Play the human triager for one queue, through the real tools."""
    path = str(db)
    open_rows = _call(
        registry, "query", path=path,
        sql="SELECT id, product, priority FROM tickets WHERE status = 'open'",
    )["rows"]
    agent_rows = _call(
        registry, "query", path=path, sql="SELECT id, skill, open_load FROM agents"
    )["rows"]
    plan = route([tuple(r) for r in open_rows], [tuple(r) for r in agent_rows])
    for ticket_id, agent_id in plan.items():
        _call(
            registry, "execute", path=path,
            sql="INSERT INTO assignments (ticket_id, agent_id) VALUES (?, ?)",
            args=[ticket_id, agent_id], confirm=True,
        )
    remaining = _call(
        registry, "query", path=path,
        sql="SELECT COUNT(*) FROM tickets WHERE status = 'open'",
    )["rows"][0][0]
    assert remaining == 0, f"{remaining} ticket(s) left open"
    return plan


def observe(workdir: Path, log: OcelLog) -> dict[str, dict[int, int]]:
    """Stage 1 — observe the triager across every sample queue (one case each)."""
    registry = observed_registry(log, clock=steady_clock())
    return {
        day: triage_by_hand(registry, build_support_db(workdir / f"support-{day}.db", tickets))
        for day, tickets in TICKET_QUEUES.items()
    }


def default_miner() -> Miner:
    """bupaR (which adds a BPMN) when R is installed, else the variant miner."""
    bupar = BupaRMiner(case_type=CASE_TYPE)
    return bupar if bupar.available() else VariantMiner(case_type=CASE_TYPE)


def mine(log: OcelLog, miner: Miner | None = None) -> ProcessModel:
    """Stage 2 — mine the log, folding the per-ticket assignment repeats."""
    return (miner or default_miner()).mine(collapse_repeats(log, case_type=CASE_TYPE))


def case_traces(log: OcelLog) -> dict[str, list[str]]:
    """Each queue's trace, with repeated per-ticket steps collapsed."""
    return traces(collapse_repeats(log, case_type=CASE_TYPE), case_type=CASE_TYPE)


def assignments_on_disk(db: Path) -> dict[int, int]:
    """Read back ticket id -> agent id from the assignments table."""
    import sqlite3

    connection = sqlite3.connect(db)
    try:
        return dict(connection.execute("SELECT ticket_id, agent_id FROM assignments"))
    finally:
        connection.close()
