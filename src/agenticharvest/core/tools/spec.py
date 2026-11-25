#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The declarative tool spec.

A ``ToolSpec`` is the stable, serializable description of one capability: its
name, what it does, the JSON Schema for its parameters, whether it only reads or
also writes, and which domain contributed it. It is exactly what a REST client
sees over the wire and what an agent turns into a model-facing tool declaration.
The spec is backend-independent — the registry binds a spec to a handler, and
the gateway reads ``effect`` to decide whether a call needs confirmation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Effect(str, Enum):
    """Whether a tool only observes the artifact or changes it.

    The gateway's dry-run gate keys off this: ``WRITE`` tools must be confirmed
    before they run, ``READ`` tools pass straight through.
    """

    READ = "read"
    WRITE = "write"


class ToolSpec(BaseModel):
    """The declarative contract for one tool.

    Attributes:
        name: Unique snake_case identifier, e.g. ``list_sheets``.
        description: One plain sentence stating what the tool does. Shown to a
            model in its tool declaration, so it stays a capability statement —
            no implementation detail.
        input_schema: JSON Schema object describing the tool's parameters.
        effect: ``Effect.READ`` (default) or ``Effect.WRITE``. Write tools are gated.
        domain: The domain that contributed this tool, e.g. ``excel``.
    """

    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)
    effect: Effect = Effect.READ
    domain: str = ""
