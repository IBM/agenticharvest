#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The web tool specs and their handlers.

Five tools read the app's description or its data, and one changes it:

    list_resources      the resources the app exposes and their operations   (read)
    describe_operation  one operation: method, path, parameters, body fields (read)
    get_field_flow       which operations produce and consume a field         (read)
    call_operation      call a read operation (GET, or an Odoo read method)  (read)
    send_operation      call a write operation (POST/PUT/PATCH/DELETE)       (write, gated)

Every tool takes ``app``, the configured name of a web app (see
:mod:`agenticharvest.domains.web.config`). The two calling tools take an operation
``name`` plus ``path_params``, ``query``, and ``body``, and refuse an operation
whose effect does not match the tool: ``call_operation`` will not run a write,
so a read-only grant really is read-only. ``send_operation`` carries
``Effect.WRITE``, so the gateway's dry-run gate demands ``confirm=true``.

Clients are created per app on first use and cached for the process, along with
the app's IR. ``list_resources`` with ``refresh=true`` reloads the description
when the app's schema changed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from agenticharvest.core.tools.spec import Effect, ToolSpec
from agenticharvest.domains.web.client import WebClient
from agenticharvest.domains.web.config import WebAppConfig
from agenticharvest.domains.web.ir import Operation

_CLIENTS: dict[str, WebClient] = {}


def client_for(app: str) -> WebClient:
    """Return the cached client for ``app``, configuring it from the environment."""
    if app not in _CLIENTS:
        _CLIENTS[app] = WebClient(WebAppConfig.from_env(app))
    return _CLIENTS[app]


def register_client(client: WebClient) -> None:
    """Use ``client`` for its app instead of one built from the environment."""
    previous = _CLIENTS.get(client.config.name)
    if previous is not None and previous is not client:
        previous.close()
    _CLIENTS[client.config.name] = client


def _require(params: dict, name: str) -> str:
    value = params.get(name)
    if not value:
        raise ValueError(f"Missing required parameter: '{name}'.")
    return str(value)


def _operation(params: dict) -> tuple[WebClient, Operation]:
    client = client_for(_require(params, "app"))
    name = _require(params, "name")
    operation = client.ir().operation(name)
    if operation is None:
        raise ValueError(
            f"No operation named '{name}' in app '{client.config.name}'. "
            "Call list_resources to see what it offers."
        )
    return client, operation


# ----------------------------------------------------------------------
# Read handlers
# ----------------------------------------------------------------------


def list_resources(params: dict) -> dict[str, Any]:
    """Return every resource with its operations, each marked read or write."""
    client = client_for(_require(params, "app"))
    ir = client.ir(refresh=bool(params.get("refresh")))
    wanted = params.get("resource")
    by_name = {op.name: op for op in ir.operations}
    return {
        "app": ir.app,
        "title": ir.title,
        "version": ir.version,
        "resources": [
            {
                "name": resource.name,
                "title": resource.title,
                "operations": [
                    {
                        "name": name,
                        "method": by_name[name].method,
                        "effect": "write" if by_name[name].writes else "read",
                        "summary": by_name[name].summary,
                    }
                    for name in resource.operations
                ],
            }
            for resource in ir.resources
            if not wanted or resource.name == wanted
        ],
    }


def describe_operation(params: dict) -> dict[str, Any]:
    """Return one operation in full, plus the fields its resource carries."""
    client, operation = _operation(params)
    resource = client.ir().resource(operation.resource)
    return {
        **operation.model_dump(),
        "effect": "write" if operation.writes else "read",
        "resource_fields": [f.model_dump() for f in resource.fields] if resource else [],
    }


def get_field_flow(params: dict) -> dict[str, Any]:
    """Return the operations that produce a field's values and those that take it."""
    client = client_for(_require(params, "app"))
    field = _require(params, "field")
    ir = client.ir()
    return {
        "field": field,
        "produced_by": ir.producers(field),
        "consumed_by": ir.consumers(field),
    }


def call_operation(params: dict) -> dict[str, Any]:
    """Call one read operation and return its response."""
    client, operation = _operation(params)
    if operation.writes:
        raise ValueError(
            f"'{operation.name}' changes state ({operation.method.upper()}); "
            "use send_operation with confirm=true."
        )
    return _invoke(client, operation, params)


# ----------------------------------------------------------------------
# Write handler
#
# ``confirm`` is consumed by the gateway's dry-run gate before this runs.
# ----------------------------------------------------------------------


def send_operation(params: dict) -> dict[str, Any]:
    """Call one write operation and return its response."""
    client, operation = _operation(params)
    if not operation.writes:
        raise ValueError(f"'{operation.name}' is a read; use call_operation.")
    return _invoke(client, operation, params)


def _invoke(client: WebClient, operation: Operation, params: dict) -> dict[str, Any]:
    path_params = params.get("path_params") or {}
    if not isinstance(path_params, dict):
        raise TypeError("Parameter 'path_params' must be an object.")
    missing = [p for p in operation.path_params if p not in path_params]
    if missing:
        raise ValueError(f"Missing path parameter(s) for '{operation.name}': {', '.join(missing)}.")
    path = operation.path
    for key, value in path_params.items():
        # Quote every value so a parameter cannot add path segments of its own.
        path = path.replace("{" + key + "}", quote(str(value), safe=""))
    query = params.get("query")
    if query is not None and not isinstance(query, dict):
        raise TypeError("Parameter 'query' must be an object.")
    body = params.get("body")
    _check_body(operation, body)
    response = client.request(operation.method, path, query=query, body=body)
    if not response.ok:
        raise RuntimeError(
            f"{operation.name} returned HTTP {response.status}: {_brief(response.body)}"
        )
    return {
        "app": client.config.name,
        "operation": operation.name,
        "resource": operation.resource,
        "status": response.status,
        "body": response.body,
        "truncated": response.truncated,
        "headers": response.headers,
    }


def _check_body(operation: Operation, body: Any) -> None:
    """Refuse a body that is missing required fields or, if closed, has unknown ones.

    APIs differ in how they treat a wrong body: some reject it, and some
    succeed while doing nothing (an RPC ``write`` without ``ids`` updates no
    records and still returns true). Checking against the description first
    turns both into an error that names the fields the operation takes.
    """
    if operation.body_kind != "object" or not operation.body_fields:
        return
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise TypeError(f"'{operation.name}' takes a JSON object body.")
    names = [f.name for f in operation.body_fields]
    missing = [f.name for f in operation.body_fields if f.required and f.name not in body]
    unknown = [k for k in body if k not in names] if operation.closed_body else []
    if missing or unknown:
        problems = []
        if missing:
            problems.append(f"missing {', '.join(missing)}")
        if unknown:
            problems.append(f"unknown {', '.join(unknown)}")
        raise ValueError(
            f"Body for '{operation.name}' is {'; '.join(problems)}. "
            f"It takes: {', '.join(names)}."
        )


def _brief(body: Any) -> str:
    text = body if isinstance(body, str) else str(body)
    return text[:400]


# ----------------------------------------------------------------------
# Specs, paired with their handlers
# ----------------------------------------------------------------------

_APP_PROP = {"app": {"type": "string", "description": "The configured web app name."}}
_NAME_PROP = {"name": {"type": "string", "description": "The operation name."}}
_CALL_PROPS = {
    "path_params": {
        "type": "object",
        "description": "Values for the operation's {placeholders}, by name.",
    },
    "query": {"type": "object", "description": "Query parameters, by name."},
    "body": {"description": "The JSON request body, when the operation takes one."},
}


def web_tools() -> list[tuple[ToolSpec, Any]]:
    """Return the web domain's ``(spec, handler)`` pairs."""
    return [
        (
            ToolSpec(
                name="list_resources",
                description="List the resources a web app exposes and the operations on "
                "each, marked read or write.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_APP_PROP,
                        "resource": {"type": "string", "description": "Only this resource."},
                        "refresh": {
                            "type": "boolean",
                            "description": "Reload the app's API description first.",
                        },
                    },
                    "required": ["app"],
                },
            ),
            list_resources,
        ),
        (
            ToolSpec(
                name="describe_operation",
                description="Describe one web operation: its method, path, parameters, "
                "and body fields.",
                input_schema={
                    "type": "object",
                    "properties": {**_APP_PROP, **_NAME_PROP},
                    "required": ["app", "name"],
                },
            ),
            describe_operation,
        ),
        (
            ToolSpec(
                name="get_field_flow",
                description="List the web operations that produce a field's values and "
                "those that take it as input.",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_APP_PROP,
                        "field": {"type": "string", "description": "The field name."},
                    },
                    "required": ["app", "field"],
                },
            ),
            get_field_flow,
        ),
        (
            ToolSpec(
                name="call_operation",
                description="Call a read operation on a web app and return its response.",
                input_schema={
                    "type": "object",
                    "properties": {**_APP_PROP, **_NAME_PROP, **_CALL_PROPS},
                    "required": ["app", "name"],
                },
            ),
            call_operation,
        ),
        (
            ToolSpec(
                name="send_operation",
                description="Call a write operation on a web app (create, update, or "
                "delete) and return its response.",
                effect=Effect.WRITE,
                input_schema={
                    "type": "object",
                    "properties": {
                        **_APP_PROP,
                        **_NAME_PROP,
                        **_CALL_PROPS,
                        "confirm": {
                            "type": "boolean",
                            "description": "Must be true to apply this change.",
                        },
                    },
                    "required": ["app", "name"],
                },
            ),
            send_operation,
        ),
    ]


__all__ = ["client_for", "register_client", "web_tools"]
