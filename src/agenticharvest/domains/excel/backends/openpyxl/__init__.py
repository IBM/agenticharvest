#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""openpyxl backend — file-level structure and VBA reads without Excel installed.

Exposes the concrete backend under the ``agenticharvest.backends`` entry point.
"""

from agenticharvest.domains.excel.backends.openpyxl.backend import OpenpyxlBackend, backend

__all__ = ["OpenpyxlBackend", "backend"]
