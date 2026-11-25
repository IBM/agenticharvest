#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The single dispatch path.

normalize(request) -> ToolRequest -> resolve(spec) -> gate(policy) ->
execute(tapped). Every channel converges here so behavior, policy, and
observability are identical no matter how the request arrived.

Public API:
    ToolRequest     a channel-agnostic request to call one tool
    dispatch        resolve + gate + execute a request through a registry
"""

from agenticharvest.gateway.dispatch.dispatcher import ToolRequest, dispatch

__all__ = ["ToolRequest", "dispatch"]
