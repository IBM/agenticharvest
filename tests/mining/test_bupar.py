#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the bupaR miner bridge.

Split in two: hermetic unit tests that never need R (CSV shaping, the missing-R
error path, the shipped entry-point instance), and one integration test that
actually runs ``Rscript`` — skipped cleanly unless R and bupaR are installed.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agenticharvest.core.events.model import Event, Object, OcelLog, Relationship
from agenticharvest.mining.miners.base import Miner, MinerError
from agenticharvest.mining.miners.bupar import BupaRMiner
from agenticharvest.mining.miners.bupar import miner as entry_point_miner

_T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _session_log() -> OcelLog:
    log = OcelLog()
    log.ensure_object(Object(id="workbook:book.xlsm", type="workbook"))
    for i, activity in enumerate(["list_sheets", "get_call_graph", "get_data_flow"]):
        log.add_event(
            Event(
                id=f"e{i + 1}",
                activity=activity,
                time=_T0 + timedelta(minutes=i),
                relationships=[Relationship(object_id="workbook:book.xlsm", qualifier="target")],
            )
        )
    return log


# ----------------------------------------------------------------------
# Hermetic unit tests (no R required)
# ----------------------------------------------------------------------


def test_bupar_satisfies_miner_protocol() -> None:
    assert isinstance(BupaRMiner(), Miner)


def test_entry_point_exposes_a_ready_instance() -> None:
    # The object the `agenticharvest.miners` entry point resolves to.
    assert isinstance(entry_point_miner, BupaRMiner)
    assert entry_point_miner.name == "bupar"


def test_bundled_r_script_ships_with_the_package() -> None:
    script = Path(BupaRMiner()._script)
    assert script.name == "bupar.R"
    assert script.is_file()


def test_write_csv_shapes_the_flattened_log(tmp_path: Path) -> None:
    from agenticharvest.mining.log import flatten

    rows = flatten(_session_log(), case_type="workbook")
    out = tmp_path / "log.csv"
    BupaRMiner._write_csv(out, rows)

    parsed = list(csv.DictReader(out.read_text().splitlines()))
    assert [r["activity"] for r in parsed] == [
        "list_sheets",
        "get_call_graph",
        "get_data_flow",
    ]
    assert all(r["case_id"] == "workbook:book.xlsm" for r in parsed)
    # No resource column when no event carried a resource.
    assert "resource" not in parsed[0]
    # Timestamps serialize as OCEL ISO-8601 (trailing Z).
    assert parsed[0]["timestamp"] == "2026-01-02T03:04:05Z"


def test_mine_raises_a_helpful_error_when_rscript_is_missing() -> None:
    miner = BupaRMiner(rscript="agenticharvest-no-such-rscript-xyz")
    assert miner.available() is False
    with pytest.raises(MinerError) as excinfo:
        miner.mine(_session_log())
    assert "not found on PATH" in str(excinfo.value)
    assert "bupaverse" in str(excinfo.value)


def test_mine_empty_log_returns_empty_model_without_touching_r() -> None:
    # No case objects -> nothing to mine -> no subprocess, no error.
    miner = BupaRMiner(rscript="agenticharvest-no-such-rscript-xyz")
    model = miner.mine(OcelLog())
    assert model.miner == "bupar"
    assert model.activities == []


# ----------------------------------------------------------------------
# Live integration (requires R + bupaR; skipped otherwise)
# ----------------------------------------------------------------------


def test_mine_a_session_with_real_bupar() -> None:
    miner = BupaRMiner()
    if not miner.available():
        pytest.skip("R with the bupaR package is not installed")

    model = miner.mine(_session_log())

    assert model.n_cases == 1
    assert model.n_events == 3
    names = {a.name for a in model.activities}
    assert {"list_sheets", "get_call_graph", "get_data_flow"} <= names
    # The directly-follows relation recovers the tool-call order.
    edges = {(t.source, t.target) for t in model.directly_follows}
    assert ("list_sheets", "get_call_graph") in edges
    assert ("get_call_graph", "get_data_flow") in edges
