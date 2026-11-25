#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the ``agenticharvest`` CLI channel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenticharvest.gateway.channels.cli import _parse_params, main


def test_parse_params_reads_json_values_and_falls_back_to_strings() -> None:
    assert _parse_params(["n=3", "flag=true", "args=[1, 2]", "sql=SELECT 1"]) == {
        "n": 3,
        "flag": True,
        "args": [1, 2],
        "sql": "SELECT 1",
    }
    with pytest.raises(SystemExit):
        _parse_params(["no-equals-sign"])


def test_tools_lists_every_domain(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "list_tables" in out
    assert "query" in out


def test_sample_then_call_round_trips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "support.db"
    assert main(["sample", "sqlite", str(db)]) == 0
    capsys.readouterr()

    assert main(["call", "query", f"path={db}", "sql=SELECT COUNT(*) FROM agents"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["output"]["rows"] == [[5]]


def test_call_honors_the_dry_run_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "support.db"
    main(["sample", "sqlite", str(db)])
    capsys.readouterr()
    write = ["call", "execute", f"path={db}", "sql=UPDATE agents SET open_load = 0"]

    assert main(write) == 1
    assert "confirm" in json.loads(capsys.readouterr().out)["error"]

    assert main([*write, "--confirm"]) == 0
    assert json.loads(capsys.readouterr().out)["output"]["rows_changed"] == 5
