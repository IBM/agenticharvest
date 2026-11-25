#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the pure-Python variant miner, conformance, and miner discovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agenticharvest.core.events.model import Event, Object, OcelLog, Relationship
from agenticharvest.mining import (
    Miner,
    VariantMiner,
    best_miner,
    check_trace,
    collapse_repeats,
    traces,
)
from agenticharvest.mining.miners import load_miners

_T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _log(cases: dict[str, list[str]]) -> OcelLog:
    """A log with one ``batch`` object per case and the given activity traces."""
    log = OcelLog()
    tick = 0
    for case, activities in cases.items():
        log.ensure_object(Object(id=case, type="batch"))
        for activity in activities:
            tick += 1
            log.add_event(
                Event(
                    id=f"e{tick}",
                    activity=activity,
                    time=_T0 + timedelta(minutes=tick),
                    relationships=[Relationship(object_id=case, qualifier="target")],
                )
            )
    return log


def test_variant_miner_satisfies_the_protocol() -> None:
    assert isinstance(VariantMiner(), Miner)
    assert VariantMiner().available() is True


def test_variant_miner_counts_activities_follows_and_variants() -> None:
    log = _log(
        {
            "b1": ["load", "check", "decide"],
            "b2": ["load", "check", "decide"],
            "b3": ["load", "decide"],
        }
    )
    model = VariantMiner().mine(log)

    assert model.miner == "variants"
    assert model.n_cases == 3
    assert model.n_events == 8
    assert {a.name: a.frequency for a in model.activities} == {
        "load": 3,
        "check": 2,
        "decide": 3,
    }
    follows = {(t.source, t.target): t.frequency for t in model.directly_follows}
    assert follows == {("load", "check"): 2, ("check", "decide"): 2, ("load", "decide"): 1}
    # Variants are ranked most frequent first.
    assert [(v.sequence, v.frequency) for v in model.variants] == [
        (["load", "check", "decide"], 2),
        (["load", "decide"], 1),
    ]
    assert model.bpmn is None


def test_variant_miner_on_an_empty_log() -> None:
    model = VariantMiner().mine(OcelLog())
    assert model.n_cases == 0
    assert model.variants == []


def test_traces_orders_each_case_by_time() -> None:
    log = _log({"b1": ["a", "b"], "b2": ["c"]})
    log.events.reverse()  # logged out of order; time still decides
    assert traces(log) == {"b2": ["c"], "b1": ["a", "b"]}


def test_collapse_repeats_folds_runs_per_case() -> None:
    log = _log(
        {
            "b1": ["load", "write", "write", "write", "verify"],
            "b2": ["load", "write", "verify"],
        }
    )
    collapsed = collapse_repeats(log)
    assert traces(collapsed) == {
        "b1": ["load", "write", "verify"],
        "b2": ["load", "write", "verify"],
    }
    # Batches of different size now share one variant.
    model = VariantMiner().mine(collapsed)
    assert [(v.sequence, v.frequency) for v in model.variants] == [
        (["load", "write", "verify"], 2)
    ]


def test_collapse_repeats_keeps_non_adjacent_repeats() -> None:
    log = _log({"b1": ["read", "write", "read"]})
    assert traces(collapse_repeats(log)) == {"b1": ["read", "write", "read"]}


def test_check_trace_reports_conformance() -> None:
    model = VariantMiner().mine(_log({"b1": ["load", "check", "decide"]}))

    exact = check_trace(model, ["load", "check", "decide"])
    assert exact.conforms and exact.known_variant
    assert exact.fitness == 1.0

    skipped = check_trace(model, ["load", "decide"])
    assert not skipped.conforms
    assert skipped.unseen_transitions == [("load", "decide")]
    assert skipped.fitness == 0.0

    novel = check_trace(model, ["load", "check", "escalate"])
    assert novel.unseen_steps == ["escalate"]
    assert novel.fitness == 0.5


def test_check_trace_single_step() -> None:
    model = VariantMiner().mine(_log({"b1": ["load"]}))
    assert check_trace(model, ["load"]).fitness == 1.0
    assert check_trace(model, ["other"]).fitness == 0.0
    assert check_trace(model, []).fitness == 0.0


def test_both_builtin_miners_are_discoverable() -> None:
    miners = load_miners()
    assert {"bupar", "variants"} <= set(miners)
    assert isinstance(miners["variants"], VariantMiner)
    # Whatever is installed, some miner is always usable.
    assert best_miner() is not None
