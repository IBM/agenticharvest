#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Conformance — does a new trace follow the mined process?

Once a process has been mined and handed to an agent, the agent's own tool calls
are observed into a fresh log. Checking each of its traces against the model
closes the loop: it says whether the agent did what the observed process does,
and where it departed from it.

The check is deliberately simple and explainable — it replays the trace against
the model's directly-follows relation rather than computing an alignment:

    known_variant      the exact trace was observed during mining
    unseen_steps       activities the model never saw
    unseen_transitions consecutive pairs the model never saw
    fitness            share of the trace's transitions the model has seen
"""

from __future__ import annotations

from itertools import pairwise

from pydantic import BaseModel, Field

from agenticharvest.mining.model import ProcessModel


class ConformanceReport(BaseModel):
    """How one trace compares with a process model.

    Attributes:
        trace: The activities checked, in order.
        known_variant: ``True`` when the exact trace is one of the model's
            variants.
        unseen_steps: Activities in the trace that the model never observed.
        unseen_transitions: Consecutive ``(source, target)`` pairs in the trace
            that the model's directly-follows relation does not contain.
        fitness: Fraction of the trace's transitions the model has seen, from
            ``0.0`` to ``1.0``. A trace of one step (no transitions) scores
            ``1.0`` when that step is known, else ``0.0``.
    """

    trace: list[str] = Field(default_factory=list)
    known_variant: bool = False
    unseen_steps: list[str] = Field(default_factory=list)
    unseen_transitions: list[tuple[str, str]] = Field(default_factory=list)
    fitness: float = 0.0

    @property
    def conforms(self) -> bool:
        """``True`` when every step and every transition was seen in mining."""
        return not self.unseen_steps and not self.unseen_transitions


def check_trace(model: ProcessModel, trace: list[str]) -> ConformanceReport:
    """Replay ``trace`` against ``model`` and report where it departs.

    Args:
        model: The mined process model.
        trace: The activities to check, in order.

    Returns:
        A :class:`ConformanceReport` for the trace.
    """
    known_steps = {a.name for a in model.activities}
    known_pairs = {(t.source, t.target) for t in model.directly_follows}
    known_variants = {tuple(v.sequence) for v in model.variants}

    pairs = list(pairwise(trace))
    unseen_pairs = [p for p in pairs if p not in known_pairs]
    unseen_steps = list(dict.fromkeys(s for s in trace if s not in known_steps))

    if pairs:
        fitness = 1 - len(unseen_pairs) / len(pairs)
    else:
        fitness = 1.0 if trace and not unseen_steps else 0.0

    return ConformanceReport(
        trace=list(trace),
        known_variant=tuple(trace) in known_variants,
        unseen_steps=unseen_steps,
        unseen_transitions=unseen_pairs,
        fitness=round(fitness, 4),
    )


__all__ = ["ConformanceReport", "check_trace"]
