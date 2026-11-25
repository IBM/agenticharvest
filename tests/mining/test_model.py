#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the process model and its bupaR-JSON parsing."""

from __future__ import annotations

from agenticharvest.mining.model import ProcessModel


def _bupar_payload(with_bpmn: bool = False) -> dict:
    payload = {
        "miner": "bupaR",
        "n_cases": 1,
        "n_events": 3,
        "activities": [
            {"name": "list_sheets", "frequency": 1},
            {"name": "get_data_flow", "frequency": 2},
        ],
        "directly_follows": [
            {"source": "list_sheets", "target": "get_data_flow", "frequency": 1},
        ],
        "variants": [
            {"sequence": ["list_sheets", "get_data_flow"], "frequency": 1},
        ],
        "bpmn": None,
    }
    if with_bpmn:
        payload["bpmn"] = {
            "nodes": [
                {"id": "n1", "name": "list_sheets", "type": "task"},
                {"id": "g1", "name": "", "type": "gateway"},
            ],
            "flows": [{"source": "n1", "target": "g1"}],
        }
    return payload


def test_from_bupar_parses_stable_results() -> None:
    model = ProcessModel.from_bupar(_bupar_payload())

    assert model.miner == "bupaR"
    assert model.n_cases == 1
    assert model.n_events == 3
    assert [a.name for a in model.activities] == ["list_sheets", "get_data_flow"]
    assert model.activities[1].frequency == 2
    assert model.directly_follows[0].source == "list_sheets"
    assert model.directly_follows[0].target == "get_data_flow"
    assert model.variants[0].sequence == ["list_sheets", "get_data_flow"]
    assert model.bpmn is None


def test_from_bupar_parses_discovered_bpmn() -> None:
    model = ProcessModel.from_bupar(_bupar_payload(with_bpmn=True))

    assert model.bpmn is not None
    assert [n.id for n in model.bpmn.nodes] == ["n1", "g1"]
    assert model.bpmn.nodes[0].type == "task"
    assert model.bpmn.flows[0].source == "n1"
    assert model.bpmn.flows[0].target == "g1"


def test_from_bupar_tolerates_missing_fields() -> None:
    # A minimal payload (only a miner name) still yields a valid, empty model.
    model = ProcessModel.from_bupar({"miner": "bupaR"})

    assert model.n_cases == 0
    assert model.activities == []
    assert model.directly_follows == []
    assert model.variants == []
    assert model.bpmn is None
