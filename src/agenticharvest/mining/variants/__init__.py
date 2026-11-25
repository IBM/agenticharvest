#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Variant discovery — the distinct paths through the event log, ranked.

:class:`VariantMiner` is a pure-Python miner behind the same ``Miner`` protocol
as the bupaR bridge. It needs no R and no extra dependency, so mining works on
any install; what it gives up is BPMN discovery. It returns the three things a
process model always carries:

    activities        how often each activity occurred
    directly_follows  how often one activity was immediately followed by another
    variants          each distinct trace and how many cases followed it

Use it as the default when R is absent, or whenever a frequency-and-variant view
is all a caller needs (as the plan compiler in ``agenticharvest.orchestration.compile``
does). It is registered under the ``agenticharvest.miners`` entry point as ``variants``.
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise

from agenticharvest.core.events.model import OcelLog
from agenticharvest.mining.log import infer_case_type, traces
from agenticharvest.mining.model import Activity, ProcessModel, Transition, Variant


class VariantMiner:
    """Mine activities, the directly-follows relation, and variants in Python.

    Args:
        case_type: The object type to treat as the case. Defaults to the type
            the most events relate to (see :func:`agenticharvest.mining.log.flatten`).
    """

    name = "variants"

    def __init__(self, case_type: str | None = None) -> None:
        self._case_type = case_type

    def available(self) -> bool:
        """Always ``True``: this miner has no runtime beyond Python itself."""
        return True

    def mine(self, log: OcelLog) -> ProcessModel:
        """Mine ``log`` into a :class:`ProcessModel` (without a BPMN)."""
        case_type = self._case_type or infer_case_type(log)
        by_case = traces(log, case_type=case_type) if case_type else {}

        activity_counts: Counter[str] = Counter()
        follows: Counter[tuple[str, str]] = Counter()
        variant_counts: Counter[tuple[str, ...]] = Counter()
        for trace in by_case.values():
            activity_counts.update(trace)
            follows.update(pairwise(trace))
            variant_counts[tuple(trace)] += 1

        # most_common() is stable for ties, so equal counts keep first-seen
        # order and the output is deterministic for a given log.
        return ProcessModel(
            miner=self.name,
            n_cases=len(by_case),
            n_events=sum(activity_counts.values()),
            activities=[
                Activity(name=name, frequency=count)
                for name, count in activity_counts.most_common()
            ],
            directly_follows=[
                Transition(source=source, target=target, frequency=count)
                for (source, target), count in follows.most_common()
            ],
            variants=[
                Variant(sequence=list(sequence), frequency=count)
                for sequence, count in variant_counts.most_common()
            ],
        )


# The instance the `agenticharvest.miners` entry point points at.
miner = VariantMiner()

__all__ = ["VariantMiner", "miner"]
