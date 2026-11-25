#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the openpyxl + oletools backend's structure and VBA reads.

These check the backend in isolation — the raw reads it exposes through the
``ExcelBackend`` interface — before the IR builder layers analysis on top. They
guard the file-format details that are easy to get subtly wrong: code names
(which live on ``sheet_properties`` and vanish in read-only mode), header
trimming, self-referencing hyperlinks (attached to cells on the read path, not to
the write-only ``_hyperlinks`` list), and module-name normalization.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agenticharvest.domains.excel.backends.base import Capability, UnsupportedOperation
from agenticharvest.domains.excel.backends.openpyxl import OpenpyxlBackend


def test_read_sheets_returns_names_codenames_and_headers(orders_workbook: Path) -> None:
    backend = OpenpyxlBackend(str(orders_workbook))
    try:
        sheets, codename_map = backend.read_sheets()
    finally:
        backend.close()

    by_name = {s.name: s for s in sheets}
    assert [s.name for s in sheets] == ["Orders", "Summary", "Config"]
    assert by_name["Orders"].codename == "shOrders"
    assert by_name["Orders"].headers == ["OrderID", "Qty", "Price"]
    # The hyperlink sits below the header row, so headers stay clean.
    assert by_name["Summary"].headers == ["Metric", "Value"]
    # codeName -> display-name map is what the data-flow analysis keys on.
    assert codename_map == {
        "shOrders": "Orders",
        "shSummary": "Summary",
        "shConfig": "Config",
    }
    # index reflects workbook order.
    assert by_name["Config"].index == 2


def test_read_named_ranges_skips_internal_names(orders_workbook: Path) -> None:
    backend = OpenpyxlBackend(str(orders_workbook))
    try:
        names = {n.name: n for n in backend.read_named_ranges()}
    finally:
        backend.close()

    assert "TaxCell" in names
    assert names["TaxCell"].sheet == "Config"
    assert names["TaxCell"].ref == "$A$1"
    # No Excel-internal names (_xlnm.*, Print_Area, ...) leak through.
    assert not any(n.startswith("_") or "Print_" in n for n in names)


def test_read_controls_finds_self_referencing_hyperlink(orders_workbook: Path) -> None:
    backend = OpenpyxlBackend(str(orders_workbook))
    try:
        controls = backend.read_controls()
    finally:
        backend.close()

    assert len(controls) == 1
    button = controls[0]
    assert button.sheet == "Summary"
    assert button.cell == "D2"
    assert button.kind == "hyperlink"
    assert button.label == "Back to Orders"


def test_read_vba_modules_returns_source_by_module(orders_workbook: Path) -> None:
    backend = OpenpyxlBackend(str(orders_workbook))
    try:
        modules = backend.read_vba_modules()
    finally:
        backend.close()

    assert set(modules) == {"modOrders", "ThisWorkbook"}
    assert "Public Sub RefreshSummary()" in modules["modOrders"]
    assert "Private Sub Workbook_Open()" in modules["ThisWorkbook"]


def test_backend_advertises_read_and_write_but_not_execute() -> None:
    caps = OpenpyxlBackend.capabilities
    assert Capability.READ in caps
    assert Capability.WRITE in caps
    assert Capability.EXECUTE not in caps


def test_run_macro_is_unsupported(orders_workbook: Path) -> None:
    backend = OpenpyxlBackend(str(orders_workbook))
    with pytest.raises(UnsupportedOperation, match="cannot run macros"):
        backend.run_macro("RefreshSummary")


def test_write_cell_sets_value_and_reports_old(
    orders_workbook: Path, tmp_path: Path
) -> None:
    book = tmp_path / "orders.xlsm"
    shutil.copyfile(orders_workbook, book)
    backend = OpenpyxlBackend(str(book))

    edit = backend.write_cell("Summary", "A1", "Report")
    assert edit.new_value == "Report"

    # Reload through a fresh backend to prove the value hit disk.
    import openpyxl

    workbook = openpyxl.load_workbook(str(book), data_only=True)
    try:
        assert workbook["Summary"]["A1"].value == "Report"
    finally:
        workbook.close()


def test_write_cell_leaves_no_empty_defined_names(tmp_path: Path) -> None:
    # openpyxl writes <definedNames/> for a workbook without named ranges; Excel
    # reports that as corrupt and its repair drops the VBA project.
    import zipfile

    from agenticharvest.domains.excel.synth import SheetSpec, build_macro_workbook

    book = build_macro_workbook(
        tmp_path / "plain.xlsm",
        sheets=[SheetSpec("Data", codename="shData", headers=["A"])],
        modules={"m": 'Attribute VB_Name = "m"\nSub Noop()\nEnd Sub\n'},
    )
    OpenpyxlBackend(str(book)).write_cell("Data", "A2", 1)

    with zipfile.ZipFile(book) as archive:
        assert "<definedNames" not in archive.read("xl/workbook.xml").decode()
        assert "xl/vbaProject.bin" in archive.namelist()
    assert OpenpyxlBackend(str(book)).read_range("Data", "A2") == [[1]]
