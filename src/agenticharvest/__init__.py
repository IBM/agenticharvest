#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""AgenticHarvest — process-mining-based agentification.

Top-level package. The public surface is intentionally small; most work is done
through the layered subpackages:

    agenticharvest.core          the domain-agnostic backbone (contracts, tools, events)
    agenticharvest.domains       pluggable domains (excel, sqlite, or your own)
    agenticharvest.gateway       one dispatch path behind every channel (REST, MCP, CLI)
    agenticharvest.mining        process mining over the event log
    agenticharvest.orchestration agents, and compiling a mined process into a plan
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agenticharvest")
except PackageNotFoundError:  # pragma: no cover - running from an unbuilt checkout
    __version__ = "0.0.0+unknown"
