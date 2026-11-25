#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Channels — pick any one.

Each channel is a thin normalizer: it accepts its native protocol and produces
the same ``ToolRequest`` the dispatch path expects. Channels carry no business
logic.

    rest    FastAPI: GET /tools, POST /tools/{name}/call   (the 'rest' extra)
    mcp     Model Context Protocol over stdio              (the 'mcp' extra)
    cli     the ``agenticharvest`` command
"""
