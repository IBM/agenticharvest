#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The tool-call → OCEL mapping seam.

The tap owns *when* an event is recorded; a mapper owns *what* that event means —
which typed objects the call touched, and in what role. This split is the
invariant at work: the tap and the log are generic, and the one piece that knows
"a workbook has sheets and macros" is a domain-supplied mapper
(``agenticharvest.domains.excel.ocel.ExcelOCEL``).

``GenericOcelMapper`` is the domain-agnostic default and the base class domains
extend: it records the tool as the event activity, attaches the artifact named by
the call's ``path`` parameter as a single object, and carries the call's success
or error as event attributes. A domain mapper overrides ``map`` for the tools it
understands and falls back to ``super().map`` for the rest.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agenticharvest.core.events.model import OBJ_ARTIFACT, Object, Relationship
from agenticharvest.core.tools.result import ToolResult


class EventMapping(BaseModel):
    """What one tool call contributes to the log: an event and its objects.

    Attributes:
        activity: The event type to record. Defaults to the tool name; a domain
            may refine it.
        attributes: Static attributes to record on the event.
        objects: The objects the call touched, to be registered in the log. Each
            carries its full type and attributes so the first sighting is
            complete.
        relationships: Event-to-object relationships for this event, each naming
            an object's role via its qualifier.
    """

    activity: str
    attributes: dict = Field(default_factory=dict)
    objects: list[Object] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


@runtime_checkable
class OcelMapper(Protocol):
    """Turns one tool call into an :class:`EventMapping`.

    Implementations must not raise — the tap treats mapping as observation, not
    as a gate. A mapper that cannot interpret a call should fall back to a
    generic mapping rather than fail.
    """

    def map(self, tool: str, params: dict, result: ToolResult) -> EventMapping:
        """Return the event and objects this call contributes."""
        ...


class GenericOcelMapper:
    """The domain-agnostic default mapper, and the base domains extend.

    It records the tool name as the activity, attaches the artifact identified by
    the call's ``path`` parameter as one object of type ``artifact``, relates the
    event to it with the ``"target"`` qualifier, and records whether the call
    succeeded (plus any error message) as event attributes. A domain subclass
    overrides ``map`` for the tools it understands and calls ``super().map`` for
    everything else.
    """

    def map(self, tool: str, params: dict, result: ToolResult) -> EventMapping:
        """Map any tool call generically; see the class docstring."""
        attributes: dict = {"ok": result.ok}
        if result.error:
            attributes["error"] = result.error

        objects: list[Object] = []
        relationships: list[Relationship] = []
        path = params.get("path")
        if path:
            artifact_id = self.artifact_id(str(path))
            objects.append(Object(id=artifact_id, type=OBJ_ARTIFACT))
            relationships.append(
                Relationship(object_id=artifact_id, qualifier="target")
            )

        return EventMapping(
            activity=tool,
            attributes=attributes,
            objects=objects,
            relationships=relationships,
        )

    @staticmethod
    def artifact_id(path: str) -> str:
        """Return the object id for the artifact at ``path``.

        Namespaced by type so a domain that derives sheet or macro ids from the
        same path cannot collide with the artifact object itself.
        """
        return f"{OBJ_ARTIFACT}:{path}"
