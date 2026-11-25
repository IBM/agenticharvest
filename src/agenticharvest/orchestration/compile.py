#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Compile a mined process model into something an agent can follow.

This is the link that closes AgenticHarvest's loop. Mining turns observed tool calls
into a :class:`~agenticharvest.mining.model.ProcessModel`; this module turns that model
back into guidance an agent runs with:

    compile_plan(model)        -> ProcessPlan: the happy path, its coverage, and
                                  the steps that need a human (writes)
    ProcessPlan.system_prompt  -> instructions to seed an agent's system prompt
    restrict_tools(tools, ...) -> only the tools the process actually used

The plan is built from the *dominant variant* — the trace most cases followed —
rather than from a BPMN, so it works with any miner (bupaR or the pure-Python
variant miner). Each step is an activity name; a domain mapper may qualify it
(``read_range:Policy``), so the tool to call is the part before the colon.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from agenticharvest.core.tools.spec import Effect, ToolSpec
from agenticharvest.mining.model import ProcessModel
from agenticharvest.orchestration.agents.base import Tool


def tool_of(activity: str) -> str:
    """Return the tool an activity calls: ``read_range:Policy`` -> ``read_range``."""
    return activity.split(":", 1)[0]


def target_of(activity: str) -> str:
    """Return the activity's target, if it names one: ``read_range:Policy`` -> ``Policy``."""
    _, _, target = activity.partition(":")
    return target


class PlanStep(BaseModel):
    """One step of a compiled plan.

    Attributes:
        activity: The activity as mined, e.g. ``read_range:Policy``.
        tool: The tool that performs it, e.g. ``read_range``.
        target: What the step acts on, when the activity names it.
        requires_approval: ``True`` for a write-effect tool — the step changes
            the artifact, so a human (or ``confirm=true``) must sign off.
    """

    activity: str
    tool: str
    target: str = ""
    requires_approval: bool = False


class ProcessPlan(BaseModel):
    """The process an agent should follow, as compiled from a model.

    Attributes:
        name: A short label for the process.
        steps: The happy path, in order.
        coverage: Share of mined cases that followed exactly this path.
        n_cases: How many cases the model was mined from.
        alternatives: The other variants, most frequent first, for context.
    """

    name: str = "process"
    steps: list[PlanStep] = Field(default_factory=list)
    coverage: float = 0.0
    n_cases: int = 0
    alternatives: list[list[str]] = Field(default_factory=list)

    @property
    def tools(self) -> list[str]:
        """The distinct tools the plan uses, in first-use order."""
        return list(dict.fromkeys(step.tool for step in self.steps))

    def system_prompt(self, goal: str = "") -> str:
        """Render the plan as agent instructions.

        Args:
            goal: An optional sentence stating what the process achieves, put
                first so the agent knows why it follows the steps.

        Returns:
            Plain-text instructions suitable for ``AgentConfig.system_prompt``.
        """
        lines: list[str] = []
        if goal:
            lines += [goal, ""]
        lines.append(
            f"Follow this {self.name} process. It was mined from {self.n_cases} "
            f"observed case(s); {self.coverage:.0%} of them followed it exactly."
        )
        lines.append("")
        for index, step in enumerate(self.steps, start=1):
            where = f" on '{step.target}'" if step.target else ""
            line = f"{index}. Call {step.tool}{where}."
            if step.requires_approval:
                line += " This writes: pass confirm=true only once the checks above pass."
            lines.append(line)
        lines.append("")
        lines.append(
            "Use only what the tools return. If a step cannot be completed, stop "
            "and say which step failed and why instead of improvising."
        )
        return "\n".join(lines)


def compile_plan(
    model: ProcessModel,
    specs: Iterable[ToolSpec] = (),
    *,
    name: str = "process",
) -> ProcessPlan:
    """Compile ``model`` into a :class:`ProcessPlan` around its dominant variant.

    Args:
        model: The mined process model.
        specs: The tool specs in play, used to flag write steps for approval.
            Without them no step is flagged.
        name: A short label for the process, used in the rendered prompt.

    Returns:
        The plan. Empty when the model has no variants.
    """
    effects = {spec.name: spec.effect for spec in specs}
    if not model.variants:
        return ProcessPlan(name=name, n_cases=model.n_cases)

    ranked = sorted(model.variants, key=lambda v: v.frequency, reverse=True)
    dominant = ranked[0]
    steps = [
        PlanStep(
            activity=activity,
            tool=tool_of(activity),
            target=target_of(activity),
            requires_approval=effects.get(tool_of(activity)) == Effect.WRITE,
        )
        for activity in dominant.sequence
    ]
    total = model.n_cases or sum(v.frequency for v in model.variants)
    return ProcessPlan(
        name=name,
        steps=steps,
        coverage=round(dominant.frequency / total, 4) if total else 0.0,
        n_cases=total,
        alternatives=[list(v.sequence) for v in ranked[1:]],
    )


def restrict_tools(tools: Iterable[Tool], plan: ProcessPlan) -> list[Tool]:
    """Keep only the tools the plan uses — least privilege for the agent.

    Args:
        tools: The agent tools on offer (e.g. from ``registry_tools``).
        plan: The compiled plan.

    Returns:
        The subset of ``tools`` whose name the plan calls, in their given order.
    """
    wanted = set(plan.tools)
    return [tool for tool in tools if tool.name in wanted]


__all__ = [
    "PlanStep",
    "ProcessPlan",
    "compile_plan",
    "restrict_tools",
    "target_of",
    "tool_of",
]
