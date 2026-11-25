#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The ``Miner`` protocol — the seam every mining engine implements.

A miner takes the object-centric event log and returns a ``ProcessModel``. That
is the whole contract: how the model is discovered (a subprocess to R, a pure-
Python algorithm, a call to a commercial service) is the miner's business, and
nothing downstream depends on it. This mirrors the ``Domain`` protocol — the one
place mining is pluggable, discovered via the ``agenticharvest.miners`` entry point.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agenticharvest.core.events.model import OcelLog
from agenticharvest.mining.model import ProcessModel


class MinerError(RuntimeError):
    """A miner could not produce a model (engine missing, or it failed).

    Carries a human-readable message — typically what is missing and how to
    install it — so a caller can surface actionable guidance rather than a
    bare traceback.
    """


@runtime_checkable
class Miner(Protocol):
    """Turns an event log into a process model.

    Attributes:
        name: A short identifier for the engine, e.g. ``"bupar"``.
    """

    name: str

    def available(self) -> bool:
        """Return whether the engine's runtime is installed and usable.

        Callers check this to skip cleanly (in tests, or to fall back to another
        miner) rather than triggering a :class:`MinerError` from :meth:`mine`.
        """
        ...

    def mine(self, log: OcelLog) -> ProcessModel:
        """Mine ``log`` into a :class:`ProcessModel`, or raise :class:`MinerError`."""
        ...
