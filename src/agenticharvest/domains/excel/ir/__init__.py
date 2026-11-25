#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Excel -> Workbook IR.

Parses a workbook into the core graph IR: sheet and macro nodes joined by
call-graph edges and data-flow (read/write) edges, plus named ranges and the
controls that trigger macros. Structure and VBA are read through an
``ExcelBackend`` so the builder is engine-agnostic.

Public API:
    build_ir        workbook source -> WorkbookIR
    WorkbookIR      typed, read-only view over the core IR graph
    Sheet, Macro, NamedRange, Button, SheetRole   the workbook IR models

Contents:
    models      the workbook IR types and edge helpers
    extract     build_ir: assemble the IR from a backend's reads
    vba         pure-text VBA analysis (call graph + data flow)
"""

from agenticharvest.domains.excel.ir.extract import build_ir
from agenticharvest.domains.excel.ir.models import (
    Button,
    Macro,
    NamedRange,
    Sheet,
    SheetRole,
    WorkbookIR,
)

__all__ = [
    "Button",
    "Macro",
    "NamedRange",
    "Sheet",
    "SheetRole",
    "WorkbookIR",
    "build_ir",
]
