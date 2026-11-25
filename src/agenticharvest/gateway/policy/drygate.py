#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The dry-run gate.

Applied uniformly at the gateway, before a tool runs: an effectful (``write``)
tool must be confirmed before it changes anything, so a caller cannot mutate an
artifact by accident. A read tool passes straight through.

It guards every write tool: ``write_cell`` and ``run_macro`` (Excel),
``execute`` (SQLite), and ``send_operation`` (web). A new write tool inherits
the guard by declaring ``Effect.WRITE``; it does not have to add it.
"""

from __future__ import annotations

from agenticharvest.core.tools.result import ToolResult
from agenticharvest.core.tools.spec import Effect, ToolSpec

# A write request opts in by carrying this parameter set to True. The gate never
# runs the tool to "preview" it — confirmation is the caller's explicit act.
CONFIRM_PARAM = "confirm"


def gate(spec: ToolSpec, params: dict) -> ToolResult | None:
    """Return a blocking result if the call is not allowed to proceed, else None.

    Args:
        spec: The spec of the tool being called.
        params: The parameters of the call.

    Returns:
        A failed ``ToolResult`` when a write is unconfirmed; ``None`` when the
        call may proceed to the handler.
    """
    if spec.effect == Effect.WRITE and params.get(CONFIRM_PARAM) is not True:
        return ToolResult.failure(
            f"Tool '{spec.name}' has a write effect and requires "
            f"'{CONFIRM_PARAM}: true' to run."
        )
    return None
