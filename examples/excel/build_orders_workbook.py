#!/usr/bin/env python
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
"""Build the "orders" demo workbook and print the IR AgenticHarvest extracts from it.

Run it::

    python examples/excel/build_orders_workbook.py [OUTPUT.xlsm]

It synthesizes a genuine macro-bearing ``.xlsm`` (no Excel or LibreOffice
needed — see :mod:`agenticharvest.domains.excel.synth`), then runs :func:`build_ir`
and prints the recovered workbook IR: the sheets and their data-flow roles, the
macros and the call graph between them, the read/write flow between macros and
sheets, the named ranges, and the clickable controls.

Extraction here is entirely static — parsing structure with openpyxl and VBA
with oletools, then inferring the call graph and data flow by pattern analysis.
No LLM is involved, and none is needed: the workflow is recovered deterministically
from the file. An LLM would only come in later, to *describe* or *act on* what
was extracted, not to extract it.

The workbook is the small "orders" workflow:

    Orders (input) --read--> ComputeTotal --> WriteTotals --write--> Summary (output)
    Config (input) --read--> TaxRate
    Workbook_Open (event) --> RefreshSummary --> {ClearSummary, WriteTotals}
"""

from __future__ import annotations

import sys
from pathlib import Path

from agenticharvest.domains.excel.ir import WorkbookIR, build_ir

# The one canonical demo-workbook definition, shipped with the package and shared
# with the tests, so the example and the fixtures can never disagree.
from agenticharvest.domains.excel.samples import build_orders_workbook


def _print_ir(ir: WorkbookIR) -> None:
    """Print a readable summary of the recovered workbook IR."""
    print("\nSheets (name, code name, role, headers)")
    print("-" * 60)
    for sheet in ir.sheets():
        print(
            f"  {sheet.name:8}  {sheet.codename!s:9}  {sheet.role.value:9}  "
            f"{sheet.headers}"
        )

    print("\nMacros (name, module, event handler?)")
    print("-" * 60)
    for macro in ir.macros():
        flag = "  [event]" if macro.is_event_handler else ""
        print(f"  {macro.name:16}  {macro.module!s:12}{flag}")

    print("\nCall graph (caller -> callee)")
    print("-" * 60)
    for edge in sorted(ir.graph.edges_of_kind("calls"), key=lambda e: (e.source, e.target)):
        print(f"  {edge.source:16} -> {edge.target}")

    print("\nData flow (macro reads/writes which sheet)")
    print("-" * 60)
    for macro in ir.macros():
        reads = ir.reads(macro.name)
        writes = ir.writes(macro.name)
        if reads or writes:
            print(f"  {macro.name:16}  reads={reads or '-'}  writes={writes or '-'}")

    print("\nNamed ranges")
    print("-" * 60)
    for name, named in ir.named_ranges.items():
        print(f"  {name:10} -> {named.sheet}!{named.ref}")

    print("\nControls")
    print("-" * 60)
    for button in ir.buttons:
        print(f"  {button.kind:10} on {button.sheet}!{button.cell}  '{button.label}'")

    print("\nWorkflow reachable from Workbook_Open")
    print("-" * 60)
    print("  " + " -> ".join(["Workbook_Open", *ir.call_chain("Workbook_Open")]))


def main(argv: list[str]) -> int:
    dest = Path(argv[1]) if len(argv) > 1 else Path("orders.xlsm")
    path = build_orders_workbook(dest)
    print(f"Built macro-enabled workbook: {path}")

    ir = build_ir(path)
    _print_ir(ir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
