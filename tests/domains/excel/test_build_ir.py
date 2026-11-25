#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""End-to-end extraction: a synthesized .xlsm -> WorkbookIR.

This is the integration test the whole Excel path exists to satisfy: given a
genuine macro-bearing workbook, ``build_ir`` must recover the sheets and their
data-flow roles, the macros and which are event handlers, the call graph, the
read/write data flow between macros and sheets, the named ranges, and the
clickable controls. Every assertion below traces back to the workbook defined in
:mod:`agenticharvest.domains.excel.samples`.
"""

from __future__ import annotations

from pathlib import Path

from agenticharvest.domains.excel.ir import SheetRole, build_ir


def test_build_ir_recovers_sheets_and_roles(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    roles = {s.name: s.role for s in ir.sheets()}
    # Orders and Config are only ever read; Summary is written by a macro.
    assert roles["Orders"] == SheetRole.INPUT
    assert roles["Config"] == SheetRole.INPUT
    assert roles["Summary"] == SheetRole.OUTPUT


def test_build_ir_recovers_macros_and_event_handler(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    macros = {m.name: m for m in ir.macros()}
    assert set(macros) == {
        "RefreshSummary",
        "ClearSummary",
        "WriteTotals",
        "ComputeTotal",
        "TaxRate",
        "ConfigName",
        "Workbook_Open",
    }
    assert macros["Workbook_Open"].is_event_handler is True
    assert macros["RefreshSummary"].is_event_handler is False
    assert macros["Workbook_Open"].module == "ThisWorkbook"
    assert macros["ComputeTotal"].module == "modOrders"


def test_build_ir_recovers_call_graph(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    edges = sorted((e.source, e.target) for e in ir.graph.edges_of_kind("calls"))
    assert edges == [
        ("ComputeTotal", "TaxRate"),
        ("RefreshSummary", "ClearSummary"),
        ("RefreshSummary", "WriteTotals"),
        ("Workbook_Open", "RefreshSummary"),
        ("WriteTotals", "ComputeTotal"),
    ]


def test_build_ir_call_chain_is_transitive(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    # From the workbook-open handler, the whole workflow is reachable.
    chain = set(ir.call_chain("Workbook_Open"))
    assert chain == {
        "RefreshSummary",
        "ClearSummary",
        "WriteTotals",
        "ComputeTotal",
        "TaxRate",
    }
    # Walking upstream from a leaf reaches the handler.
    assert "Workbook_Open" in ir.call_chain("TaxRate", direction="callers")


def test_build_ir_recovers_data_flow(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    # Reads: by code name (Orders/Config) and by literal name (Config).
    assert ir.reads("ComputeTotal") == ["Orders"]
    assert ir.reads("TaxRate") == ["Config"]
    assert ir.reads("ConfigName") == ["Config"]
    # Writes: both go to Summary; neither reads it.
    assert ir.writes("ClearSummary") == ["Summary"]
    assert ir.writes("WriteTotals") == ["Summary"]


def test_build_ir_reverse_data_flow_queries(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    assert sorted(ir.writers_of("Summary")) == ["ClearSummary", "WriteTotals"]
    assert sorted(ir.readers_of("Config")) == ["ConfigName", "TaxRate"]
    assert ir.readers_of("Orders") == ["ComputeTotal"]
    # Summary is a pure output: nothing reads it.
    assert ir.readers_of("Summary") == []


def test_build_ir_recovers_named_range(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    assert "TaxCell" in ir.named_ranges
    named = ir.named_ranges["TaxCell"]
    assert named.sheet == "Config"
    assert named.ref == "$A$1"


def test_build_ir_recovers_hyperlink_control(orders_workbook: Path) -> None:
    ir = build_ir(orders_workbook)
    assert len(ir.buttons) == 1
    button = ir.buttons[0]
    assert button.sheet == "Summary"
    assert button.cell == "D2"
    assert button.kind == "hyperlink"
