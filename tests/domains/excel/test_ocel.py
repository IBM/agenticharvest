#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for ExcelOCEL — the Excel domain's OCEL mapping.

Drives the real Excel tools through a registry with an ``OcelTap`` wired to
``ExcelOCEL``, then inspects the object-centric log the calls produced.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agenticharvest.core.events import OcelTap
from agenticharvest.core.tools import ToolRegistry, ToolResult
from agenticharvest.domains.base import register_domain
from agenticharvest.domains.excel import ExcelOCEL, domain


def _registry_with_ocel() -> tuple[ToolRegistry, OcelTap]:
    tap = OcelTap(mapper=ExcelOCEL())
    registry = ToolRegistry(taps=[tap])
    register_domain(registry, domain)
    return registry, tap


def _qualifiers(event, prefix: str) -> set[str]:
    """Return the element names an event relates to via ``prefix:`` object ids."""
    return {
        r.object_id.split(":")[-1]
        for r in event.relationships
        if r.object_id.startswith(prefix)
    }


def test_get_data_flow_relates_reads_and_writes(orders_workbook: Path) -> None:
    registry, tap = _registry_with_ocel()
    path = str(orders_workbook)

    registry.call("get_data_flow", {"path": path, "macro": "WriteTotals"})

    event = tap.log.events[-1]
    assert event.activity == "get_data_flow"
    assert event.attributes["ok"] is True

    # The workbook is the target; the subject macro and the sheets it writes are
    # related with role-naming qualifiers a miner can group on.
    by_qualifier = {(r.object_id.split(":")[0], r.qualifier) for r in event.relationships}
    assert ("workbook", "target") in by_qualifier
    assert ("macro", "subject") in by_qualifier
    writes = {r.object_id.split(":")[-1] for r in event.relationships if r.qualifier == "writes"}
    assert "Summary" in writes


def test_get_call_graph_relates_callees_and_callers(orders_workbook: Path) -> None:
    registry, tap = _registry_with_ocel()
    path = str(orders_workbook)

    registry.call("get_call_graph", {"path": path, "macro": "WriteTotals"})

    event = tap.log.events[-1]
    calls = {r.object_id.split(":")[-1] for r in event.relationships if r.qualifier == "calls"}
    called_by = {
        r.object_id.split(":")[-1] for r in event.relationships if r.qualifier == "called_by"
    }
    assert "ComputeTotal" in calls
    assert "Workbook_Open" in called_by


def test_objects_are_typed_and_deduplicated(orders_workbook: Path) -> None:
    registry, tap = _registry_with_ocel()
    path = str(orders_workbook)

    # Two calls that both touch the same workbook and overlapping macros.
    registry.call("get_call_graph", {"path": path, "macro": "WriteTotals"})
    registry.call("get_data_flow", {"path": path, "macro": "WriteTotals"})

    types = set(tap.log.object_types())
    assert {"workbook", "macro"} <= types

    # The workbook and the WriteTotals macro each appear once, not per call.
    workbook_ids = [o.id for o in tap.log.objects if o.type == "workbook"]
    assert len(workbook_ids) == 1
    write_totals = [
        o for o in tap.log.objects if o.type == "macro" and o.id.endswith(":WriteTotals")
    ]
    assert len(write_totals) == 1
    # Sheets and macros link back to their workbook (object-to-object).
    assert any(r.qualifier == "belongs_to" for r in write_totals[0].relationships)


def test_failed_call_records_attempt_against_workbook(orders_workbook: Path) -> None:
    registry, tap = _registry_with_ocel()
    path = str(orders_workbook)

    registry.call("get_data_flow", {"path": path, "macro": "NoSuchMacro"})

    event = tap.log.events[-1]
    assert event.attributes["ok"] is False
    assert "error" in event.attributes
    # Only the workbook is related — a failed call has no output to expand.
    assert [r.qualifier for r in event.relationships] == ["target"]


def test_write_cell_relates_the_written_sheet(
    orders_workbook: Path, tmp_path: Path
) -> None:
    # The write really mutates the file, so work on a private copy.
    book = tmp_path / "orders.xlsm"
    shutil.copyfile(orders_workbook, book)

    registry, tap = _registry_with_ocel()
    registry.call(
        "write_cell",
        {"path": str(book), "sheet": "Summary", "cell": "B1", "value": 1},
    )

    event = tap.log.events[-1]
    assert event.activity == "write_cell"
    assert event.attributes["ok"] is True
    # The write is on the workbook case (so it mines with the reads) and names
    # the sheet it mutated.
    by_qualifier = {(r.object_id.split(":")[0], r.qualifier) for r in event.relationships}
    assert ("workbook", "target") in by_qualifier
    writes = {r.object_id.split(":")[-1] for r in event.relationships if r.qualifier == "writes"}
    assert writes == {"Summary"}


def test_run_macro_relates_the_executed_macro(orders_workbook: Path) -> None:
    # run_macro needs a live engine, so exercise the mapping directly with a
    # successful MacroRun result rather than requiring Excel to be installed.
    mapping = ExcelOCEL().map(
        "run_macro",
        {"path": str(orders_workbook), "macro": "RefreshSummary"},
        ToolResult.success(
            {"macro": "RefreshSummary", "args": [], "returned": None}
        ),
    )
    assert mapping.activity == "run_macro"
    # The workbook is the target and the executed macro is related as "runs".
    quals = {(r.object_id.split(":")[0], r.qualifier) for r in mapping.relationships}
    assert ("workbook", "target") in quals
    assert ("macro", "runs") in quals
    runs = {r.object_id.split(":")[-1] for r in mapping.relationships if r.qualifier == "runs"}
    assert runs == {"RefreshSummary"}


def test_read_range_relates_the_read_sheet(orders_workbook: Path) -> None:
    path = str(orders_workbook)
    params = {"path": path, "sheet": "Orders"}
    result = ToolResult.success({"sheet": "Orders", "range": None, "rows": []})

    plain = ExcelOCEL().map("read_range", params, result)
    assert plain.activity == "read_range"
    reads = {r.object_id.split(":")[-1] for r in plain.relationships if r.qualifier == "reads"}
    assert reads == {"Orders"}

    # Opting in qualifies the activity with the sheet, so a mined process can
    # tell "read Orders" from "read Config".
    qualified = ExcelOCEL(qualify_sheets=True).map("read_range", params, result)
    assert qualified.activity == "read_range:Orders"
    # Tools that do not act on one sheet stay unqualified.
    listing = ExcelOCEL(qualify_sheets=True).map(
        "list_sheets", {"path": path}, ToolResult.success([])
    )
    assert listing.activity == "list_sheets"


def test_unknown_tool_falls_back_to_generic_mapping(orders_workbook: Path) -> None:
    # A tool ExcelOCEL does not special-case is mapped as a generic artifact.
    mapping = ExcelOCEL().map(
        "some_other_tool",
        {"path": str(orders_workbook)},
        ToolResult.success(None),
    )
    assert [o.type for o in mapping.objects] == ["artifact"]


def test_end_to_end_serializes_to_ocel2(orders_workbook: Path) -> None:
    registry, tap = _registry_with_ocel()
    path = str(orders_workbook)

    registry.call("list_sheets", {"path": path})
    registry.call("list_macros", {"path": path})
    registry.call("get_data_flow", {"path": path, "macro": "ComputeTotal"})

    doc = tap.log.to_ocel2()
    assert {"objectTypes", "eventTypes", "events", "objects"} == set(doc)
    assert len(doc["events"]) == 3
    assert "workbook" in {t["name"] for t in doc["objectTypes"]}
    # Every event serializes an OCEL "type" (the activity) and a timestamp.
    assert all("type" in e and "time" in e for e in doc["events"])
