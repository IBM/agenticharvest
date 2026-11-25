#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Adapt gateway tools onto the model-callable ``Tool`` interface.

An agent backend drives anything that subclasses ``Tool``; a AgenticHarvest domain
contributes ``ToolSpec`` + handler pairs to a ``ToolRegistry``. These adapters
bridge the two, so any registered domain can be handed straight to an agent:

    registry_tools(registry)   in-process — each call goes through ``dispatch``
                               (dry-run gate + taps), no server needed
    rest_tools(client)         over HTTP — fetch ``GET /tools`` from a running
                               gateway and invoke ``POST /tools/{name}/call``

Both routes apply the same policy and feed the same taps, so an agent's calls are
observed identically whichever one is used.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from agenticharvest.core.tools.registry import ToolRegistry
from agenticharvest.core.tools.result import ToolResult
from agenticharvest.core.tools.spec import ToolSpec
from agenticharvest.gateway.dispatch import ToolRequest, dispatch
from agenticharvest.orchestration.agents.base import Tool


def _render(result: ToolResult) -> str:
    """Render a ``ToolResult`` as the string a model reads back."""
    if not result.ok:
        return f"error: {result.error}"
    return json.dumps(result.output, default=str)


class RegistryTool(Tool):
    """One registry tool, callable by an agent in-process through ``dispatch``."""

    def __init__(self, registry: ToolRegistry, spec: ToolSpec) -> None:
        self._registry = registry
        self._spec = spec

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def description(self) -> str:
        return self._spec.description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._spec.input_schema

    def run(self, arguments: dict[str, Any]) -> str:
        request = ToolRequest(tool=self._spec.name, params=arguments)
        return _render(dispatch(self._registry, request))


def registry_tools(registry: ToolRegistry) -> list[Tool]:
    """Wrap every tool in ``registry`` as a callable ``Tool``."""
    return [RegistryTool(registry, spec) for spec in registry.list_specs()]


class HttpClient(Protocol):
    """The slice of an ``httpx.Client`` the REST adapter uses.

    ``httpx.Client`` and FastAPI's ``TestClient`` both satisfy it, so the same
    adapter drives a remote gateway or an in-process app under test.
    """

    def get(self, url: str) -> Any: ...

    def post(self, url: str, *, json: Any) -> Any: ...


class RestTool(Tool):
    """One gateway tool, callable by an agent through the REST channel."""

    def __init__(self, client: HttpClient, spec: dict[str, Any]) -> None:
        self._client = client
        self._name = spec["name"]
        self._description = spec["description"]
        self._input_schema = spec["input_schema"]

    @property
    def name(self) -> str:
        return str(self._name)

    @property
    def description(self) -> str:
        return str(self._description)

    @property
    def input_schema(self) -> dict[str, Any]:
        return dict(self._input_schema)

    def run(self, arguments: dict[str, Any]) -> str:
        response = self._client.post(f"/tools/{self._name}/call", json=arguments)
        return _render(ToolResult.model_validate(response.json()))


def rest_tools(client: HttpClient) -> list[Tool]:
    """Fetch a gateway's advertised tools and wrap each as a callable ``Tool``.

    Args:
        client: An HTTP client whose base URL is the gateway, e.g.
            ``httpx.Client(base_url="http://127.0.0.1:8000")``.
    """
    specs = client.get("/tools").json()
    return [RestTool(client, spec) for spec in specs]
