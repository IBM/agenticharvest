#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The tool registry.

The registry is the one place a tool name resolves to a spec and a handler, and
the one place a call is executed and observed. A domain contributes its specs +
handlers (directly, or via the ``agenticharvest.domains`` entry point); a caller then
looks a tool up, lists the specs to advertise, or calls one by name.

``call`` never propagates a handler exception: it catches it into a failed
``ToolResult`` so every caller gets a structured result, then feeds every
registered tap. The registry holds no global state — callers construct and own
their registry, so a test's registry can never leak into production wiring.
"""

from __future__ import annotations

from collections.abc import Callable

from agenticharvest.core.tools.result import ToolResult
from agenticharvest.core.tools.spec import ToolSpec
from agenticharvest.core.tools.tap import Tap

# A handler takes the call's parameters and returns any JSON-serializable value.
# It may raise; the registry turns a raise into a failed ToolResult.
Handler = Callable[[dict], object]


class ToolRegistry:
    """Resolves tool names to specs + handlers and executes tapped calls."""

    def __init__(self, taps: list[Tap] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Handler] = {}
        self._taps: list[Tap] = list(taps or [])

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: ToolSpec, handler: Handler) -> None:
        """Register a tool spec and its handler.

        Args:
            spec: The declarative tool spec.
            handler: The callable that executes the tool.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if spec.name in self._specs:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def add_tap(self, tap: Tap) -> None:
        """Attach a tap that observes every subsequent call."""
        self._taps.append(tap)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> ToolSpec | None:
        """Return the spec for ``name``, or ``None`` if it is not registered."""
        return self._specs.get(name)

    def has(self, name: str) -> bool:
        """Return whether a tool named ``name`` is registered."""
        return name in self._specs

    def list_specs(self) -> list[ToolSpec]:
        """Return every registered spec, in registration order."""
        return list(self._specs.values())

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def call(self, name: str, params: dict) -> ToolResult:
        """Execute the named tool and return a normalized result.

        A missing tool, or a handler that raises, both yield ``ok=False`` rather
        than an exception, so callers always get a structured result. Every call
        that reaches a handler is reported to each registered tap.

        Args:
            name: The tool to call.
            params: The parameters to pass to the handler.

        Returns:
            The tool's ``ToolResult``.
        """
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult.failure(f"Unknown tool: '{name}'.")

        try:
            output = handler(params)
            result = ToolResult.success(output)
        except Exception as exc:  # noqa: BLE001 - any handler failure is a result
            result = ToolResult.failure(f"{type(exc).__name__}: {exc}")

        for tap in self._taps:
            tap.record(name, params, result)
        return result


def default_registry(taps: list[Tap] | None = None) -> ToolRegistry:
    """Return a fresh registry with every entry-point domain loaded.

    This is the wiring the gateway uses by default: it discovers domains
    registered under the ``agenticharvest.domains`` entry point and registers their
    tools. It is a factory, not a singleton — each call builds an independent
    registry the caller owns.

    Args:
        taps: Optional taps to attach before domains are loaded.

    Returns:
        A populated ``ToolRegistry``.
    """
    # Imported here to avoid a core -> domains import cycle at module load.
    from agenticharvest.domains.base.registry import load_entry_point_domains

    registry = ToolRegistry(taps=taps)
    load_entry_point_domains(registry)
    return registry
