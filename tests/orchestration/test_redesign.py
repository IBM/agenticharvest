#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Deriving a to-be plan from an as-is plan, and checking traces against it."""

from __future__ import annotations

from agenticharvest.mining import check_trace
from agenticharvest.orchestration.compile import PlanStep, ProcessPlan
from agenticharvest.orchestration.redesign import plan_model, redesign


def _plan() -> ProcessPlan:
    return ProcessPlan(
        name="fulfillment",
        n_cases=6,
        coverage=1.0,
        steps=[
            PlanStep(activity="query:orders", tool="query", target="orders"),
            PlanStep(activity="write_cell:Orders", tool="write_cell", target="Orders",
                     requires_approval=True),
            PlanStep(activity="run_macro:Price", tool="run_macro", target="Price",
                     requires_approval=True),
            PlanStep(activity="call_operation:readShipments", tool="call_operation",
                     target="readShipments"),
            PlanStep(activity="send_operation:createShipments", tool="send_operation",
                     target="createShipments", requires_approval=True),
        ],
        alternatives=[["query:orders"]],
    )


def test_remove_and_replace_steps_with_reasons() -> None:
    priced = PlanStep(activity="compute:price", tool="compute")
    change = redesign(
        _plan(),
        remove={"write_cell:*": "pastes values the next step re-reads",
                "call_operation:readShipments": ""},
        replace={"run_macro:Price": priced},
        reasons={"run_macro:Price": "the discount rule, implemented natively"},
        name="fulfillment (to-be)",
    )
    assert [s.activity for s in change.to_be.steps] == [
        "query:orders", "compute:price", "send_operation:createShipments",
    ]
    assert change.removed == ["write_cell:Orders", "call_operation:readShipments"]
    assert change.to_be.name == "fulfillment (to-be)"
    assert change.to_be.n_cases == 6 and change.to_be.alternatives == []
    # The as-is plan is kept unchanged for review.
    assert len(change.as_is.steps) == 5
    reasons = {c.activity: c.reason for c in change.changes}
    assert reasons["call_operation:readShipments"] == "only carries data between systems"
    assert reasons["run_macro:Price"] == "the discount rule, implemented natively"
    summary = change.summary()
    assert "as-is: 5 steps -> to-be: 3 steps" in summary
    assert "replaced run_macro:Price -> compute:price" in summary


def test_writes_kept_in_the_to_be_plan_stay_gated() -> None:
    change = redesign(_plan(), remove=["write_cell:Orders"])
    gated = {s.activity: s.requires_approval for s in change.to_be.steps}
    assert gated["send_operation:createShipments"] is True
    assert "pass confirm=true" in change.to_be.system_prompt()


def test_plan_model_checks_a_trace_against_the_to_be_plan() -> None:
    change = redesign(_plan(), remove=["write_cell:*", "run_macro:*", "call_operation:*"])
    model = plan_model(change.to_be)
    assert [v.sequence for v in model.variants] == [
        ["query:orders", "send_operation:createShipments"]
    ]
    assert check_trace(model, ["query:orders", "send_operation:createShipments"]).conforms
    as_is_trace = ["query:orders", "call_operation:readShipments",
                   "send_operation:createShipments"]
    report = check_trace(model, as_is_trace)
    assert not report.conforms and report.unseen_steps == ["call_operation:readShipments"]
