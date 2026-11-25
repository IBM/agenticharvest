#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Opt-in: the cross-system examples' agent stages, end to end on live systems.

Deselected by default; run them explicitly with::

    pytest tests/examples/test_cross_system_agents.py -m real_llm -v -s

Each test runs an example's ``run.py --agent`` exactly as a reader would. The
script checks the agent's work in the live systems and its trace, and exits
non-zero when any check fails, so the test asserts the exit code and shows
the transcript. It skips when no LLM endpoint or no live stack is configured.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agenticharvest.utils.llm import LLMConfig, LLMConfigError

pytestmark = pytest.mark.real_llm

_ROOT = Path(__file__).resolve().parents[2]


def _run(example: str, tmp_path: Path, apps: list[str]) -> None:
    if not (_ROOT / "examples" / example / "run.py").is_file():
        pytest.skip(f"examples/{example} is a submodule; run: git submodule update --init")
    try:
        LLMConfig.from_env(_ROOT / ".env")
    except LLMConfigError as exc:
        pytest.skip(f"No LLM endpoint configured: {exc}")
    pytest.importorskip("httpx", reason="needs the 'web' extra")
    from agenticharvest.domains.web.client import WebClient, WebRequestError
    from agenticharvest.domains.web.config import WebAppConfig, WebConfigError

    for app in apps:
        try:
            client = WebClient(WebAppConfig.from_env(app, env_file=_ROOT / ".env"))
            client.ir()
            client.request("get", "/")  # the server itself, not just its description
        except (WebConfigError, WebRequestError, OSError) as exc:
            pytest.skip(f"live app '{app}' unavailable: {exc}")

    done = subprocess.run(
        [sys.executable, f"examples/{example}/run.py", "--agent", "--out", str(tmp_path)],
        cwd=_ROOT, capture_output=True, text=True, timeout=900, check=False,
    )
    print(done.stdout[-6000:], done.stderr[-2000:])
    assert done.returncode == 0, "the example's own checks failed; see the transcript above"


def test_order_fulfillment_agent(tmp_path: Path) -> None:
    _run("order_fulfillment", tmp_path, ["directus"])


def test_hire_to_onboard_agent(tmp_path: Path) -> None:
    _run("hire_to_onboard", tmp_path, ["odoo", "keycloak", "directus"])
