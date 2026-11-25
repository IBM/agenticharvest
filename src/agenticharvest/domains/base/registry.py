#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Domain discovery — load built-in and entry-point domains into a registry.

A domain becomes available to the gateway by registering under the
``agenticharvest.domains`` entry point (declared in ``pyproject.toml``). This module
walks that group, imports each ``domain`` object, and registers its specs +
handlers into a ``ToolRegistry``. Third-party packages extend AgenticHarvest "beyond
Excel" purely by adding their own entry point — no core change required.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from agenticharvest.core.contracts.domain import Domain
from agenticharvest.core.tools.registry import ToolRegistry

# The entry-point group third-party domains register under. Mirrors the
# ``[project.entry-points."agenticharvest.domains"]`` table in pyproject.toml.
DOMAIN_ENTRY_POINT_GROUP = "agenticharvest.domains"


def register_domain(registry: ToolRegistry, domain: Domain) -> None:
    """Register every tool a single domain contributes into ``registry``.

    Args:
        registry: The registry to populate.
        domain: A ``Domain`` (its ``tool_specs`` bound to its ``handlers`` by
            tool name).

    Raises:
        KeyError: If a spec names a tool the domain provides no handler for.
    """
    handlers = domain.handlers()
    for spec in domain.tool_specs():
        registry.register(spec, handlers[spec.name])


def load_entry_point_domains(registry: ToolRegistry) -> list[str]:
    """Load every domain registered under the ``agenticharvest.domains`` entry point.

    Args:
        registry: The registry to populate.

    Returns:
        The names of the domains that were loaded, in discovery order.
    """
    loaded: list[str] = []
    for ep in entry_points(group=DOMAIN_ENTRY_POINT_GROUP):
        domain: Domain = ep.load()
        register_domain(registry, domain)
        loaded.append(getattr(domain, "name", ep.name))
    return loaded
