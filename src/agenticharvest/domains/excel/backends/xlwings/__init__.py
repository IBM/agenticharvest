#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""xlwings backend — live MS Excel (real recalculation, real macros)."""

from agenticharvest.domains.excel.backends.xlwings.backend import (
    XlwingsBackend,
    backend,
)

__all__ = ["XlwingsBackend", "backend"]
