#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""MCP channel — serve the gateway's tools to Model Context Protocol clients.

A thin normalizer, like the REST channel: ``tools/list`` advertises every
registered ``ToolSpec``, and ``tools/call`` becomes a ``ToolRequest`` handed to
``dispatch`` — so the dry-run gate and every tap apply exactly as they do over
REST. Write-effect tools are marked with ``destructiveHint`` and read tools with
``readOnlyHint``, so a client can ask its user before a write.

A failed tool call is returned as an MCP result with ``isError: true`` (the
model sees the message and can correct course), never as a protocol error.
Requires the ``mcp`` extra.
"""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

from agenticharvest.core.tools.registry import ToolRegistry, default_registry
from agenticharvest.core.tools.spec import Effect, ToolSpec
from agenticharvest.gateway.dispatch import ToolRequest, dispatch

SERVER_NAME = "agenticharvest"


def _to_mcp_tool(spec: ToolSpec) -> types.Tool:
    """Render a ``ToolSpec`` as an MCP tool declaration."""
    writes = spec.effect == Effect.WRITE
    return types.Tool(
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema or {"type": "object", "properties": {}},
        annotations=types.ToolAnnotations(
            read_only_hint=not writes,
            destructive_hint=writes,
        ),
    )


def create_server(registry: ToolRegistry | None = None) -> Server[Any]:
    """Build an MCP server over a tool registry.

    Args:
        registry: The registry to serve. Defaults to a fresh one with every
            ``agenticharvest.domains`` entry-point domain loaded.

    Returns:
        A configured low-level MCP ``Server``; run it over any MCP transport.
    """
    registry = registry or default_registry()

    async def list_tools(
        _ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_to_mcp_tool(s) for s in registry.list_specs()])

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        result = dispatch(
            registry, ToolRequest(tool=params.name, params=dict(params.arguments or {}))
        )
        if not result.ok:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=result.error or "tool failed")],
                is_error=True,
            )
        text = json.dumps(result.output, default=str)
        structured = result.output if isinstance(result.output, dict) else None
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=structured,
            is_error=False,
        )

    from agenticharvest import __version__

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=(
            "AgenticHarvest exposes typed tools over structured artifacts (workbooks, "
            "databases, ...). Write tools need confirm=true."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_stdio(registry: ToolRegistry | None = None) -> None:
    """Serve the registry over stdio — the transport desktop MCP clients launch."""
    from mcp.server.stdio import stdio_server

    server = create_server(registry)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
