#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The process model — the compiled, automatable artifact.

A miner-agnostic representation of the discovered process: the activities it saw
and how often, the directly-follows relation between them, the distinct variants
(traces) and their frequencies, and — when a discovery engine that produces one
is available — a full BPMN model (nodes and sequence flows).

This is what ``orchestration`` compiles into an agent loop. Keeping it
independent of any single miner means engines can be swapped without changing
downstream consumers: ``bupaR`` produces it today, but any miner behind the
``Miner`` protocol returns the same shape, so a report or a diagram consumes
mining output without knowing which engine ran.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Activity(BaseModel):
    """One activity (event type) and how often it occurred.

    Attributes:
        name: The activity name — a tool/event activity from the log.
        frequency: Absolute number of occurrences.
    """

    name: str
    frequency: int = 0


class Transition(BaseModel):
    """A directly-follows edge: ``source`` was immediately followed by ``target``.

    Attributes:
        source: The activity that came first.
        target: The activity that immediately followed it.
        frequency: How often this succession was observed.
    """

    source: str
    target: str
    frequency: int = 0


class Variant(BaseModel):
    """A distinct trace (ordered activity sequence) and its frequency.

    Attributes:
        sequence: The activities in order, as one case played out.
        frequency: How many cases followed exactly this sequence.
    """

    sequence: list[str] = Field(default_factory=list)
    frequency: int = 0


class BpmnNode(BaseModel):
    """A node in a discovered BPMN model (a task, gateway, or event).

    Attributes:
        id: The node's identifier within the model.
        name: Its label (an activity name for tasks; often empty for gateways).
        type: The BPMN element type, e.g. ``task`` or ``gateway`` — engine-
            reported, so left an open string.
    """

    id: str
    name: str = ""
    type: str = ""


class BpmnFlow(BaseModel):
    """A sequence flow (directed edge) between two BPMN nodes.

    Attributes:
        source: The id of the node the flow leaves.
        target: The id of the node the flow enters.
    """

    source: str
    target: str


class Bpmn(BaseModel):
    """A discovered BPMN process model: nodes joined by sequence flows."""

    nodes: list[BpmnNode] = Field(default_factory=list)
    flows: list[BpmnFlow] = Field(default_factory=list)


class ProcessModel(BaseModel):
    """The result of mining an event log.

    Attributes:
        miner: The engine that produced the model, e.g. ``bupaR``.
        n_cases: Number of cases (distinct case ids) mined.
        n_events: Number of events mined.
        activities: The activities seen, with frequencies.
        directly_follows: The directly-follows relation between activities.
        variants: Distinct traces and their frequencies.
        bpmn: A discovered BPMN model when the engine produced one; otherwise
            ``None`` (activities / directly-follows / variants are always
            present, a BPMN only when a discovery engine like ``bupaRminer`` ran).
    """

    miner: str = ""
    n_cases: int = 0
    n_events: int = 0
    activities: list[Activity] = Field(default_factory=list)
    directly_follows: list[Transition] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    bpmn: Bpmn | None = None

    @classmethod
    def from_bupar(cls, data: dict[str, Any]) -> ProcessModel:
        """Build a ``ProcessModel`` from the JSON the bupaR script emits."""
        bpmn_data = data.get("bpmn")
        bpmn = None
        if isinstance(bpmn_data, dict):
            bpmn = Bpmn(
                nodes=[BpmnNode(**n) for n in bpmn_data.get("nodes", [])],
                flows=[BpmnFlow(**f) for f in bpmn_data.get("flows", [])],
            )
        return cls(
            miner=data.get("miner", "bupaR"),
            n_cases=int(data.get("n_cases", 0)),
            n_events=int(data.get("n_events", 0)),
            activities=[Activity(**a) for a in data.get("activities", [])],
            directly_follows=[Transition(**t) for t in data.get("directly_follows", [])],
            variants=[Variant(**v) for v in data.get("variants", [])],
            bpmn=bpmn,
        )


__all__ = [
    "Activity",
    "Bpmn",
    "BpmnFlow",
    "BpmnNode",
    "ProcessModel",
    "Transition",
    "Variant",
]
