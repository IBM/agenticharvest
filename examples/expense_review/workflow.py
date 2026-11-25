#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The expense-review workflow: observe a person, mine the process, agentify it.

Shared by the runnable scripts in this directory and by the test that keeps them
honest (``tests/examples/test_expense_review.py``). Nothing here is Excel-only
except the workbook itself: the observation, mining, compilation, and
conformance steps are AgenticHarvest's generic layers.

The business process is the one a finance reviewer follows every month:

    1. look at the claim batch            read_range:Claims
    2. look up the expense policy         read_range:Policy
    3. see which macros record outcomes   get_data_flow (ApproveClaims)
    4. record each claim's decision       write_cell:Decisions   (gated write)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agenticharvest.core.events import OcelLog, OcelTap
from agenticharvest.core.tools import ToolRegistry
from agenticharvest.domains.excel.ocel import OBJ_WORKBOOK, ExcelOCEL
from agenticharvest.domains.excel.samples import (
    EXPENSE_BATCHES,
    EXPENSE_POLICY,
    build_expense_workbook,
)
from agenticharvest.domains.excel.tools import excel_tools
from agenticharvest.gateway.dispatch import ToolRequest, dispatch
from agenticharvest.mining import (
    BupaRMiner,
    Miner,
    ProcessModel,
    VariantMiner,
    collapse_repeats,
    traces,
)

# One case per claim batch: each batch lives in its own workbook. Named
# explicitly because a review touches sheets as often as the workbook, and the
# case must be the batch, not a sheet.
CASE_TYPE = OBJ_WORKBOOK

GOAL = (
    "You review an employee expense-claim batch in a workbook. Approve each "
    "claim at or under its category limit, flag each claim over it, and record "
    "every decision on the Decisions sheet. Keep its header row (row 1: "
    "ClaimID, Decision, Reason) and write one row per claim from row 2 down: "
    "the claim id in column A, 'approved' or 'flagged' in column B, and a short "
    "reason in column C."
)


def decisions_on_disk(book: Path) -> dict[str, str]:
    """Read back claim id -> decision from the Decisions sheet, by header name.

    Columns are located by their header rather than assumed positions, and the
    decision is lower-cased, so the check measures *what* was decided.
    """
    from agenticharvest.domains.excel.tools.specs import read_range

    rows = read_range({"path": str(book), "sheet": "Decisions"})["rows"]
    if not rows:
        return {}
    header = [str(h or "").strip().lower() for h in rows[0]]
    if "claimid" not in header or "decision" not in header:
        return {}
    id_col, decision_col = header.index("claimid"), header.index("decision")
    return {
        str(row[id_col]): str(row[decision_col]).strip().lower()
        for row in rows[1:]
        if len(row) > max(id_col, decision_col) and row[id_col]
    }


def observed_registry(log: OcelLog, clock: Callable[[], datetime] | None = None) -> ToolRegistry:
    """A registry over the Excel tools whose every call is recorded into ``log``.

    Activities are sheet-qualified (``read_range:Policy``), so the mined process
    reads as business steps rather than a list of tool names.
    """
    registry = ToolRegistry(
        taps=[OcelTap(mapper=ExcelOCEL(qualify_sheets=True), log=log, clock=clock)]
    )
    for spec, handler in excel_tools():
        registry.register(spec, handler)
    return registry


def steady_clock(start: datetime | None = None) -> Callable[[], datetime]:
    """A clock that ticks one minute per call, so event order is unambiguous."""
    ticks: Iterator[int] = iter(range(10**9))
    origin = start or datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    return lambda: origin + timedelta(minutes=next(ticks))


def decide(category: str, amount: float) -> tuple[str, str]:
    """The reviewer's rule: approve within the category limit, flag above it."""
    limit = EXPENSE_POLICY.get(category)
    if limit is None:
        return "flagged", f"no policy for category {category}"
    if amount <= limit:
        return "approved", f"{amount:.2f} within {category} limit {limit:.2f}"
    return "flagged", f"{amount:.2f} over {category} limit {limit:.2f}"


def _call(registry: ToolRegistry, tool: str, **params: Any) -> Any:
    result = dispatch(registry, ToolRequest(tool=tool, params=params))
    if not result.ok:
        raise RuntimeError(f"{tool} failed: {result.error}")
    return result.output


def review_batch_by_hand(registry: ToolRegistry, book: Path) -> list[tuple[str, str, str]]:
    """Play the human reviewer for one batch, through the real tools.

    Every step goes through ``dispatch`` — the dry-run gate is in force, so each
    decision write carries ``confirm=True`` — and the registry's tap records it.

    Returns:
        The ``(claim id, decision, reason)`` rows written to Decisions.
    """
    path = str(book)
    claims = _call(registry, "read_range", path=path, sheet="Claims")["rows"][1:]
    policy_rows = _call(registry, "read_range", path=path, sheet="Policy")["rows"][1:]
    assert {row[0]: row[1] for row in policy_rows} == EXPENSE_POLICY
    _call(registry, "get_data_flow", path=path, macro="ApproveClaims")

    decisions = []
    for offset, (claim_id, _employee, category, amount) in enumerate(claims):
        decision, reason = decide(category, float(amount))
        row = offset + 2
        _call(registry, "write_cell", path=path, sheet="Decisions",
              cell=f"A{row}", value=claim_id, confirm=True)
        _call(registry, "write_cell", path=path, sheet="Decisions",
              cell=f"B{row}", value=decision, confirm=True)
        _call(registry, "write_cell", path=path, sheet="Decisions",
              cell=f"C{row}", value=reason, confirm=True)
        decisions.append((claim_id, decision, reason))
    return decisions


def observe(workdir: Path, log: OcelLog) -> dict[str, list[tuple[str, str, str]]]:
    """Stage 1 — observe the reviewer across every sample batch.

    Builds one workbook per batch (one case each) and reviews it by hand.

    Returns:
        Batch name -> the decisions recorded for it.
    """
    registry = observed_registry(log, clock=steady_clock())
    outcomes = {}
    for batch, claims in EXPENSE_BATCHES.items():
        book = build_expense_workbook(workdir / f"claims-{batch}.xlsm", claims)
        outcomes[batch] = review_batch_by_hand(registry, book)
    return outcomes


def default_miner() -> Miner:
    """bupaR (which adds a BPMN) when R is installed, else the variant miner."""
    bupar = BupaRMiner(case_type=CASE_TYPE)
    return bupar if bupar.available() else VariantMiner(case_type=CASE_TYPE)


def mine(log: OcelLog, miner: Miner | None = None) -> ProcessModel:
    """Stage 2 — mine the observed log into a process model, one case per batch.

    Per-claim writes repeat once per claim, so runs of the same step are folded
    first (:func:`~agenticharvest.mining.collapse_repeats`): batches of any size then
    share one variant.
    """
    return (miner or default_miner()).mine(collapse_repeats(log, case_type=CASE_TYPE))


def case_traces(log: OcelLog) -> dict[str, list[str]]:
    """Each batch's trace, with repeated per-claim steps collapsed."""
    return traces(collapse_repeats(log, case_type=CASE_TYPE), case_type=CASE_TYPE)
