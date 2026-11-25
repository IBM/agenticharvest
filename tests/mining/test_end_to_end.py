#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""End-to-end: real tools + real backend + the process miner, one pipeline.

This is the whole loop in a single test, with nothing mocked out:

    dispatch (gateway) ─▶ excel tools (real handlers, real openpyxl backend)
                        ─▶ OcelTap + ExcelOCEL (one call → one OCEL event)
                        ─▶ flatten + BupaRMiner (Rscript) → ProcessModel

An agent session is simulated as a sequence of tool calls against one workbook:
it inspects the workbook (four read tools), then mutates it (a confirmed
``write_cell``, which really edits the file on disk through the openpyxl
backend, having passed the dry-run gate). Every call is observed by the tap into
an object-centric log keyed on the ``workbook``, and that log is mined into a
process model.

The point the test pins down: the *write* tool shows up in the mined process on
the same case as the reads — so the discovered process is "inspect, then write",
end to end, with the real backend doing the write. The mining leg needs R; when
R/bupaR is absent the test still exercises dispatch → tap → log and only skips
the assertions that require a mined model, mirroring the miner's own graceful
degradation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# The whole pipeline test needs the Excel extra to build and read a workbook;
# skip cleanly (rather than erroring on import) on a core-only install.
openpyxl = pytest.importorskip(
    "openpyxl", reason="the end-to-end pipeline test needs the 'excel' extra"
)
pytest.importorskip("oletools", reason="the end-to-end pipeline test needs the 'excel' extra")

from agenticharvest.core.events.model import OcelLog
from agenticharvest.core.events.tap import OcelTap
from agenticharvest.core.tools import ToolRegistry
from agenticharvest.domains.excel.ocel import ExcelOCEL
from agenticharvest.domains.excel.samples import build_orders_workbook
from agenticharvest.domains.excel.tools import excel_tools
from agenticharvest.gateway.dispatch import ToolRequest, dispatch
from agenticharvest.mining.log import flatten, infer_case_type
from agenticharvest.mining.miners.bupar import BupaRMiner

_T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    """A fresh, writable orders workbook for one test (the session mutates it)."""
    return build_orders_workbook(tmp_path / "orders.xlsm")


def _steady_clock():
    """A clock that ticks one minute per call, so event order is deterministic.

    Wall-clock ``now()`` can collapse several fast calls onto the same instant,
    which would make the mined directly-follows order ambiguous. A monotonic
    fake keeps the trace stable without changing anything the tools do.
    """
    state = {"n": 0}

    def tick() -> datetime:
        stamp = _T0 + timedelta(minutes=state["n"])
        state["n"] += 1
        return stamp

    return tick


def _observed_registry(log: OcelLog) -> ToolRegistry:
    """A registry over the real Excel tools with an ExcelOCEL tap attached."""
    registry = ToolRegistry(
        taps=[OcelTap(mapper=ExcelOCEL(), log=log, clock=_steady_clock())]
    )
    for spec, handler in excel_tools():
        registry.register(spec, handler)
    return registry


def _run_session(registry: ToolRegistry, book: Path) -> None:
    """Drive one workbook session: inspect the workbook, then write to it.

    Each step goes through the gateway's ``dispatch`` — the same path a channel
    uses — so the dry-run gate is really in force (the write carries
    ``confirm=True``; without it dispatch would block it).
    """
    path = str(book)
    steps = [
        ToolRequest(tool="list_sheets", params={"path": path}),
        ToolRequest(tool="get_call_graph", params={"path": path, "macro": "WriteTotals"}),
        ToolRequest(tool="get_data_flow", params={"path": path, "macro": "ComputeTotal"}),
        ToolRequest(
            tool="write_cell",
            params={
                "path": path,
                "sheet": "Summary",
                "cell": "B1",
                "value": 4242,
                "confirm": True,
            },
        ),
    ]
    for request in steps:
        result = dispatch(registry, request)
        assert result.ok is True, f"{request.tool} failed: {result.error}"


def test_pipeline_dispatch_tap_and_log(workbook: Path) -> None:
    """The read tools and the real write land as events on one workbook case."""
    book = workbook

    log = OcelLog()
    _run_session(_observed_registry(log), book)

    # The real backend actually changed the file — this is not a mock.
    workbook = openpyxl.load_workbook(book, keep_vba=True)
    try:
        assert workbook["Summary"]["B1"].value == 4242
    finally:
        vba_archive = getattr(workbook, "vba_archive", None)
        if vba_archive is not None:
            vba_archive.close()
        workbook.close()

    # One event per call, all four keyed on the same workbook object.
    assert [e.activity for e in log.events] == [
        "list_sheets",
        "get_call_graph",
        "get_data_flow",
        "write_cell",
    ]
    assert infer_case_type(log) == "workbook"

    # The write event relates to the workbook (so it shares the reads' case) and
    # to the Summary sheet it mutated (so the mutation target is recorded).
    write_event = log.events[-1]
    workbook_id = f"workbook:{book}"
    sheet_id = f"sheet:{book}:Summary"
    related = {rel.object_id: rel.qualifier for rel in write_event.relationships}
    assert related.get(workbook_id) == "target"
    assert related.get(sheet_id) == "writes"

    # Flattening keeps the write on the workbook case — it does not fall through
    # to a separate generic ``artifact`` object and get dropped.
    rows = flatten(log, case_type="workbook")
    assert [r.activity for r in rows] == [
        "list_sheets",
        "get_call_graph",
        "get_data_flow",
        "write_cell",
    ]
    assert {r.case_id for r in rows} == {workbook_id}


def test_pipeline_mines_the_session_into_a_process(workbook: Path) -> None:
    """The observed session mines into a process whose trace includes the write."""
    miner = BupaRMiner()
    if not miner.available():
        pytest.skip("R with the bupaR package is not installed")

    book = workbook

    log = OcelLog()
    _run_session(_observed_registry(log), book)

    model = miner.mine(log)

    # One workbook, four tool calls, and every activity — including the write —
    # is present in the discovered process.
    assert model.n_cases == 1
    assert model.n_events == 4
    names = {a.name for a in model.activities}
    assert {
        "list_sheets",
        "get_call_graph",
        "get_data_flow",
        "write_cell",
    } <= names

    # The directly-follows relation recovers "inspect, then write": the reads
    # flow in order and the session ends on the mutation.
    edges = {(t.source, t.target) for t in model.directly_follows}
    assert ("list_sheets", "get_call_graph") in edges
    assert ("get_call_graph", "get_data_flow") in edges
    assert ("get_data_flow", "write_cell") in edges
