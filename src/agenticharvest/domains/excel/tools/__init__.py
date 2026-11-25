#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Excel tool specs — the stable contract for spreadsheets.

Declarative specs (name, typed params, effect) for the read-only Excel
capabilities the current backend supports: ``list_sheets``, ``describe_sheet``,
``list_macros``, ``get_call_graph``, ``get_data_flow``. Each is backed by the
workbook IR; the registry binds them to their handlers at load time.

Public API:
    excel_tools     the domain's (spec, handler) pairs
"""

from agenticharvest.domains.excel.tools.specs import excel_tools

__all__ = ["excel_tools"]
