#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Provider-agnostic LLM connection config.

``LLMConfig`` holds the three things any OpenAI-compatible endpoint needs: a base
URL, an API key, and a model name. It never names a specific provider or model —
those come entirely from the environment at the call site, so the library stays
vendor-neutral and no credential is ever baked into the code.

``from_env`` reads a ``.env`` file with a tiny inline parser (no new dependency)
and then the process environment, so a developer can drop credentials in a
``.env`` and a deployment can set real environment variables — the standard
precedence, environment over file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

# Primary variable names, matching the repo convention. Provider-neutral: they
# say "an OpenAI-compatible endpoint", not which one.
_ENV_KEYS = {
    "api_base": ("API_BASE", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
    "api_key": ("API_KEY", "OPENAI_API_KEY"),
    "model": ("MODEL", "OPENAI_MODEL"),
}


class LLMConfigError(RuntimeError):
    """Raised when required LLM configuration is missing.

    Callers (notably tests) catch this to skip cleanly when no endpoint is
    configured, rather than failing.
    """


class LLMConfig(BaseModel):
    """Connection settings for an OpenAI-compatible chat endpoint.

    Attributes:
        api_base: The endpoint base URL (e.g. ``https://.../v1``). The Chat
            Completions path is appended by the client.
        api_key: The bearer token sent as ``Authorization``.
        model: The model identifier to request.
    """

    api_base: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, env_file: Path | str | None = None) -> LLMConfig:
        """Build a config from a ``.env`` file (if present) and the environment.

        Args:
            env_file: Path to a ``.env`` file. Defaults to ``.env`` in the
                current working directory. A missing file is not an error.

        Returns:
            A populated ``LLMConfig``.

        Raises:
            LLMConfigError: If any of base URL, key, or model cannot be found.
        """
        file_values = read_env_file(Path(env_file) if env_file else Path(".env"))

        resolved: dict[str, str] = {}
        missing: list[str] = []
        for field, names in _ENV_KEYS.items():
            value = _first(names, os.environ, file_values)
            if value is None:
                missing.append(names[0])
            else:
                resolved[field] = value

        if missing:
            raise LLMConfigError(
                "Missing LLM configuration: "
                + ", ".join(missing)
                + ". Set them in the environment or a .env file."
            )
        return cls(**resolved)


def _first(
    names: tuple[str, ...],
    env: Mapping[str, str],
    file_values: Mapping[str, str],
) -> str | None:
    """Return the first non-empty value for any of ``names``, env before file."""
    for name in names:
        value = env.get(name) or file_values.get(name)
        if value:
            return value
    return None


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines from a ``.env`` file into a dict.

    Blank lines and ``#`` comments are skipped, surrounding quotes are stripped,
    and a leading ``export`` is ignored. A missing file yields an empty dict —
    the environment alone may carry everything. Credential *values* are only
    read to build the config; they are never logged or echoed.

    Args:
        path: The ``.env`` file path.

    Returns:
        The parsed key/value pairs.
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values
