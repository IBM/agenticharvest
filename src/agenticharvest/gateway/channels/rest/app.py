#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""REST channel — a FastAPI surface over the dispatch path.

Two endpoints, both thin: list the available tools, and call one. Each request
is normalized into a ``ToolRequest`` and handed to ``dispatch`` — the REST layer
adds no behavior of its own, so a tool behaves identically here and over any
other channel.

A tool that fails is still a normal HTTP 200 carrying ``ok: false`` — a tool
error is a result, not a transport failure. Only an unknown tool is a 404.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from agenticharvest.core.tools.registry import ToolRegistry, default_registry
from agenticharvest.core.tools.result import ToolResult
from agenticharvest.core.tools.spec import ToolSpec
from agenticharvest.gateway.dispatch import ToolRequest, dispatch


def create_app(registry: ToolRegistry | None = None) -> FastAPI:
    """Build the REST app over a tool registry.

    Args:
        registry: The registry to serve. Defaults to a fresh one with every
            ``agenticharvest.domains`` entry-point domain loaded. Tests pass an explicit
            registry to serve a controlled tool set.

    Returns:
        A configured ``FastAPI`` application.
    """
    registry = registry or default_registry()
    app = FastAPI(title="AgenticHarvest gateway", version="0")

    @app.get("/tools", response_model=list[ToolSpec])
    def list_tools() -> list[ToolSpec]:
        """List every registered tool spec."""
        return registry.list_specs()

    @app.post("/tools/{name}/call", response_model=ToolResult)
    def call_tool(name: str, params: dict) -> ToolResult:
        """Call one tool by name with a JSON parameters body."""
        if not registry.has(name):
            raise HTTPException(status_code=404, detail=f"Unknown tool: '{name}'.")
        return dispatch(registry, ToolRequest(tool=name, params=params))

    return app
