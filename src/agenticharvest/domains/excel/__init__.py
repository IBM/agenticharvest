#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The Excel domain — the reference implementation.

Excel -> Workbook IR (structure, call graph, data flow, VBA macros), exposing
five read-only tools (``list_sheets``, ``describe_sheet``, ``list_macros``,
``get_call_graph``, ``get_data_flow``) over that IR, ``read_range`` over the cell
values, plus two write tools (``write_cell``, ``run_macro``) that change the
artifact through the gateway's dry-run gate, all backed by pluggable execution
engines.

The module-level ``domain`` object is what the ``agenticharvest.domains`` entry point in
pyproject points at; loading it registers the Excel tools into a gateway
registry.

Subpackages and modules:
    ir          the Workbook IR builder (workbook -> core.ir graph)
    tools       the Excel tool specs
    backends    execution engines (openpyxl file-level, xlwings live)
    ocel        the OCEL mapper (ExcelOCEL) for the event log
    synth       synthesize a genuine macro-bearing .xlsm without Excel
    samples     ready-made sample workbooks for demos and tests
"""

from agenticharvest.domains.base import SimpleDomain
from agenticharvest.domains.excel.ocel import ExcelOCEL
from agenticharvest.domains.excel.tools import excel_tools

# The entry-point target. ``load_entry_point_domains`` imports this object and
# registers every (spec, handler) pair it carries.
domain = SimpleDomain(name="excel", tools=excel_tools())

__all__ = ["ExcelOCEL", "domain"]
