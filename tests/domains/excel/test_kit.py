#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the Excel authoring kit: an .xlsx plus the VBA modules to import."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from agenticharvest.domains.excel.ir.vba import infer_data_flow, parse_macros
from agenticharvest.domains.excel.kit import write_kit
from agenticharvest.domains.excel.synth import SheetSpec

_MODULE = (
    'Attribute VB_Name = "modCopy"\n'
    "Sub Copy()\n"
    '    shOut.Range("A1").Value = shIn.Range("A1").Value\n'
    "End Sub\n"
)


def test_write_kit_writes_sheets_with_code_names_and_modules(tmp_path: Path) -> None:
    kit = write_kit(
        tmp_path,
        sheets=[
            SheetSpec("In", codename="shIn", headers=["X"], rows=[[1]]),
            SheetSpec("Out", codename="shOut", headers=["Y"]),
        ],
        modules={"modCopy": _MODULE, "ThisWorkbook": 'Attribute VB_Name = "ThisWorkbook"\n'},
        name="book",
    )

    assert kit.workbook == tmp_path / "book.xlsx"
    workbook = openpyxl.load_workbook(kit.workbook)
    try:
        assert workbook.sheetnames == ["In", "Out"]
        assert workbook["In"].sheet_properties.codeName == "shIn"
        assert workbook["In"]["A2"].value == 1
    finally:
        workbook.close()

    # A standard module is importable as .bas; a document module is pasted.
    assert [p.name for p in kit.modules] == ["modCopy.bas"]
    assert [p.name for p in kit.documents] == ["ThisWorkbook.cls.txt"]
    raw = kit.modules[0].read_bytes()
    assert b"\r\n" in raw and b"\n\n" not in raw.replace(b"\r\n", b"")


def test_kit_module_keeps_the_data_flow_the_ir_reads(tmp_path: Path) -> None:
    kit = write_kit(
        tmp_path,
        sheets=[SheetSpec("In", codename="shIn"), SheetSpec("Out", codename="shOut")],
        modules={"modCopy": _MODULE},
    )
    source = kit.modules[0].read_text(encoding="cp1252")
    flows = infer_data_flow(
        parse_macros({"modCopy": source}), {"shIn": "In", "shOut": "Out"}, ["In", "Out"]
    )
    assert flows["Copy"] == {"reads": ["In"], "writes": ["Out"]}
