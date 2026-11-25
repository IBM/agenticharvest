#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Accept an Excel workbook in various forms.

``ExcelSource`` is the domain's :class:`SourceResolver`: it turns whatever a
caller supplies — a filesystem path, a byte buffer from an upload, or a
file-like stream — into a single validated, hashed, local ``ResolvedSource``
that the IR builder and backends can open by path.

It detects the workbook format (.xlsx / .xlsm / .xlsb), computes a content hash
so every downstream tool call is traceable to this exact input, and writes
non-path inputs to a temporary file with the correct extension (openpyxl and
oletools key off the suffix).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agenticharvest.core.ir.base import Artifact
from agenticharvest.core.ir.source import (
    RawSource,
    ResolvedSource,
    read_bytes,
    sha256_bytes,
)

# The workbook formats the Excel domain claims. Kept as a set so format
# detection and validation share one source of truth.
SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".xls"}

# Artifact kind discriminator — domains match on this to claim a source.
ARTIFACT_KIND = "excel"


class UnsupportedWorkbookError(ValueError):
    """Raised when a source is not a recognized Excel workbook format."""


class ExcelSource:
    """Resolve raw workbook input into a local, identified ``ResolvedSource``."""

    def resolve(
        self, raw: RawSource, *, filename: str | None = None
    ) -> ResolvedSource:
        """Normalize ``raw`` into a materialized, identified workbook source.

        Args:
            raw: The workbook in any supported form — a path (``str``/``Path``),
                raw ``bytes``, or a file-like object with ``read()``.
            filename: Original name, used for format detection when ``raw`` is
                not a path and recorded in the artifact metadata.

        Returns:
            A ``ResolvedSource`` pointing at a local workbook file, with an
            ``Artifact`` carrying the content hash, size, and format.

        Raises:
            UnsupportedWorkbookError: If the format cannot be determined or is
                not a supported workbook type.
        """
        if isinstance(raw, (str, Path)):
            return self._resolve_path(Path(raw))
        return self._resolve_bytes(read_bytes(raw), filename)

    # ------------------------------------------------------------------
    # Path input — used in place, no copy
    # ------------------------------------------------------------------

    def _resolve_path(self, path: Path) -> ResolvedSource:
        """Resolve a filesystem path, validating its suffix and hashing it.

        Args:
            path: The workbook path.

        Returns:
            A ``ResolvedSource`` referencing the original file (not a copy).

        Raises:
            UnsupportedWorkbookError: If the suffix is not a supported format.
            FileNotFoundError: If the path does not exist.
        """
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise UnsupportedWorkbookError(
                f"{path.name}: {suffix or 'no extension'} is not a supported "
                f"workbook format ({', '.join(sorted(SUPPORTED_SUFFIXES))})"
            )
        data = path.read_bytes()
        artifact = Artifact(
            uri=path.resolve().as_uri(),
            kind=ARTIFACT_KIND,
            content_hash=sha256_bytes(data),
            size_bytes=len(data),
            metadata={"filename": path.name, "format": suffix},
        )
        return ResolvedSource(
            path=str(path.resolve()), artifact=artifact, is_temporary=False
        )

    # ------------------------------------------------------------------
    # Bytes / stream input — staged to a temp file
    # ------------------------------------------------------------------

    def _resolve_bytes(self, data: bytes, filename: str | None) -> ResolvedSource:
        """Stage raw workbook bytes to a temp file with the right suffix.

        Args:
            data: The raw workbook content.
            filename: Original name, used to infer the format; defaults to
                ``.xlsx`` when no usable suffix is available.

        Returns:
            A ``ResolvedSource`` pointing at a temporary file the caller may
            delete after use (``is_temporary`` is ``True``).

        Raises:
            UnsupportedWorkbookError: If ``filename`` carries an unsupported
                suffix.
        """
        suffix = self._suffix_for(filename)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
        temp_path = Path(handle.name)
        artifact = Artifact(
            uri=temp_path.as_uri(),
            kind=ARTIFACT_KIND,
            content_hash=sha256_bytes(data),
            size_bytes=len(data),
            metadata={"filename": filename or temp_path.name, "format": suffix},
        )
        return ResolvedSource(
            path=str(temp_path), artifact=artifact, is_temporary=True
        )

    @staticmethod
    def _suffix_for(filename: str | None) -> str:
        """Determine the file suffix to stage bytes under.

        Args:
            filename: Original name, if any.

        Returns:
            A supported suffix. Falls back to ``.xlsx`` when ``filename`` has no
            suffix at all.

        Raises:
            UnsupportedWorkbookError: If ``filename`` has a suffix that is not a
                supported workbook format.
        """
        if not filename:
            return ".xlsx"
        suffix = Path(filename).suffix.lower()
        if not suffix:
            return ".xlsx"
        if suffix not in SUPPORTED_SUFFIXES:
            raise UnsupportedWorkbookError(
                f"{filename}: {suffix} is not a supported workbook format "
                f"({', '.join(sorted(SUPPORTED_SUFFIXES))})"
            )
        return suffix
