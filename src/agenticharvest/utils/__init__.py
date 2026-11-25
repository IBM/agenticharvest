#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Cross-cutting utilities.

Shared helpers that are not part of the domain-agnostic backbone in
``agenticharvest.core`` (IR, tools, events, tracing) but are used across the library —
notably the bring-your-own LLM client abstraction. Keeping these here, rather
than in ``core``, keeps ``core`` focused on the process-mining spine and lets
provider integrations evolve independently.

Subpackages:
    llm     a provider-agnostic model-client interface + optional adapters
"""
