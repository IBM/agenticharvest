#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The base domain assembly shared by every domain.

``SimpleDomain`` is the concrete ``Domain`` a domain author assembles rather than
reinventing wiring: hand it a name and a list of ``(spec, handler)`` pairs and it
satisfies the ``core.contracts.Domain`` protocol. Nothing here knows about any
specific artifact — the Excel domain, or any future one, builds its specs and
handlers elsewhere and composes them into one of these.
"""

from __future__ import annotations

from agenticharvest.core.tools.registry import Handler
from agenticharvest.core.tools.spec import ToolSpec


class SimpleDomain:
    """A ``Domain`` assembled from a name and ``(spec, handler)`` pairs.

    Args:
        name: The domain's identifier (also stamped onto each spec's ``domain``
            field if the spec left it blank).
        tools: The tools the domain offers, as ``(ToolSpec, handler)`` pairs.
    """

    def __init__(self, name: str, tools: list[tuple[ToolSpec, Handler]]) -> None:
        self.name = name
        self._specs: list[ToolSpec] = []
        self._handlers: dict[str, Handler] = {}
        for spec, handler in tools:
            # Stamp the domain onto specs that did not set it, so a caller can
            # always tell which domain served a tool without the author repeating
            # the name on every spec.
            if not spec.domain:
                spec = spec.model_copy(update={"domain": name})
            self._specs.append(spec)
            self._handlers[spec.name] = handler

    def tool_specs(self) -> list[ToolSpec]:
        """Return the specs for every tool this domain offers."""
        return list(self._specs)

    def handlers(self) -> dict[str, Handler]:
        """Return the handler for each tool, keyed by tool name."""
        return dict(self._handlers)
