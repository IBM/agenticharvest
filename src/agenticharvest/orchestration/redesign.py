#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Turn an as-is plan into a to-be plan: drop the steps that only carry data.

The as-is plan (:func:`~agenticharvest.orchestration.compile.compile_plan`) replays
what people did, including the steps that exist only because two systems do not
talk to each other: read a value here, type it in there. Cross-system analysis
(:func:`~agenticharvest.mining.analysis.retyped_values`) names those carries. This
module rewrites the plan around them:

- A **transfer** step writes values into an intermediate system purely so a
  later step can read them back out (paste the orders into the workbook, then
  read the priced lines off it). It is removed; the values flow directly.
- A **transform** step is where an intermediate system adds something: a macro
  computes the discount. It is kept, unless the caller supplies a replacement
  (the rule implemented natively), in which case it becomes that step.
- Every **write** to a system of record is kept and stays gated.

What counts as which is decided by the caller, from the analysis, not guessed
here: the caller passes the activities to drop and to replace. The result is a
:class:`Redesign` recording the as-is plan, the to-be plan, and why each step
went, so the change can be reviewed before an agent runs it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import TypeVar

from pydantic import BaseModel, Field

from agenticharvest.mining.model import Activity, ProcessModel, Transition, Variant
from agenticharvest.orchestration.compile import PlanStep, ProcessPlan

_V = TypeVar("_V")


class StepChange(BaseModel):
    """What happened to one as-is step.

    Attributes:
        activity: The as-is activity.
        change: ``kept``, ``removed``, or ``replaced``.
        reason: Why, in a sentence, for review.
        replacement: The activity it became, when replaced.
    """

    activity: str
    change: str
    reason: str = ""
    replacement: str = ""


class Redesign(BaseModel):
    """An as-is plan, the to-be plan derived from it, and the step changes.

    Attributes:
        as_is: The plan as observed.
        to_be: The plan to run instead.
        changes: One entry per as-is step.
    """

    as_is: ProcessPlan
    to_be: ProcessPlan
    changes: list[StepChange] = Field(default_factory=list)

    @property
    def removed(self) -> list[str]:
        """The as-is activities the to-be plan no longer performs."""
        return [c.activity for c in self.changes if c.change == "removed"]

    def summary(self) -> str:
        """A short review of the redesign, one line per change."""
        lines = [
            f"as-is: {len(self.as_is.steps)} steps -> to-be: {len(self.to_be.steps)} steps",
        ]
        for change in self.changes:
            if change.change == "kept":
                continue
            target = f" -> {change.replacement}" if change.replacement else ""
            lines.append(f"  {change.change:8} {change.activity}{target}  ({change.reason})")
        return "\n".join(lines)


def redesign(
    plan: ProcessPlan,
    *,
    remove: Mapping[str, str] | Iterable[str] = (),
    replace: Mapping[str, PlanStep] | None = None,
    reasons: Mapping[str, str] | None = None,
    name: str = "",
) -> Redesign:
    """Derive a to-be plan from ``plan``.

    Args:
        plan: The as-is plan.
        remove: The activities to drop, or activity -> reason. An activity
            ending in ``*`` matches by prefix.
        replace: Activity -> the step to perform instead.
        reasons: Activity -> why it was replaced, for review.
        name: The to-be plan's name. Defaults to the as-is name.

    Returns:
        The redesign. The to-be plan keeps the as-is coverage and case count,
        since it was derived from the same observations.
    """
    drop = dict(remove) if isinstance(remove, Mapping) else dict.fromkeys(remove, "")
    swaps = dict(replace or {})
    why = dict(reasons or {})
    steps: list[PlanStep] = []
    changes: list[StepChange] = []
    for step in plan.steps:
        reason = _match(drop, step.activity)
        if reason is not None:
            changes.append(StepChange(
                activity=step.activity,
                change="removed",
                reason=reason or "only carries data between systems",
            ))
            continue
        swap = _match(swaps, step.activity)
        if swap is not None:
            if all(s.activity != swap.activity for s in steps):
                steps.append(swap)
            changes.append(StepChange(
                activity=step.activity,
                change="replaced",
                reason=_match(why, step.activity) or "performed natively",
                replacement=swap.activity,
            ))
            continue
        steps.append(step)
        changes.append(StepChange(activity=step.activity, change="kept"))
    to_be = plan.model_copy(update={
        "name": name or plan.name,
        "steps": steps,
        "alternatives": [],
    })
    return Redesign(as_is=plan, to_be=to_be, changes=changes)


def plan_model(plan: ProcessPlan) -> ProcessModel:
    """The process model a plan describes: its steps as the one variant.

    A to-be plan was never observed, so there is no mined model to check an
    agent against. This gives :func:`~agenticharvest.mining.conformance.check_trace`
    one: every case is expected to follow exactly the plan's steps.
    """
    sequence = [step.activity for step in plan.steps]
    cases = plan.n_cases or 1
    return ProcessModel(
        miner="plan",
        n_cases=cases,
        n_events=len(sequence) * cases,
        activities=[Activity(name=a, frequency=cases) for a in dict.fromkeys(sequence)],
        directly_follows=[
            Transition(source=s, target=t, frequency=cases) for s, t in pairwise(sequence)
        ],
        variants=[Variant(sequence=sequence, frequency=cases)],
    )


def _match(table: Mapping[str, _V], activity: str) -> _V | None:
    """The value for ``activity`` in ``table``, honoring ``prefix*`` keys."""
    if activity in table:
        return table[activity]
    for key, value in table.items():
        if key.endswith("*") and activity.startswith(key[:-1]):
            return value
    return None


__all__ = ["Redesign", "StepChange", "plan_model", "redesign"]
