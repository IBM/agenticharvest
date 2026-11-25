#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Shared fixtures for the orchestration tests."""

from __future__ import annotations

import pytest

from agenticharvest.utils.llm import LLMConfig, LLMConfigError


@pytest.fixture(scope="session")
def llm_config() -> LLMConfig:
    """Load endpoint config from .env / env, skipping if none is configured.

    Used by the opt-in ``real_llm`` tests so they skip cleanly anywhere no live
    endpoint is available, rather than failing.
    """
    try:
        return LLMConfig.from_env()
    except LLMConfigError as exc:
        pytest.skip(f"No LLM endpoint configured: {exc}")
