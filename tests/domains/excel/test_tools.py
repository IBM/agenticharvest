#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the Excel domain's tool handlers against the orders workbook."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agenticharvest.core.tools import ToolRegistry
from agenticharvest.domains.excel.tools import excel_tools
from agenticharvest.domains.excel.tools.specs import (
    describe_sheet,
    get_call_graph,
    get_data_flow,
    list_macros,
    list_sheets,
    read_range,
    run_macro,
    write_cell,
)
from agenticharvest.gateway.dispatch import ToolRequest, dispatch


def test_excel_tools_expose_six_read_and_two_write_specs() -> None:
    # Check the assembled domain: SimpleDomain is what stamps each spec's
    # ``domain`` field, so this exercises the real entry-point wiring.
    from agenticharvest.domains.excel import domain

    specs = {s.name: s for s in domain.tool_specs()}
    assert set(specs) == {
        "list_sheets",
        "describe_sheet",
        "list_macros",
        "get_call_graph",
        "get_data_flow",
        "read_range",
        "write_cell",
        "run_macro",
    }
    read_tools = {"list_sheets", "describe_sheet", "list_macros", "get_call_graph",
                  "get_data_flow", "read_range"}
    assert {n for n, s in specs.items() if s.effect == "read"} == read_tools
    assert {n for n, s in specs.items() if s.effect == "write"} == {
        "write_cell",
        "run_macro",
    }
    assert all(s.domain == "excel" for s in specs.values())
    # Descriptions stay short, generic capability statements.
    assert all(len(s.description) < 120 for s in specs.values())


def test_list_sheets_reports_roles(orders_workbook: Path) -> None:
    sheets = list_sheets({"path": str(orders_workbook)})
    by_name = {s["name"]: s for s in sheets}

    assert by_name["Orders"]["role"] == "input"
    assert by_name["Summary"]["role"] == "output"
    assert by_name["Orders"]["codename"] == "shOrders"
    assert by_name["Orders"]["headers"] == ["OrderID", "Qty", "Price"]


def test_describe_sheet_reports_readers_and_writers(orders_workbook: Path) -> None:
    detail = describe_sheet({"path": str(orders_workbook), "sheet": "Summary"})
    assert detail["role"] == "output"
    assert "WriteTotals" in detail["written_by"]


def test_describe_sheet_unknown_raises(orders_workbook: Path) -> None:
    with pytest.raises(ValueError, match="No sheet named"):
        describe_sheet({"path": str(orders_workbook), "sheet": "Nope"})


def test_list_macros_flags_event_handler(orders_workbook: Path) -> None:
    macros = {m["name"]: m for m in list_macros({"path": str(orders_workbook)})}
    assert macros["Workbook_Open"]["is_event_handler"] is True
    assert macros["ComputeTotal"]["is_event_handler"] is False


def test_get_call_graph_walks_both_directions(orders_workbook: Path) -> None:
    graph = get_call_graph({"path": str(orders_workbook), "macro": "WriteTotals"})
    assert "ComputeTotal" in graph["calls"]
    assert "TaxRate" in graph["calls"]  # transitive
    assert "RefreshSummary" in graph["called_by"]


def test_get_data_flow_reports_reads_and_writes(orders_workbook: Path) -> None:
    flow = get_data_flow({"path": str(orders_workbook), "macro": "ComputeTotal"})
    assert flow["reads"] == ["Orders"]
    assert flow["writes"] == []


def test_missing_path_parameter_raises(orders_workbook: Path) -> None:
    with pytest.raises(ValueError, match="Missing required parameter"):
        list_sheets({})


def test_read_range_returns_cell_values(tmp_path: Path) -> None:
    from agenticharvest.domains.excel.synth import SheetSpec, build_macro_workbook

    book = build_macro_workbook(
        tmp_path / "data.xlsm",
        sheets=[SheetSpec("Data", headers=["Id", "Amount"], rows=[[1, 9.5], [2, 3.0]])],
        modules={"modA": 'Attribute VB_Name = "modA"\nSub Noop()\nEnd Sub\n'},
    )

    whole = read_range({"path": str(book), "sheet": "Data"})
    assert whole["rows"] == [["Id", "Amount"], [1, 9.5], [2, 3.0]]

    window = read_range({"path": str(book), "sheet": "Data", "range": "B2:B3"})
    assert window["rows"] == [[9.5], [3.0]]

    capped = read_range({"path": str(book), "sheet": "Data", "max_rows": 1})
    assert capped["rows"] == [["Id", "Amount"]]


def test_read_range_unknown_sheet_raises(orders_workbook: Path) -> None:
    with pytest.raises(ValueError, match="No sheet named"):
        read_range({"path": str(orders_workbook), "sheet": "Nope"})


# ----------------------------------------------------------------------
# Write tools
# ----------------------------------------------------------------------


def _workbook_copy(orders_workbook: Path, tmp_path: Path) -> Path:
    """A private copy of the shared workbook, safe to mutate in one test."""
    dest = tmp_path / "orders.xlsm"
    shutil.copyfile(orders_workbook, dest)
    return dest


def test_write_cell_changes_the_value_and_preserves_vba(
    orders_workbook: Path, tmp_path: Path
) -> None:
    book = _workbook_copy(orders_workbook, tmp_path)

    result = write_cell(
        {"path": str(book), "sheet": "Summary", "cell": "A1", "value": "Report"}
    )
    assert result["sheet"] == "Summary"
    assert result["cell"] == "A1"
    assert result["new_value"] == "Report"

    # The change is on disk, and the macro project survived the round-trip.
    from agenticharvest.domains.excel.backends.openpyxl import OpenpyxlBackend

    backend = OpenpyxlBackend(str(book))
    try:
        modules = backend.read_vba_modules()
    finally:
        backend.close()
    assert "modOrders" in modules

    # A second write sees the value the first one wrote as its old value.
    again = write_cell(
        {"path": str(book), "sheet": "Summary", "cell": "A1", "value": "Report 2"}
    )
    assert again["old_value"] == "Report"


def test_write_cell_unknown_sheet_raises(
    orders_workbook: Path, tmp_path: Path
) -> None:
    book = _workbook_copy(orders_workbook, tmp_path)
    with pytest.raises(ValueError, match="No sheet named"):
        write_cell(
            {"path": str(book), "sheet": "Nope", "cell": "A1", "value": 1}
        )


def test_write_cell_requires_value(orders_workbook: Path, tmp_path: Path) -> None:
    book = _workbook_copy(orders_workbook, tmp_path)
    with pytest.raises(ValueError, match="Missing required parameter: 'value'"):
        write_cell({"path": str(book), "sheet": "Summary", "cell": "A1"})


def test_run_macro_reports_no_execution_backend_when_absent(
    orders_workbook: Path,
) -> None:
    # No live Excel/xlwings on CI, so the tool honestly reports it cannot run
    # the macro rather than pretending. (When xlwings + Excel are present this
    # would instead run the macro; skip so the assertion stays deterministic.)
    from agenticharvest.domains.excel.backends.xlwings import XlwingsBackend

    if XlwingsBackend("x").available():
        pytest.skip("a live xlwings backend is installed; skip the absence path")

    with pytest.raises(RuntimeError, match="execution backend"):
        run_macro({"path": str(orders_workbook), "macro": "RefreshSummary"})


# ----------------------------------------------------------------------
# The dry-run gate now guards a real write
# ----------------------------------------------------------------------


def _excel_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec, handler in excel_tools():
        registry.register(spec, handler)
    return registry


def test_dispatch_blocks_write_cell_without_confirm(
    orders_workbook: Path, tmp_path: Path
) -> None:
    book = _workbook_copy(orders_workbook, tmp_path)
    request = ToolRequest(
        tool="write_cell",
        params={"path": str(book), "sheet": "Summary", "cell": "A1", "value": "X"},
    )

    result = dispatch(_excel_registry(), request)

    assert result.ok is False
    assert "confirm" in result.error


def test_dispatch_allows_confirmed_write_cell(
    orders_workbook: Path, tmp_path: Path
) -> None:
    book = _workbook_copy(orders_workbook, tmp_path)
    request = ToolRequest(
        tool="write_cell",
        params={
            "path": str(book),
            "sheet": "Summary",
            "cell": "A1",
            "value": "X",
            "confirm": True,
        },
    )

    result = dispatch(_excel_registry(), request)

    assert result.ok is True
    assert result.output["new_value"] == "X"
