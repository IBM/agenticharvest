#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The bupaR miner package.

Bundles the :class:`BupaRMiner` (the Python side of the bridge) and the
self-contained ``bupar.R`` script it invokes. ``miner`` is a ready-to-use
instance, and the object the ``agenticharvest.miners`` entry point resolves to.
"""

from __future__ import annotations

from agenticharvest.mining.miners.bupar.miner import BupaRMiner

# The instance the `agenticharvest.miners` entry point points at. Constructing it does
# no work and touches no runtime — R is only invoked when mine() is called.
miner = BupaRMiner()

__all__ = ["BupaRMiner", "miner"]
