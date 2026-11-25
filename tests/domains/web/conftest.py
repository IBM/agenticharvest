#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Fixtures for the web domain tests: live apps, or a clean skip.

Each fixture yields a client for one app configured in ``.env`` (see
``examples/systems``). An app that is not configured, or not answering, skips
the tests that need it rather than failing them, so the suite stays green on a
machine without the stack.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("httpx", reason="web domain tests need the 'web' extra")

from agenticharvest.domains.web.client import WebClient, WebRequestError
from agenticharvest.domains.web.config import WebAppConfig, WebConfigError

ROOT = Path(__file__).resolve().parents[3]

#: The official Keycloak Admin REST API description examples/systems/setup.py saves.
KEYCLOAK_SPEC = ROOT / "examples" / "systems" / "specs" / "keycloak-26.4.7.json"


def live_client(app: str) -> Iterator[WebClient]:
    """Yield a client for ``app`` once the live server answers, or skip.

    Loading the description is not enough: an app whose description is a local
    file (Keycloak's) loads it with the server down. So the client also sends
    one request to the server itself.
    """
    try:
        config = WebAppConfig.from_env(app, env_file=ROOT / ".env")
    except WebConfigError as exc:
        pytest.skip(f"live app '{app}' not configured: {exc}")
    client = WebClient(config)
    try:
        client.ir()
        client.request("get", "/")
    except (WebRequestError, OSError) as exc:
        client.close()
        pytest.skip(f"live app '{app}' not reachable: {exc}")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="module")
def directus() -> Iterator[WebClient]:
    yield from live_client("directus")


@pytest.fixture(scope="module")
def keycloak() -> Iterator[WebClient]:
    yield from live_client("keycloak")


@pytest.fixture(scope="module")
def odoo() -> Iterator[WebClient]:
    yield from live_client("odoo")


@pytest.fixture(scope="module")
def keycloak_spec() -> dict:
    if not KEYCLOAK_SPEC.is_file():
        pytest.skip("run examples/systems/setup.py to save the Keycloak API description")
    import json

    return json.loads(KEYCLOAK_SPEC.read_text(encoding="utf-8"))
