#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for the REST channel via FastAPI's TestClient (no network)."""

from __future__ import annotations

import pytest

from agenticharvest.core.tools import ToolRegistry, ToolSpec

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="REST channel tests need the 'rest' extra"
)
TestClient = fastapi_testclient.TestClient

from agenticharvest.gateway.channels.rest import create_app


@pytest.fixture()
def client() -> TestClient:
    """A TestClient over an app serving one controlled echo tool."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="Echo the value back.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            domain="test",
        ),
        lambda p: {"echoed": p["value"]},
    )
    return TestClient(create_app(registry))


def test_get_tools_lists_specs(client: TestClient) -> None:
    response = client.get("/tools")
    assert response.status_code == 200
    body = response.json()
    assert [t["name"] for t in body] == ["echo"]
    assert body[0]["effect"] == "read"


def test_call_tool_success(client: TestClient) -> None:
    response = client.post("/tools/echo/call", json={"value": "hi"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["output"] == {"echoed": "hi"}


def test_call_unknown_tool_is_404(client: TestClient) -> None:
    response = client.post("/tools/missing/call", json={})
    assert response.status_code == 404


def test_handler_error_is_ok_false_not_500(client: TestClient) -> None:
    # Missing the required "value" -> the handler raises KeyError, which the
    # registry turns into a normal ok=false result (HTTP 200), not a crash.
    response = client.post("/tools/echo/call", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]
