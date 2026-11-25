#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Miners — the pluggable process-mining engine.

The mining source is pluggable, exactly like a domain. A ``Miner`` takes the
OCEL event log and returns a process model; AgenticHarvest does not mandate one engine.
Wrap an external/commercial miner, plug in a custom algorithm, or opt into a
third-party adapter — all behind the ``Miner`` protocol.

The default engine is :class:`~agenticharvest.mining.miners.bupar.BupaRMiner`, a bridge
to the MIT-licensed R ``bupaverse``. It is registered under the ``agenticharvest.miners``
entry point but stays dormant unless used — R is only invoked when ``mine()`` is
called, and the miner reports :meth:`~...BupaRMiner.available` ``False`` when R
is absent, so nothing requires R to be installed.

Note on pm4py: it is a capable engine but ships under the AGPL-3.0 license, so
AgenticHarvest neither depends on it, bundles it, nor registers it. Users who have
reviewed its terms can install it themselves and register their own adapter
under the ``agenticharvest.miners`` entry point (see the README).

Miners are discovered two ways:
    - built-ins imported from this package (bupaR ships by default; the
      pure-Python ``agenticharvest.mining.variants`` miner needs no R at all)
    - third-party packages registered under the ``agenticharvest.miners`` entry point

Contents:
    base        the Miner protocol + MinerError (event log -> process model)
    bupar       the bupaR miner package (Python bridge + bundled R script)
    registry    entry-point discovery; ``best_miner`` picks a usable engine
"""

from __future__ import annotations

from agenticharvest.mining.miners.base import Miner, MinerError
from agenticharvest.mining.miners.bupar import BupaRMiner
from agenticharvest.mining.miners.registry import best_miner, load_miners

__all__ = ["BupaRMiner", "Miner", "MinerError", "best_miner", "load_miners"]
