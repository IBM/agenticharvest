#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The live client: authenticate, load the API description, send requests.

One :class:`WebClient` is bound to one configured app. It is the only place that
opens a connection, so it is also where the safety rules live:

- **Origin allowlist.** A request is built from the app's base URL and an
  operation's path template; a resulting URL whose origin differs from the
  base URL's is refused before anything is sent.
- **Credentials stay here.** Tokens are attached as headers at send time and
  never returned in a result. Response headers that carry credentials are
  dropped from results.
- **Bounded output.** Response bodies are capped, so one call cannot flood an
  agent's context.

Authentication schemes (see :class:`~agenticharvest.domains.web.config.AuthKind`):
``bearer`` and ``header`` send a static token; ``oauth2-password`` exchanges a
username and password for an access token at ``token_url`` and refreshes it on
expiry; ``odoo`` sends an Odoo API key as a bearer token with the database
header Odoo's JSON-2 API needs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote, urlsplit

from pydantic import BaseModel

from agenticharvest.domains.web.config import AuthKind, SpecKind, WebAppConfig
from agenticharvest.domains.web.ir import WebIR, from_odoo, from_openapi

#: The most response characters a tool returns.
MAX_BODY_CHARS = 20_000

#: Seconds to wait for any one request.
TIMEOUT_SECONDS = 30.0

# Response headers never passed back to a caller.
_SECRET_HEADERS = frozenset({"set-cookie", "authorization", "x-api-key", "proxy-authorization"})

# Response headers worth returning: they identify what a write created.
_USEFUL_HEADERS = ("location", "content-type", "etag")


class WebRequestError(RuntimeError):
    """Raised when a request is refused before sending or fails to send."""


class WebResponse(BaseModel):
    """What one request returned.

    Attributes:
        status: The HTTP status code.
        ok: ``True`` for a 2xx status.
        body: The parsed JSON body, or the text body, truncated.
        truncated: Whether ``body`` was cut to fit.
        headers: The useful, non-secret response headers.
    """

    status: int
    ok: bool
    body: Any = None
    truncated: bool = False
    headers: dict[str, str] = {}


class WebClient:
    """Sends requests to one configured web app.

    Args:
        config: The app to talk to.
    """

    def __init__(self, config: WebAppConfig) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise WebRequestError(
                "The web domain needs the 'web' extra: install agenticharvest[web] (httpx)."
            ) from exc

        self.config = config
        self._http = httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = Lock()
        self._ir: WebIR | None = None

    def close(self) -> None:
        """Release the connection pool."""
        self._http.close()

    # ------------------------------------------------------------------
    # The API description
    # ------------------------------------------------------------------

    def ir(self, *, refresh: bool = False) -> WebIR:
        """Return the app's IR, loading its description on first use.

        Args:
            refresh: Reload it, e.g. after the app's schema changed (a new
                collection in a data platform adds operations).
        """
        if self._ir is None or refresh:
            self._ir = self._load_ir()
        return self._ir

    def _load_ir(self) -> WebIR:
        import httpx

        try:
            if self.config.spec_kind is SpecKind.ODOO:
                return self._load_odoo()
            document = self._load_document(self.config.spec)
        except httpx.HTTPError as exc:
            # Every failure to reach the app surfaces as one error type, so a
            # caller (and a tool result) can tell "unreachable" from a bug.
            raise WebRequestError(
                f"Could not read the API description of '{self.config.name}': {exc}"
            ) from exc
        return from_openapi(self.config.name, document, prefix=self.config.prefix)

    def _load_document(self, location: str) -> dict[str, Any]:
        """Load a JSON or YAML document from a URL, an app path, or a file."""
        path = Path(location).expanduser()
        if not location.startswith(("http://", "https://", "/")) or path.is_file():
            text = path.read_text(encoding="utf-8")
        else:
            response = self._send("get", location)
            if response.status_code >= 400:
                raise WebRequestError(
                    f"Could not load the API description from {location}: "
                    f"HTTP {response.status_code}."
                )
            text = response.text
        return _parse_document(text)

    def _load_odoo(self) -> WebIR:
        """Read ``/doc/<model>.json`` for each configured model.

        Odoo serves these pages to a logged-in session, not to an API key, so
        the description is read with a session for the same user.
        """
        session = self._odoo_session()
        try:
            models = self.config.models
            if not models:
                index = session.get(f"{self.config.base_url}/doc/index.json").json()
                models = [m["model"] for m in index.get("models", [])]
            docs = []
            for model in models:
                response = session.get(f"{self.config.base_url}/doc/{quote(model)}.json")
                if response.status_code != 200:
                    raise WebRequestError(
                        f"Odoo has no description for model '{model}' "
                        f"(HTTP {response.status_code}); is its app installed?"
                    )
                docs.append(response.json())
            # A JSON-RPC route: it answers only a POST carrying a JSON-RPC body.
            version = session.post(
                f"{self.config.base_url}/web/webclient/version_info",
                json={"jsonrpc": "2.0", "params": {}},
            )
            server = ""
            if version.status_code == 200:
                server = str((version.json().get("result") or {}).get("server_version", ""))
            return from_odoo(
                self.config.name, docs, version=server, methods=self.config.methods
            )
        finally:
            session.close()

    def _odoo_session(self) -> Any:
        import httpx

        if not (self.config.username and self.config.password):
            raise WebRequestError(
                "Reading Odoo's API description needs a login: set the app's "
                "USERNAME and PASSWORD."
            )
        session = httpx.Client(timeout=TIMEOUT_SECONDS)
        reply = session.post(
            f"{self.config.base_url}/web/session/authenticate",
            json={
                "jsonrpc": "2.0",
                "params": {
                    "db": self.config.database,
                    "login": self.config.username,
                    "password": self.config.password.get_secret_value(),
                },
            },
        ).json()
        if not (reply.get("result") or {}).get("uid"):
            session.close()
            raise WebRequestError("Odoo rejected the login used to read its API description.")
        return session

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        max_chars: int | None = MAX_BODY_CHARS,
    ) -> WebResponse:
        """Send one request to the app and return its bounded response.

        Args:
            method: The HTTP method.
            path: A path on the app (``/items/orders``), never a full URL to
                another host.
            query: Query parameters. Lists and objects are JSON-encoded, as
                most APIs that take structured filters expect.
            body: A JSON body.
            max_chars: The most response characters to keep. The default keeps
                an agent's context small; ``None`` keeps the whole body, for
                code reading history or exports.

        Raises:
            WebRequestError: If the URL leaves the app's origin, or the request
                cannot be sent at all.
        """
        response = self._send(method, path, query=query, body=body)
        return _bounded(response, max_chars)

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        import httpx

        url = self._url(path)
        params = {
            key: json.dumps(value) if isinstance(value, (dict, list)) else value
            for key, value in (query or {}).items()
            if value is not None
        }
        # Odoo's JSON-2 API takes every call, read or write, as a POST.
        verb = "post" if self.config.auth is AuthKind.ODOO and "/json/2/" in path else method
        try:
            return self._http.request(
                verb.upper(),
                url,
                params=params,
                json=body if body is not None else (
                    {} if self.config.auth is AuthKind.ODOO and verb == "post" else None
                ),
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise WebRequestError(f"{method.upper()} {path} failed: {exc}") from exc

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = f"{self.config.base_url}/{path.lstrip('/')}"
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self.config.origin:
            raise WebRequestError(
                f"Refused: {parts.scheme}://{parts.netloc} is not this app's origin "
                f"({self.config.origin})."
            )
        return url

    def _headers(self) -> dict[str, str]:
        auth = self.config.auth
        headers = {"Accept": "application/json"}
        if auth is AuthKind.NONE:
            return headers
        if auth is AuthKind.OAUTH2_PASSWORD:
            headers["Authorization"] = f"Bearer {self._access_token()}"
            return headers
        if self.config.token is None:
            raise WebRequestError(
                f"Web app '{self.config.name}' uses {auth.value} auth but has no TOKEN set."
            )
        token = self.config.token.get_secret_value()
        if auth is AuthKind.HEADER:
            headers[self.config.auth_header] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        if auth is AuthKind.ODOO and self.config.database:
            headers["X-Odoo-Database"] = self.config.database
        return headers

    def _access_token(self) -> str:
        """Return a valid OAuth access token, fetching one when needed."""
        import httpx

        with self._lock:
            if self._token and time.monotonic() < self._token_expiry:
                return self._token
            if not (self.config.token_url and self.config.username and self.config.password):
                raise WebRequestError(
                    f"Web app '{self.config.name}' uses oauth2-password auth: set its "
                    "TOKEN_URL, USERNAME, and PASSWORD."
                )
            try:
                reply = self._http.post(
                    self._url(self.config.token_url),
                    data={
                        "grant_type": "password",
                        "client_id": self.config.client_id,
                        "username": self.config.username,
                        "password": self.config.password.get_secret_value(),
                    },
                )
            except httpx.HTTPError as exc:
                raise WebRequestError(f"Could not reach the token endpoint: {exc}") from exc
            if reply.status_code != 200:
                raise WebRequestError(
                    f"The token endpoint refused the login (HTTP {reply.status_code})."
                )
            payload = reply.json()
            self._token = str(payload["access_token"])
            # Refresh a little early so a token never expires mid-request.
            self._token_expiry = time.monotonic() + max(int(payload.get("expires_in", 60)) - 10, 5)
            return self._token


def _bounded(response: Any, max_chars: int | None = MAX_BODY_CHARS) -> WebResponse:
    """Turn an httpx response into a bounded, secret-free :class:`WebResponse`.

    A body over ``max_chars`` comes back as truncated text, flagged, so a caller
    can never mistake a cut-off listing for the whole one.
    """
    text = response.text
    truncated = max_chars is not None and len(text) > max_chars
    body: Any
    try:
        body = response.json() if text and not truncated else None
    except ValueError:
        body = None
    if body is None and text:
        body = text[:max_chars] if max_chars is not None else text
    headers = {
        name: response.headers[name]
        for name in _USEFUL_HEADERS
        if name in response.headers and name not in _SECRET_HEADERS
    }
    return WebResponse(
        status=response.status_code,
        ok=200 <= response.status_code < 300,
        body=body,
        truncated=truncated,
        headers=headers,
    )


def _parse_document(text: str) -> dict[str, Any]:
    """Parse a JSON or YAML API description."""
    try:
        document = json.loads(text)
    except ValueError:
        import yaml

        document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise WebRequestError("The API description is not a JSON or YAML object.")
    return document


__all__ = ["MAX_BODY_CHARS", "WebClient", "WebRequestError", "WebResponse"]
