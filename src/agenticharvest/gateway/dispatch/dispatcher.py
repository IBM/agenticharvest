#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The single dispatch path.

Every channel (REST, MCP, CLI, …) normalizes its request into one ``ToolRequest``
and hands it here, so behavior, policy, and observability are identical no matter
how the request arrived: resolve the tool spec, apply the dry-run gate, then run
the tapped call through the registry.

``ToolRequest`` is deliberately not called ``ToolCall``: that name already
belongs to ``orchestration.agents.base.ToolCall`` (the record of a call a model
made mid-run), a different concept. Keeping the names distinct keeps the two
layers from being confused.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agenticharvest.core.tools.registry import ToolRegistry
from agenticharvest.core.tools.result import ToolResult
from agenticharvest.gateway.policy import gate


class ToolRequest(BaseModel):
    """A channel-agnostic request to call one tool.

    Attributes:
        tool: The name of the tool to call.
        params: The parameters to pass to it.
    """

    tool: str
    params: dict = Field(default_factory=dict)


def dispatch(registry: ToolRegistry, request: ToolRequest) -> ToolResult:
    """Resolve, gate, and execute one tool request.

    Args:
        registry: The registry that owns the tools and taps.
        request: The normalized request.

    Returns:
        The ``ToolResult`` — a failure envelope for an unknown tool or a call the
        policy gate blocked, otherwise whatever the tool returned.
    """
    spec = registry.get(request.tool)
    if spec is None:
        return ToolResult.failure(f"Unknown tool: '{request.tool}'.")

    blocked = gate(spec, request.params)
    if blocked is not None:
        return blocked

    return registry.call(request.tool, request.params)
