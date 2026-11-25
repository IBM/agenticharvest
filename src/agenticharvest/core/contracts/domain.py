#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The ``Domain`` protocol — what a source type implements to become first-class.

A domain is the one layer that knows what its artifact is (a workbook, a PDF, a
repository). It answers two questions the generic layers above it need: what
tools does it offer (``tool_specs``), and how does each one run (``handlers``).
Everything else — the registry, the gateway, the channels — is domain-agnostic
and drives a domain only through this protocol.

Keeping the protocol here, in ``core.contracts``, makes "what do I build to go
beyond Excel?" a single file to read: implement ``Domain`` and register it under
the ``agenticharvest.domains`` entry point.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agenticharvest.core.tools.registry import Handler
from agenticharvest.core.tools.spec import ToolSpec


@runtime_checkable
class Domain(Protocol):
    """A source type that contributes tools over its own artifact.

    Attributes:
        name: The domain's identifier, e.g. ``excel``. Recorded on the tools it
            contributes so callers can tell which domain served a call.
    """

    name: str

    def tool_specs(self) -> list[ToolSpec]:
        """Return the declarative specs for every tool the domain offers."""
        ...

    def handlers(self) -> dict[str, Handler]:
        """Return the handler for each tool, keyed by ``ToolSpec.name``."""
        ...
