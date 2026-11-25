#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The stable tool contract, the registry, and the tap point.

Tools are the stable contract between agents and artifacts, and the single
place every call is observed. A domain contributes tool specs + handlers; the
registry resolves a name to a concrete handler and executes the call; each tap
observes it so that (once tracing and events land) one call emits one span and
one event.

Public API:
    ToolSpec        the declarative tool spec (name, schema, effect, domain)
    ToolResult      the normalized tool result envelope
    ToolRegistry    register / resolve / list / call
    default_registry  a registry with every entry-point domain loaded
    Tap, InMemoryTap, TapRecord   the observability seam and its reference tap
    Handler         the handler callable type
"""

from agenticharvest.core.tools.registry import Handler, ToolRegistry, default_registry
from agenticharvest.core.tools.result import ToolResult
from agenticharvest.core.tools.spec import Effect, ToolSpec
from agenticharvest.core.tools.tap import InMemoryTap, Tap, TapRecord

__all__ = [
    "Effect",
    "Handler",
    "InMemoryTap",
    "Tap",
    "TapRecord",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "default_registry",
]
