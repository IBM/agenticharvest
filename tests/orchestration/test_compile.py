#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for compiling a process model into an agent plan."""

from __future__ import annotations

from typing import Any, ClassVar

from agenticharvest.core.tools.spec import Effect, ToolSpec
from agenticharvest.mining.model import ProcessModel, Variant
from agenticharvest.orchestration.agents.base import Tool
from agenticharvest.orchestration.compile import (
    compile_plan,
    restrict_tools,
    target_of,
    tool_of,
)

_SPECS = [
    ToolSpec(name="read_range", description="Read cells."),
    ToolSpec(name="get_data_flow", description="Data flow."),
    ToolSpec(name="write_cell", description="Write a cell.", effect=Effect.WRITE),
]


def _model() -> ProcessModel:
    return ProcessModel(
        miner="test",
        n_cases=4,
        variants=[
            Variant(sequence=["read_range:Claims", "write_cell:Decisions"], frequency=1),
            Variant(
                sequence=["read_range:Claims", "read_range:Policy", "write_cell:Decisions"],
                frequency=3,
            ),
        ],
    )


def test_activity_parsing() -> None:
    assert tool_of("read_range:Policy") == "read_range"
    assert target_of("read_range:Policy") == "Policy"
    assert tool_of("get_data_flow") == "get_data_flow"
    assert target_of("get_data_flow") == ""


def test_compile_plan_follows_the_dominant_variant() -> None:
    plan = compile_plan(_model(), _SPECS, name="expense review")

    assert [s.activity for s in plan.steps] == [
        "read_range:Claims",
        "read_range:Policy",
        "write_cell:Decisions",
    ]
    assert plan.coverage == 0.75
    assert plan.n_cases == 4
    assert plan.alternatives == [["read_range:Claims", "write_cell:Decisions"]]
    assert plan.tools == ["read_range", "write_cell"]
    # Only the write-effect step needs approval.
    assert [s.requires_approval for s in plan.steps] == [False, False, True]


def test_compile_plan_without_specs_flags_nothing() -> None:
    plan = compile_plan(_model())
    assert not any(s.requires_approval for s in plan.steps)


def test_compile_plan_on_an_empty_model() -> None:
    plan = compile_plan(ProcessModel(), _SPECS)
    assert plan.steps == []
    assert plan.coverage == 0.0


def test_system_prompt_lists_steps_and_gates_writes() -> None:
    prompt = compile_plan(_model(), _SPECS, name="expense review").system_prompt(
        goal="Review the claims."
    )
    assert prompt.startswith("Review the claims.")
    assert "75% of them followed it exactly" in prompt
    assert "1. Call read_range on 'Claims'." in prompt
    assert "3. Call write_cell on 'Decisions'. This writes: pass confirm=true" in prompt


class _Named(Tool):
    description = "x"
    input_schema: ClassVar[dict[str, Any]] = {"type": "object"}

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def run(self, arguments: dict) -> str:
        return ""


def test_restrict_tools_keeps_only_planned_tools() -> None:
    plan = compile_plan(_model(), _SPECS)
    offered = [_Named(n) for n in ["list_sheets", "read_range", "run_macro", "write_cell"]]
    assert [t.name for t in restrict_tools(offered, plan)] == ["read_range", "write_cell"]
