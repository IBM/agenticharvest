#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Backend discovery — pick a backend that can perform a given capability.

Backends register under the ``agenticharvest.backends`` entry point (declared in
``pyproject.toml``). A tool that needs to *write* a cell or *run* a macro does
not hard-code a backend; it asks :func:`select_backend` for one that both
advertises the capability and is available in this environment. openpyxl covers
``WRITE`` from the file everywhere; xlwings covers ``EXECUTE`` only where a live
Excel is installed, so ``select_backend(path, Capability.EXECUTE)`` returns
``None`` on a machine without it — and the caller degrades gracefully rather than
crashing, mirroring how the miner reports itself unavailable without R.

This is the third of AgenticHarvest's three extension seams (domains, backends, miners):
a third-party package adds a backend purely by registering its own entry point.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from agenticharvest.domains.excel.backends.base import Capability, ExcelBackend

# The entry-point group execution backends register under. Mirrors the
# ``[project.entry-points."agenticharvest.backends"]`` table in pyproject.toml.
BACKEND_ENTRY_POINT_GROUP = "agenticharvest.backends"

# Discovery is process-stable, so the loaded classes are cached after the first
# scan; a call still constructs a fresh, path-bound instance every time.
_BACKEND_CLASSES: list[type[ExcelBackend]] | None = None


def load_backend_classes() -> list[type[ExcelBackend]]:
    """Return the backend classes registered under ``agenticharvest.backends``.

    A backend whose entry point fails to import is skipped rather than allowed
    to break discovery for the others. The group is shared by every domain, so
    only Excel backends are kept: classes that subclass :class:`ExcelBackend`
    and declare ``domain = "excel"``.
    """
    global _BACKEND_CLASSES
    if _BACKEND_CLASSES is None:
        classes: list[type[ExcelBackend]] = []
        for ep in entry_points(group=BACKEND_ENTRY_POINT_GROUP):
            try:
                loaded = ep.load()
            except Exception:  # noqa: BLE001, S112 - one bad backend must not hide the rest
                continue
            if (
                isinstance(loaded, type)
                and issubclass(loaded, ExcelBackend)
                and loaded.domain == "excel"
            ):
                classes.append(loaded)
        _BACKEND_CLASSES = classes
    return _BACKEND_CLASSES


def select_backend(path: str, capability: Capability) -> ExcelBackend | None:
    """Return a backend bound to ``path`` that can perform ``capability``.

    Backends are considered in registration order; the first that both
    advertises ``capability`` and reports itself :meth:`~ExcelBackend.available`
    is constructed and returned.

    Args:
        path: The workbook the backend will operate on.
        capability: The operation the caller needs (``WRITE`` or ``EXECUTE``).

    Returns:
        A ready backend instance, or ``None`` when no registered backend can
        perform the capability in this environment.
    """
    for cls in load_backend_classes():
        if capability not in getattr(cls, "capabilities", frozenset()):
            continue
        instance = cls(path)
        if instance.available():
            return instance
    return None
