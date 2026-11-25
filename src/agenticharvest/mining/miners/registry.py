#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Miner discovery — pick an engine from the ``agenticharvest.miners`` entry point.

Every miner registers under ``agenticharvest.miners`` (declared in ``pyproject.toml``),
the same way domains and backends do. ``load_miners`` imports them all;
``best_miner`` returns the first one that is usable in this environment, so a
caller that just wants "a process model" gets bupaR when R is installed and the
pure-Python variant miner otherwise — without naming either.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from agenticharvest.mining.miners.base import Miner

# The entry-point group miners register under. Mirrors the
# ``[project.entry-points."agenticharvest.miners"]`` table in pyproject.toml.
MINER_ENTRY_POINT_GROUP = "agenticharvest.miners"

# Preferred order among the built-ins: the richer engine first. Third-party
# miners follow in discovery order.
_PREFERENCE = ("bupar", "variants")


def load_miners() -> dict[str, Miner]:
    """Return every registered miner, keyed by entry-point name.

    A miner whose entry point fails to import is skipped rather than allowed to
    break discovery for the others.
    """
    found: dict[str, Miner] = {}
    for ep in entry_points(group=MINER_ENTRY_POINT_GROUP):
        try:
            found[ep.name] = ep.load()
        except Exception:  # noqa: BLE001, S112 - one bad plugin must not break the rest
            continue
    return found


def best_miner() -> Miner | None:
    """Return the most capable miner available here, or ``None`` if there is none."""
    miners = load_miners()
    ordered = [miners[n] for n in _PREFERENCE if n in miners]
    ordered += [m for n, m in miners.items() if n not in _PREFERENCE]
    return next((m for m in ordered if m.available()), None)


__all__ = ["MINER_ENTRY_POINT_GROUP", "best_miner", "load_miners"]
