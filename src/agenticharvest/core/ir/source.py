#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Generic source ingestion.

Before a domain can build an IR it needs its raw input in a stable, local,
identified form — regardless of whether the caller passed a file path, a byte
buffer from an upload, or a file-like stream. ``ResolvedSource`` is that stable
form, and ``SourceResolver`` is the protocol a domain implements to produce one.

This lives in core (not the Excel domain) because every future domain — PDFs,
databases, code repositories — faces the same "accept input in various forms"
problem. Keeping the contract here means channels and the gateway can hand a
source to any domain without special-casing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agenticharvest.core.ir.base import Artifact

# The input forms a resolver must accept. A ``str``/``Path`` is a filesystem
# path; ``bytes`` is raw content (e.g. an HTTP upload). File-like streams are
# read by the resolver, so they are not part of the static type but are handled
# at runtime by ``read_bytes``.
RawSource = str | Path | bytes


class ResolvedSource(BaseModel):
    """A source materialized to a local path with computed provenance.

    Domains build their IR from ``path``; ``artifact`` travels with the IR and
    into the event log so every downstream tool call is traceable to this exact
    input by content hash.

    Attributes:
        path: Absolute filesystem path to the materialized source. For inputs
            that were already files this is the original path; for byte / stream
            inputs it is a temporary file the resolver wrote.
        artifact: Provenance (uri, kind, content hash, size) for this source.
        is_temporary: ``True`` when ``path`` points at a file the resolver
            created and the caller may remove after use.
    """

    path: str
    artifact: Artifact
    is_temporary: bool = False


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of ``data``.

    Used to give every resolved source a stable content hash for caching and
    for tying event-log records back to an exact input.
    """
    return hashlib.sha256(data).hexdigest()


def read_bytes(raw: object) -> bytes:
    """Read raw bytes from any supported input form.

    Args:
        raw: A ``bytes`` buffer, a ``str``/``Path`` filesystem path, or a
            file-like object exposing ``read()``.

    Returns:
        The full content as bytes.

    Raises:
        TypeError: If ``raw`` is none of the supported forms.
    """
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, (str, Path)):
        return Path(raw).read_bytes()
    reader = getattr(raw, "read", None)
    if callable(reader):
        data = reader()
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        raise TypeError(f"read() returned {type(data)!r}, expected bytes or str")
    raise TypeError(f"Unsupported source form: {type(raw)!r}")


@runtime_checkable
class SourceResolver(Protocol):
    """Protocol for turning raw input into a ``ResolvedSource``.

    A domain implements this to normalize the many ways a caller can supply its
    source into one validated, hashed, local artifact. Implementations decide
    format detection and any staging (e.g. writing an upload to a temp file).
    """

    def resolve(self, raw: RawSource, *, filename: str | None = None) -> ResolvedSource:
        """Normalize ``raw`` into a materialized, identified source.

        Args:
            raw: The input in any supported form.
            filename: Optional original name, used for format detection and
                recorded in the artifact metadata when ``raw`` is not a path.

        Returns:
            A ``ResolvedSource`` ready for IR construction.
        """
        ...


class SourceMetadata(BaseModel):
    """Optional caller-supplied hints attached to a resolved source.

    Attributes:
        filename: Original filename, when the input was not a path.
        mime_type: Declared content type, when known.
        extra: Any additional provenance the caller wants preserved.
    """

    filename: str | None = None
    mime_type: str | None = None
    extra: dict[str, object] = Field(default_factory=dict)
