#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The abstract Excel backend.

A backend is whatever actually reads and drives a workbook artifact: openpyxl
over the file, live Excel via xlwings, a headless LibreOffice, and so on.
Everything engine-specific lives behind this interface so the rest of the
domain — the IR extractor, the tools — depends only on the abstraction.

Backends are *partial*: none is expected to do everything. Each declares a
:class:`Capability` set and implements exactly those operations; the default of
every operation is to raise :class:`UnsupportedOperation`. openpyxl reads the
file structure and writes cell values but cannot run macros; a live backend
(xlwings) runs macros but leaves file-level IR extraction to openpyxl. A caller
checks ``capabilities`` (or lets :func:`~agenticharvest.domains.excel.backends.registry.
select_backend` pick a backend that has the capability) before calling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

if TYPE_CHECKING:
    # Only referenced in method annotations (this module has
    # ``from __future__ import annotations``), so importing at runtime would
    # create a cycle with ``ir.extract``, which imports ``ExcelBackend``.
    from agenticharvest.domains.excel.ir.models import Button, NamedRange, Sheet


class Capability(str, Enum):
    """What a backend can do, so a caller can pick one that fits the tool.

    ``READ`` builds the IR (structure, VBA); ``WRITE`` changes a cell value;
    ``EXECUTE`` runs a macro. A backend advertises the subset it supports.
    """

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class UnsupportedOperation(NotImplementedError):
    """Raised when a backend is asked for an operation it does not support."""


class CellEdit(BaseModel):
    """The before/after of a single cell write."""

    sheet: str
    cell: str
    old_value: Any = None
    new_value: Any = None


class MacroRun(BaseModel):
    """The outcome of running one macro."""

    macro: str
    args: list[Any] = []
    returned: Any = None


class ExcelBackend(ABC):
    """Read, write, and execute operations over one workbook artifact.

    A backend instance is bound to a single workbook at construction. Concrete
    backends decide how they open it — openpyxl loads the file, a live backend
    attaches to an Excel session — and each implements only the operations its
    :attr:`capabilities` advertise. The default of every mutating operation is
    to raise :class:`UnsupportedOperation`, so a backend need only override what
    it actually supports.

    Args:
        path: Filesystem path to the workbook this backend operates on.
    """

    #: The operations this backend supports. Subclasses narrow or widen it.
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.READ})

    #: The domain this backend executes for. The ``agenticharvest.backends`` entry
    #: point is shared by every domain, so discovery keeps only its own.
    domain: ClassVar[str] = "excel"

    def __init__(self, path: str) -> None:
        self.path = path

    def available(self) -> bool:
        """Return whether this backend can run in the current environment.

        The default is ``True`` — a file-level backend needs nothing beyond its
        Python dependency. A backend that needs an external runtime (a live
        Excel install for xlwings) overrides this to probe for it, mirroring the
        miner's ``available()`` so callers can degrade gracefully.
        """
        return True

    # ------------------------------------------------------------------
    # Cell values — default to unsupported; a backend overrides only what
    # its capabilities advertise.
    # ------------------------------------------------------------------

    def read_range(
        self, sheet: str, ref: str | None = None, max_rows: int = 200
    ) -> list[list[Any]]:
        """Read cell values from a sheet, row by row.

        Args:
            sheet: The display name of the sheet to read.
            ref: An A1-style range such as ``"A1:C10"``. ``None`` reads the
                sheet's whole used range.
            max_rows: The most rows to return, so a huge sheet cannot flood
                the caller.

        Raises:
            UnsupportedOperation: If this backend cannot read cell values.
        """
        raise UnsupportedOperation(f"the {self.name} backend cannot read cell values")

    def write_cell(self, sheet: str, cell: str, value: Any) -> CellEdit:
        """Set one cell's value, returning its before/after.

        Args:
            sheet: The display name of the sheet to write to.
            cell: The A1-style coordinate, e.g. ``"B1"``.
            value: The value to write.

        Raises:
            UnsupportedOperation: If this backend cannot write cells.
        """
        raise UnsupportedOperation(f"the {self.name} backend cannot write cells")

    def run_macro(self, macro: str, args: list | None = None) -> MacroRun:
        """Run a macro by name, returning what it produced.

        Args:
            macro: The macro (VBA procedure) name to run.
            args: Positional arguments to pass, if any.

        Raises:
            UnsupportedOperation: If this backend cannot run macros.
        """
        raise UnsupportedOperation(f"the {self.name} backend cannot run macros")

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for the backend (e.g. ``"openpyxl"``)."""

    @abstractmethod
    def read_sheets(self, max_header_cols: int = 64) -> tuple[list[Sheet], dict[str, str]]:
        """Read every sheet's name, code name, header row, and order.

        Args:
            max_header_cols: How many leading columns of the first row to read
                as the header.

        Returns:
            A tuple of (sheets in workbook order, code-name -> display-name map).
            The code-name map lets the data-flow analyzer resolve VBA references
            such as ``wsTemp`` back to a sheet.
        """

    @abstractmethod
    def read_named_ranges(self) -> list[NamedRange]:
        """Read analyst-relevant defined names.

        Excel-internal auto-generated names should be excluded. Both
        workbook-scoped and sheet-scoped names are included.

        Returns:
            The defined names as ``NamedRange`` models.
        """

    @abstractmethod
    def read_controls(self) -> list[Button]:
        """Read controls that can trigger macros (buttons, clickable links).

        Recovery is best-effort and backend-dependent; a backend that cannot
        see a given control type returns what it can.

        Returns:
            The controls found, empty when none are recoverable.
        """

    @abstractmethod
    def read_vba_modules(self) -> dict[str, str]:
        """Read the workbook's VBA source, one entry per module.

        Returns:
            Mapping of module name to its VBA source text. Empty when the
            workbook has no macros. Parsing this text into procedures, a call
            graph, and data flow is the analyzer's job, not the backend's.
        """

    def close(self) -> None:
        """Release any resources the backend holds.

        The default is a no-op; backends that hold an open handle or a live
        session override this.
        """
