#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Domain scaffolding shared by every domain.

Provides the base ``Domain`` implementation (``SimpleDomain``, satisfying
``core.contracts.Domain``) and the discovery logic that loads built-in and
entry-point domains into a ``ToolRegistry``. A new domain author assembles a
``SimpleDomain`` here rather than reinventing wiring.

Public API:
    SimpleDomain            assemble a domain from (spec, handler) pairs
    register_domain         register one domain's tools into a registry
    load_entry_point_domains  load every ``agenticharvest.domains`` entry point
"""

from agenticharvest.domains.base.domain import SimpleDomain
from agenticharvest.domains.base.registry import (
    DOMAIN_ENTRY_POINT_GROUP,
    load_entry_point_domains,
    register_domain,
)

__all__ = [
    "DOMAIN_ENTRY_POINT_GROUP",
    "SimpleDomain",
    "load_entry_point_domains",
    "register_domain",
]
