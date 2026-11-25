#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Cross-cutting request policy.

Applied uniformly at the gateway, before dispatch. Only the dry-run gate is
implemented today; auth and rate limiting are future seams.

    drygate     the dry-run gate — a write tool must be confirmed before it runs

Public API:
    gate            block an unconfirmed write, else allow the call
    CONFIRM_PARAM   the parameter a write opts in with
"""

from agenticharvest.gateway.policy.drygate import CONFIRM_PARAM, gate

__all__ = ["CONFIRM_PARAM", "gate"]
