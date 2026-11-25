#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Agent runner — composes ``AbstractAgent`` instances according to a pattern.

Four patterns are supported:

- ``SEQUENTIAL``:          agents run one after another; the output of each feeds
  as context into the next.

- ``ORCHESTRATOR_WORKER``: an orchestrator agent produces a plan; a caller-supplied
  factory creates worker agents for each subtask; workers run in parallel.

- ``PARALLEL``:            independent agents run concurrently; results are returned
  as a list in task order.

- ``REFLECTION``:          a single agent iteratively critiques and rewrites its own
  output until a stop condition is met or the iteration budget is exhausted.

``AgentRunner`` is stateless — each method is a self-contained coroutine. The
caller is responsible for agent lifecycle; the runner does not call ``close()``
on any agent.

Usage::

    runner = AgentRunner()

    # Sequential: one stage's output seeds the next
    result = await runner.run_sequential([
        (planner, "Draft a plan for the requested change."),
        (editor,  "Apply the plan."),
    ])

    # Orchestrator + workers
    orch_result, worker_results = await runner.run_orchestrator_worker(
        orchestrator=planner,
        orchestrator_prompt="Break the task into independent subtasks.",
        worker_factory=lambda r: [
            (worker_a, f"Do subtask A per the plan: {r.content}"),
            (worker_b, f"Do subtask B per the plan: {r.content}"),
        ],
    )

    # Parallel
    results = await runner.run_parallel([
        (worker_a, "Handle region A."),
        (worker_b, "Handle region B."),
    ])

    # Reflection: retry until a condition holds or the budget runs out
    result = await runner.run_reflection(
        agent=fixer,
        prompt="Resolve the validation errors.",
        max_iterations=5,
        stop_condition=lambda r: "OK" in r.content,
    )
"""

import asyncio
from collections.abc import Callable
from enum import Enum

from agenticharvest.orchestration.agents.base import (
    AbstractAgent,
    AgentResult,
    Message,
    StopReason,
)


class AgentPattern(str, Enum):
    """Agent composition patterns supported by ``AgentRunner``.

    Values:
        SEQUENTIAL: Agents run in order; each receives the previous agent's
            output as context.
        ORCHESTRATOR_WORKER: An orchestrator agent produces a plan; a worker
            factory creates agents for each subtask; workers run in parallel.
        PARALLEL: Independent agents run concurrently; results are collected
            as a list in the same order as the input tasks.
        REFLECTION: A single agent iteratively critiques and rewrites its own
            output until a stop condition is met or the iteration budget runs out.
    """

    SEQUENTIAL          = "sequential"
    ORCHESTRATOR_WORKER = "orchestrator_worker"
    PARALLEL            = "parallel"
    REFLECTION          = "reflection"


class AgentRunner:
    """Executes agents according to a composition pattern.

    ``AgentRunner`` is stateless — each method is a self-contained coroutine.
    Agents are responsible for their own lifecycle; the runner never calls
    ``close()`` on them.
    """

    async def run_sequential(
        self,
        steps: list[tuple[AbstractAgent, str]],
        initial_context: list[Message] | None = None,
    ) -> AgentResult:
        """Run agents in order, threading context from one to the next.

        Each agent receives the full message history from the previous agent as
        its context.

        Args:
            steps: Ordered list of ``(agent, prompt)`` pairs. Must be non-empty.
            initial_context: Optional seed context for the first agent.

        Returns:
            The ``AgentResult`` from the final step.

        Raises:
            ValueError: If ``steps`` is empty.
        """
        if not steps:
            raise ValueError("steps must contain at least one (agent, prompt) pair.")

        context = list(initial_context) if initial_context else []
        result: AgentResult | None = None

        for agent, prompt in steps:
            result = await agent.run(prompt, context)
            context = list(result.messages)

        return result  # type: ignore[return-value]

    async def run_parallel(
        self,
        tasks: list[tuple[AbstractAgent, str]],
        context: list[Message] | None = None,
    ) -> list[AgentResult]:
        """Run independent agents concurrently and return all results.

        Args:
            tasks: List of ``(agent, prompt)`` pairs to run concurrently.
            context: Shared read-only context provided to every agent.
                Agents do not write back to this list.

        Returns:
            List of ``AgentResult`` in the same order as ``tasks``.
        """
        coros = [agent.run(prompt, context) for agent, prompt in tasks]
        return list(await asyncio.gather(*coros))

    async def run_orchestrator_worker(
        self,
        orchestrator: AbstractAgent,
        orchestrator_prompt: str,
        worker_factory: Callable[[AgentResult], list[tuple[AbstractAgent, str]]],
        context: list[Message] | None = None,
    ) -> tuple[AgentResult, list[AgentResult]]:
        """Run an orchestrator then dispatch its planned subtasks to workers in parallel.

        The caller supplies a ``worker_factory`` that maps the orchestrator's
        ``AgentResult`` to ``(agent, prompt)`` pairs for each subtask. This keeps
        task-extraction logic out of the runner and in the caller where the
        domain context lives.

        Args:
            orchestrator: Agent that produces the execution plan.
            orchestrator_prompt: Prompt sent to the orchestrator.
            worker_factory: Callable that receives the orchestrator ``AgentResult``
                and returns a list of ``(worker_agent, subtask_prompt)`` pairs.
            context: Optional context provided to the orchestrator.

        Returns:
            A tuple of ``(orchestrator_result, worker_results)`` where
            ``worker_results`` is ordered to match the pairs returned by
            ``worker_factory``.
        """
        orchestrator_result = await orchestrator.run(orchestrator_prompt, context)
        worker_tasks = worker_factory(orchestrator_result)
        worker_results = await self.run_parallel(worker_tasks, orchestrator_result.messages)
        return orchestrator_result, worker_results

    async def run_reflection(
        self,
        agent: AbstractAgent,
        prompt: str,
        max_iterations: int = 5,
        stop_condition: Callable[[AgentResult], bool] | None = None,
        context: list[Message] | None = None,
    ) -> AgentResult:
        """Run an agent and iteratively reflect on its output until satisfied.

        The initial ``run()`` counts as the first attempt. ``reflect()`` is then
        called up to ``max_iterations`` more times. Total possible runs:
        ``1 + max_iterations``.

        Reflection stops early when:

        - ``stop_condition(result)`` returns ``True``, or
        - ``result.stop_reason`` is ``ERROR`` (no point reflecting on a crash).

        Args:
            agent: Agent that will run and self-reflect.
            prompt: Initial instruction for the first run.
            max_iterations: Maximum number of ``reflect()`` calls after the
                initial run. Defaults to ``5``.
            stop_condition: Optional predicate evaluated after each run and
                reflection. Stops early when it returns ``True``.
            context: Optional seed context for the initial run.

        Returns:
            The ``AgentResult`` from the final iteration.
        """
        result = await agent.run(prompt, context)

        for _ in range(max_iterations):
            if stop_condition and stop_condition(result):
                break
            if result.stop_reason == StopReason.ERROR:
                break
            result = await agent.reflect(result)

        return result
