#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The web domain's OCEL mapping.

``WebOCEL`` is the one place that knows a web tool call touches a *web app* made
of *resources* acted on by *operations*. Every call relates to its ``webapp``
object (the case a single-app miner keys on); a call naming an operation also
relates to that ``operation`` and to the ``resource`` it acts on — ``reads`` for
``call_operation``, ``writes`` for ``send_operation``, ``describes`` for
``describe_operation``.

Activities can be operation-qualified (``send_operation:createInvoice``),
so a mined process reads as business steps rather than "send, send, send".

Record ids are kept as event attributes, not objects: which ids a call touched
(path parameters, and the ``id`` of each record a response returned) lands in
``attributes["records"]``. Turning those into shared business objects — the
order or the employee that several systems touch — is the cross-system
correlator's job (:mod:`agenticharvest.core.events.correlate`), which the domain
does not need to know about.
"""

from __future__ import annotations

from typing import Any

from agenticharvest.core.events.mapping import EventMapping, GenericOcelMapper
from agenticharvest.core.events.model import Object, Relationship
from agenticharvest.core.tools.result import ToolResult

OBJ_WEBAPP = "webapp"
OBJ_RESOURCE = "resource"
OBJ_OPERATION = "operation"

_ENRICHED = frozenset(
    {"list_resources", "describe_operation", "get_field_flow", "call_operation", "send_operation"}
)
_ROLE = {"call_operation": "reads", "send_operation": "writes", "describe_operation": "describes"}

#: The most record ids recorded for one event, so a large listing stays small.
MAX_RECORDS = 50


class WebOCEL(GenericOcelMapper):
    """Maps web tool calls to webapp / resource / operation objects.

    Args:
        qualify_operations: When ``True``, a call naming an operation records its
            activity as ``tool:operation``. Off by default.
    """

    def __init__(self, *, qualify_operations: bool = False) -> None:
        self._qualify = qualify_operations

    def map(self, tool: str, params: dict, result: ToolResult) -> EventMapping:
        """Emit typed objects for a web tool; defer unknown tools to the base."""
        app = params.get("app")
        if tool not in _ENRICHED or not app:
            return super().map(tool, params, result)

        app = str(app)
        attributes: dict[str, Any] = {"ok": result.ok, "app": app}
        if result.error:
            attributes["error"] = result.error

        webapp = Object(id=webapp_id(app), type=OBJ_WEBAPP)
        objects: list[Object] = [webapp]
        relationships = [Relationship(object_id=webapp.id, qualifier="target")]

        name = str(params.get("name") or "")
        output = result.output if result.ok and isinstance(result.output, dict) else {}
        resource = str(output.get("resource") or "")
        if name:
            operation = Object(
                id=f"{OBJ_OPERATION}:{app}:{name}",
                type=OBJ_OPERATION,
                relationships=[Relationship(object_id=webapp.id, qualifier="belongs_to")],
            )
            objects.append(operation)
            relationships.append(Relationship(object_id=operation.id, qualifier="invokes"))
        if resource:
            target = Object(
                id=f"{OBJ_RESOURCE}:{app}:{resource}",
                type=OBJ_RESOURCE,
                relationships=[Relationship(object_id=webapp.id, qualifier="belongs_to")],
            )
            objects.append(target)
            relationships.append(
                Relationship(object_id=target.id, qualifier=_ROLE.get(tool, "reads"))
            )
            attributes["resource"] = resource

        records = record_ids(params, output)
        if records:
            attributes["records"] = records
        if "status" in output:
            attributes["status"] = output["status"]

        activity = f"{tool}:{name}" if self._qualify and name else tool
        return EventMapping(
            activity=activity,
            attributes=attributes,
            objects=objects,
            relationships=relationships,
        )


def webapp_id(app: str) -> str:
    """The object id of a configured web app."""
    return f"{OBJ_WEBAPP}:{app}"


def record_ids(params: dict, output: dict[str, Any]) -> list[str]:
    """The record ids a call named or a response returned, as strings.

    Path parameters are the records a call addressed. In the response, an
    ``id`` on the body, on each item of a list body, or on the items of a
    ``data`` envelope (the shape many REST APIs use) is a record it returned.
    A bare integer or a list of integers (what an RPC-style create returns) is
    a list of record ids too.
    """
    found: list[str] = []
    for value in (params.get("path_params") or {}).values():
        found.append(str(value))
    body = output.get("body")
    if isinstance(body, dict) and "data" in body:
        body = body["data"]
    items = body if isinstance(body, list) else [body]
    for item in items:
        if isinstance(item, dict) and item.get("id") is not None:
            found.append(str(item["id"]))
        elif isinstance(item, int) and not isinstance(item, bool):
            found.append(str(item))
    return list(dict.fromkeys(found))[:MAX_RECORDS]


__all__ = [
    "MAX_RECORDS",
    "OBJ_OPERATION",
    "OBJ_RESOURCE",
    "OBJ_WEBAPP",
    "WebOCEL",
    "record_ids",
    "webapp_id",
]
