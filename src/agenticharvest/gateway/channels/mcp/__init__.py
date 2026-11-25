#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""MCP channel — expose AgenticHarvest tools to Model Context Protocol clients.

Public API:
    create_server   build an MCP server over a tool registry
    serve_stdio     run it over stdio (``agenticharvest serve mcp``)

Requires the ``mcp`` extra; importing this package without it raises
``ImportError`` naming the extra.
"""

try:
    from agenticharvest.gateway.channels.mcp.server import create_server, serve_stdio
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "The MCP channel needs the 'mcp' extra: pip install 'agenticharvest[mcp]'."
    ) from exc

__all__ = ["create_server", "serve_stdio"]
