#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The domain-agnostic backbone.

Everything here is stable and knows nothing about spreadsheets. This is the
layer that lets AgenticHarvest go beyond Excel: a new domain plugs into these contracts
and inherits the tool registry, event log, and tracing for free.

The bring-your-own LLM client lives outside core, in ``agenticharvest.utils.llm``, so a
provider swap is never a core change.

Subpackages:
    contracts   the protocols every domain/backend/tool must satisfy
    ir          base intermediate-representation abstractions (graph, dataflow)
    tools       the stable tool contract, registry, and the tap point
    events      the OCEL event log — the evidence process mining consumes
    tracing     TraceSink: one tool call = one OpenTelemetry span
"""
