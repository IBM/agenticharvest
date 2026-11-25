#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The normalized tool result envelope.

Every tool call — no matter which channel it arrived on or which domain served
it — resolves to one ``ToolResult``. A tool that fails returns ``ok=False`` with
an ``error`` message rather than raising, so a caller (an agent loop, a REST
client) always receives a structured, serializable result and can decide what to
do next.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """The outcome of a single tool call.

    Attributes:
        ok: ``True`` when the tool ran and produced output; ``False`` when it
            failed or was blocked by policy.
        output: The tool's return value on success. Any JSON-serializable shape.
        error: A human-readable message when ``ok`` is ``False``; ``None`` on
            success.
    """

    ok: bool
    output: Any = None
    error: str | None = None

    @classmethod
    def success(cls, output: Any) -> ToolResult:
        """Build a successful result carrying ``output``."""
        return cls(ok=True, output=output)

    @classmethod
    def failure(cls, error: str) -> ToolResult:
        """Build a failed result carrying an ``error`` message."""
        return cls(ok=False, error=error)
