#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Unit tests for the pure VBA static analysis.

These exercise :mod:`agenticharvest.domains.excel.ir.vba` on plain strings — no
workbook, no backend, no optional deps — because the analysis is deliberately
engine-agnostic. They pin the call-graph and data-flow heuristics precisely so a
future refactor cannot quietly change what the IR infers.
"""

from __future__ import annotations

from agenticharvest.domains.excel.ir.vba import (
    build_call_edges,
    infer_data_flow,
    module_of,
    parse_macros,
)

# A compact module that covers the cases the analysis must get right.
_MODULE = '''\
Attribute VB_Name = "mod1"
Public Sub Runner()
    LoadData
    total = Compute()
End Sub

Sub LoadData()
    shInput.Range("A1").Value = 0
End Sub

Function Compute() As Double
    Compute = shInput.Range("A1").Value
End Function
'''


def test_parse_macros_finds_every_procedure() -> None:
    macros = parse_macros({"mod1": _MODULE})
    names = [m.name for m in macros]
    assert names == ["Runner", "LoadData", "Compute"]
    # Module is carried through, and the header line is not read as a call.
    assert {m.module for m in macros} == {"mod1"}


def test_parse_macros_flags_event_handlers() -> None:
    src = (
        'Attribute VB_Name = "ThisWorkbook"\n'
        "Private Sub Workbook_Open()\nDoThing\nEnd Sub\n"
        "Sub DoThing()\nEnd Sub\n"
    )
    macros = parse_macros({"ThisWorkbook": src})
    by_name = {m.name: m for m in macros}
    assert by_name["Workbook_Open"].is_event_handler is True
    assert by_name["DoThing"].is_event_handler is False


def test_build_call_edges_resolves_only_known_macros() -> None:
    macros = parse_macros({"mod1": _MODULE})
    edges = build_call_edges(macros)
    # Runner calls both; Compute/LoadData call nothing known. `total` and other
    # locals/built-ins are dropped because they name no macro.
    assert edges == [("Runner", "Compute"), ("Runner", "LoadData")]


def test_build_call_edges_skips_self_reference() -> None:
    # A Function that assigns to its own name (the VBA return idiom) must not
    # produce a self-edge.
    macros = parse_macros({"mod1": _MODULE})
    assert not any(src == dst for src, dst in build_call_edges(macros))


def test_infer_data_flow_by_codename() -> None:
    macros = parse_macros({"mod1": _MODULE})
    flows = infer_data_flow(
        macros, codename_map={"shInput": "Input"}, sheet_names=["Input"]
    )
    # LoadData assigns .Value -> write; Compute reads .Value -> read.
    assert flows["LoadData"] == {"reads": [], "writes": ["Input"]}
    assert flows["Compute"] == {"reads": ["Input"], "writes": []}
    # Runner touches no sheet directly, so it is omitted entirely.
    assert "Runner" not in flows


def test_infer_data_flow_by_literal_sheet_name() -> None:
    src = (
        'Attribute VB_Name = "m"\n'
        'Function Name1() As String\n'
        '    Name1 = Sheets("Config").Range("A1").Value\n'
        "End Function\n"
    )
    macros = parse_macros({"m": src})
    flows = infer_data_flow(macros, codename_map={}, sheet_names=["Config"])
    assert flows["Name1"] == {"reads": ["Config"], "writes": []}


def test_infer_data_flow_ignores_unknown_literal_sheet() -> None:
    # A literal that does not name a real sheet is not recorded.
    src = (
        'Attribute VB_Name = "m"\n'
        "Sub S()\n"
        '    x = Sheets("Nope").Range("A1").Value\n'
        "End Sub\n"
    )
    macros = parse_macros({"m": src})
    assert infer_data_flow(macros, {}, ["Config"]) == {}


def test_infer_data_flow_recognizes_clear_as_write() -> None:
    src = (
        'Attribute VB_Name = "m"\n'
        "Sub Wipe()\n"
        '    shOut.Range("A1:A9").ClearContents\n'
        "End Sub\n"
    )
    macros = parse_macros({"m": src})
    flows = infer_data_flow(macros, {"shOut": "Out"}, ["Out"])
    assert flows["Wipe"]["writes"] == ["Out"]


def test_infer_data_flow_does_not_spill_a_write_into_the_previous_statement() -> None:
    # The read of shIn sits one line above a write to shOut; only shOut is written.
    src = (
        'Attribute VB_Name = "m"\n'
        "Sub Copy()\n"
        '    x = shIn.Range("A1").Value\n'
        '    shOut.Range("A1").Value = x\n'
        "End Sub\n"
    )
    macros = parse_macros({"m": src})
    flows = infer_data_flow(macros, {"shIn": "In", "shOut": "Out"}, ["In", "Out"])
    assert flows["Copy"] == {"reads": ["In"], "writes": ["Out"]}


def test_module_of() -> None:
    macros = parse_macros({"mod1": _MODULE})
    assert module_of("Compute", macros) == "mod1"
    assert module_of("Missing", macros) is None
