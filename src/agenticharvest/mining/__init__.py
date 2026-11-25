#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Process mining — from evidence to a process model.

Consumes the OCEL event log (``core.events``) and discovers how the artifact is
actually used: the frequent variants, which steps need a human, and which are
safe to automate. The output is a process model that ``orchestration`` can
compile into an agent loop — closing the observe -> mine -> agentify loop.

The mining engine itself is pluggable: the event log is a stable input, and a
miner sits behind the ``Miner`` protocol. The default is the MIT-licensed bupaR
bridge (``miners.bupar``); users can bring their own (an external/commercial
engine, or a custom algorithm) without changing anything downstream.

Subpackages and modules:
    log           flatten an OCEL into the case-centric log a miner consumes
    miners        the pluggable mining engines (bupaR ships by default)
    variants      a pure-Python miner: activities, directly-follows, variants
    model         the compiled process model (the automatable artifact)
    conformance   replay a new trace against a model and report deviations
"""

from __future__ import annotations

from agenticharvest.mining.conformance import ConformanceReport, check_trace
from agenticharvest.mining.log import (
    ClassicEvent,
    collapse_repeats,
    flatten,
    infer_case_type,
    traces,
)
from agenticharvest.mining.miners import BupaRMiner, Miner, MinerError, best_miner
from agenticharvest.mining.model import ProcessModel
from agenticharvest.mining.variants import VariantMiner

__all__ = [
    "BupaRMiner",
    "ClassicEvent",
    "ConformanceReport",
    "Miner",
    "MinerError",
    "ProcessModel",
    "VariantMiner",
    "best_miner",
    "check_trace",
    "collapse_repeats",
    "flatten",
    "infer_case_type",
    "traces",
]
