#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Connection settings for the web apps the web domain may call.

A web app is named by a short identifier (``hris``, ``portal``) and configured
entirely from the environment, so no URL or credential lives in code:

    AGENTICHARVEST_WEB_<NAME>_URL         base URL, e.g. http://127.0.0.1:8055   (required)
    AGENTICHARVEST_WEB_<NAME>_SPEC        where the API describes itself          (required)
                                    an http(s) URL, a path relative to the base URL
                                    (``/server/specs/oas``), or a local file
    AGENTICHARVEST_WEB_<NAME>_SPEC_KIND   ``openapi`` (default) or ``odoo``
    AGENTICHARVEST_WEB_<NAME>_AUTH        ``bearer`` (default), ``header``, ``none``,
                                    ``oauth2-password``, or ``odoo``
    AGENTICHARVEST_WEB_<NAME>_TOKEN       the bearer token or API key
    AGENTICHARVEST_WEB_<NAME>_AUTH_HEADER header name for ``header`` auth
    AGENTICHARVEST_WEB_<NAME>_TOKEN_URL   token endpoint for ``oauth2-password``
    AGENTICHARVEST_WEB_<NAME>_CLIENT_ID   client id for ``oauth2-password``
    AGENTICHARVEST_WEB_<NAME>_USERNAME    user for ``oauth2-password``
    AGENTICHARVEST_WEB_<NAME>_PASSWORD    password for ``oauth2-password``
    AGENTICHARVEST_WEB_<NAME>_DATABASE    database name for ``odoo`` auth
    AGENTICHARVEST_WEB_<NAME>_PREFIX      only expose operations under this path prefix
    AGENTICHARVEST_WEB_<NAME>_MODELS      ``odoo`` only: comma-separated models to expose
    AGENTICHARVEST_WEB_<NAME>_METHODS     ``odoo`` only: extra business methods to expose,
                                    as ``model.method`` (``action_*`` are automatic)

``AGENTICHARVEST_WEB_APPS`` lists the configured names (``hris,portal``). Values are
read from the process environment first and a ``.env`` file second, the same
precedence ``LLMConfig`` uses. Credential values are never logged.

Each app's base URL is also its host allowlist: the backend refuses to send a
request anywhere else, so an agent cannot be pointed at an arbitrary URL.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr

from agenticharvest.utils.llm.config import read_env_file

ENV_PREFIX = "AGENTICHARVEST_WEB_"
APPS_VAR = "AGENTICHARVEST_WEB_APPS"


class WebConfigError(RuntimeError):
    """Raised when a web app's configuration is missing or invalid.

    Tests catch this to skip cleanly when a live system is not configured.
    """


class SpecKind(str, Enum):
    """How the app describes its API."""

    OPENAPI = "openapi"
    ODOO = "odoo"


class AuthKind(str, Enum):
    """How a request proves who it is from."""

    NONE = "none"
    BEARER = "bearer"
    HEADER = "header"
    OAUTH2_PASSWORD = "oauth2-password"
    ODOO = "odoo"


class WebAppConfig(BaseModel):
    """One web app: where it is, how it describes itself, how to authenticate.

    Attributes:
        name: The short identifier tools take as their ``app`` parameter.
        base_url: Every request goes to this origin and nowhere else.
        spec: Where to load the API description from.
        spec_kind: The description's format.
        auth: The authentication scheme.
        token: The bearer token or API key, for ``bearer``/``header``/``odoo``.
        auth_header: The header carrying ``token`` for ``header`` auth.
        token_url: The token endpoint, for ``oauth2-password``.
        client_id: The OAuth client id, for ``oauth2-password``.
        username: The user, for ``oauth2-password``.
        password: The password, for ``oauth2-password``.
        database: The database, for ``odoo``.
        prefix: When set, only operations whose path starts with it are exposed.
        models: For ``odoo``, the models to expose (all described models when
            empty).
        methods: For ``odoo``, extra argument-free methods to expose, as
            ``model.method``.
    """

    name: str
    base_url: str
    spec: str
    spec_kind: SpecKind = SpecKind.OPENAPI
    auth: AuthKind = AuthKind.BEARER
    token: SecretStr | None = None
    auth_header: str = "Authorization"
    token_url: str = ""
    client_id: str = ""
    username: str = ""
    password: SecretStr | None = None
    database: str = ""
    prefix: str = ""
    models: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)

    @property
    def origin(self) -> str:
        """The scheme, host, and port requests are confined to."""
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @classmethod
    def from_env(cls, name: str, env_file: Path | str | None = None) -> WebAppConfig:
        """Build the config for app ``name`` from the environment and ``.env``.

        Raises:
            WebConfigError: If the URL or spec is missing, or a value is invalid.
        """
        values = _environment(env_file)
        key = f"{ENV_PREFIX}{name.upper()}_"

        def get(field: str) -> str:
            return values.get(key + field, "")

        missing = [key + f for f in ("URL", "SPEC") if not get(f)]
        if missing:
            raise WebConfigError(
                f"Web app '{name}' is not configured: set {', '.join(missing)} "
                "in the environment or .env."
            )
        try:
            return cls(
                name=name,
                base_url=get("URL").rstrip("/"),
                spec=get("SPEC"),
                spec_kind=SpecKind(get("SPEC_KIND") or SpecKind.OPENAPI.value),
                auth=AuthKind(get("AUTH") or AuthKind.BEARER.value),
                token=SecretStr(get("TOKEN")) if get("TOKEN") else None,
                auth_header=get("AUTH_HEADER") or "Authorization",
                token_url=get("TOKEN_URL"),
                client_id=get("CLIENT_ID"),
                username=get("USERNAME"),
                password=SecretStr(get("PASSWORD")) if get("PASSWORD") else None,
                database=get("DATABASE"),
                prefix=get("PREFIX"),
                models=_split(get("MODELS")),
                methods=_split(get("METHODS")),
            )
        except ValueError as exc:
            raise WebConfigError(f"Web app '{name}' has an invalid setting: {exc}") from exc


def configured_apps(env_file: Path | str | None = None) -> list[str]:
    """Return the app names listed in ``AGENTICHARVEST_WEB_APPS``, in order."""
    return _split(_environment(env_file).get(APPS_VAR, ""))


def _split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _environment(env_file: Path | str | None) -> Mapping[str, str]:
    """The ``.env`` values overlaid by the process environment."""
    merged = dict(read_env_file(Path(env_file) if env_file else Path(".env")))
    merged.update({k: v for k, v in os.environ.items() if v})
    return merged


__all__ = [
    "APPS_VAR",
    "ENV_PREFIX",
    "AuthKind",
    "SpecKind",
    "WebAppConfig",
    "WebConfigError",
    "configured_apps",
]
