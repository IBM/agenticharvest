#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""xlwings backend — live MS Excel (real recalculation, real macros).

This is the full execution backend: it drives a running Excel through xlwings,
so it reads a workbook's structure *and* writes cells *and* runs VBA macros
against the real engine. It advertises every capability — ``READ``, ``WRITE``,
``EXECUTE`` — where the file-level openpyxl backend stops at ``READ``/``WRITE``.

Like the bupaR miner, it degrades gracefully: xlwings and a real Excel install
only exist on Windows and macOS, so :meth:`available` probes for the dependency
and returns ``False`` when it is absent, letting a caller fall back or report the
capability as simply unavailable rather than crashing. ``xlwings`` is imported
lazily, inside the methods that use it, so importing this module never requires
the optional ``excel-live`` extra to be installed.

Every operation opens the workbook in a hidden Excel, does its work, and quits
that instance in a ``finally`` — a live backend must never leak an Excel process.
The live-Excel code paths run only where Excel is installed, so they are not
covered by the hermetic test suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ClassVar

from agenticharvest.domains.excel.backends.base import (
    Capability,
    CellEdit,
    ExcelBackend,
    MacroRun,
)
from agenticharvest.domains.excel.ir.models import Button, NamedRange, Sheet

if TYPE_CHECKING:  # pragma: no cover - typing only
    import xlwings as xw

# Excel-internal defined names that are noise for analysis; the same set the
# openpyxl backend filters, kept identical so both backends yield the same names.
_INTERNAL_NAME_PREFIXES = (
    "_xlnm.",
    "_xlfn.",
    "_FilterDatabase",
    "Print_Area",
    "Print_Titles",
)

# Values a macro may return that carry no meaning across the boundary as JSON;
# anything else non-primitive is stringified so the result stays serializable.
_PRIMITIVE = (str, int, float, bool, type(None))


class XlwingsBackend(ExcelBackend):
    """Read, write, and run macros against a live Excel via xlwings."""

    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.WRITE, Capability.EXECUTE}
    )

    @property
    def name(self) -> str:
        return "xlwings"

    def available(self) -> bool:
        """Return whether xlwings (and thus a usable Excel bridge) is importable.

        Importing xlwings is the honest, cheap gate — it succeeds only where the
        ``excel-live`` extra is installed on a platform xlwings supports. Whether
        Excel then actually launches is discovered when a call runs; a failure
        there surfaces as a normal failed tool result.
        """
        try:
            import xlwings  # noqa: F401
        except ImportError:
            return False
        return True

    # ------------------------------------------------------------------
    # Live session management
    # ------------------------------------------------------------------

    @contextmanager
    def _book(self, *, save: bool) -> Iterator[xw.Book]:  # pragma: no cover - live
        """Open the workbook in a hidden Excel, yield it, then always quit.

        Args:
            save: Whether to save the workbook before closing — set for the
                mutating operations, cleared for the reads.
        """
        import xlwings as xw

        app = xw.App(visible=False, add_book=False)
        try:
            book = app.books.open(self.path)
            yield book
            if save:
                book.save()
            book.close()
        finally:
            app.quit()

    # ------------------------------------------------------------------
    # Structure (READ)
    # ------------------------------------------------------------------

    def read_sheets(  # pragma: no cover - live Excel only
        self, max_header_cols: int = 64
    ) -> tuple[list[Sheet], dict[str, str]]:
        sheets: list[Sheet] = []
        codename_map: dict[str, str] = {}
        with self._book(save=False) as book:
            for index, worksheet in enumerate(book.sheets):
                codename = self._code_name(worksheet)
                if codename:
                    codename_map[codename] = worksheet.name
                sheets.append(
                    Sheet(
                        name=worksheet.name,
                        codename=codename,
                        headers=self._read_header_row(worksheet, max_header_cols),
                        index=index,
                    )
                )
        return sheets, codename_map

    @staticmethod
    def _code_name(worksheet: Any) -> str | None:  # pragma: no cover - live
        """Return a sheet's VBA code name across the Windows/macOS COM shapes.

        The code name lives on the underlying automation object, spelled
        ``CodeName`` through pywin32 on Windows and ``code_name`` through
        appscript on macOS; try both and give up quietly if neither is present.
        """
        api = worksheet.api
        for attribute in ("CodeName", "code_name"):
            try:
                value = getattr(api, attribute)
            except Exception:  # noqa: BLE001, S112 - attribute shape is platform-specific
                continue
            if value:
                return str(value)
        return None

    @staticmethod
    def _read_header_row(worksheet: Any, max_cols: int) -> list[str]:  # pragma: no cover
        """Return the first row's values as strings, trailing blanks trimmed.

        Mirrors the openpyxl backend's header logic so the two backends agree on
        what a sheet's header row is.
        """
        row = worksheet.range((1, 1), (1, max_cols)).value
        if row is None:
            return []
        cells = row if isinstance(row, list) else [row]
        values = ["" if cell is None else str(cell).strip() for cell in cells]
        while values and not values[-1]:
            values.pop()
        return values

    def read_named_ranges(self) -> list[NamedRange]:  # pragma: no cover - live Excel
        result: list[NamedRange] = []
        with self._book(save=False) as book:
            for defined in book.names:
                name = defined.name
                if self._is_internal_name(name):
                    continue
                # A sheet-scoped name is spelled "Sheet!Local"; the bare local
                # name is what analysts refer to, and the "!" marks the scope.
                scope = "sheet" if "!" in name else "workbook"
                local_name = name.split("!")[-1]
                sheet, ref = self._resolve_name(defined)
                result.append(
                    NamedRange(name=local_name, sheet=sheet, ref=ref, scope=scope)
                )
        return result

    @staticmethod
    def _is_internal_name(name: str) -> bool:
        """Return ``True`` for Excel-internal names that should be skipped."""
        bare = name.split("!")[-1]
        return any(
            name.startswith(prefix) or bare.startswith(prefix)
            for prefix in _INTERNAL_NAME_PREFIXES
        )

    @staticmethod
    def _resolve_name(defined: Any) -> tuple[str | None, str]:  # pragma: no cover
        """Return a defined name's (sheet, ref).

        A name that points at cells resolves to its sheet and address; a name
        that holds a constant or formula has no cell destination, so its raw
        ``refers_to`` string is kept as the ref with no sheet.
        """
        try:
            cells = defined.refers_to_range
            return cells.sheet.name, cells.address
        except Exception:  # noqa: BLE001 - non-range names raise; fall back to raw
            refers_to = getattr(defined, "refers_to", "") or ""
            return None, str(refers_to).lstrip("=")

    def read_controls(self) -> list[Button]:  # pragma: no cover - live Excel only
        """Read self-referencing hyperlinks used as clickable buttons.

        Best-effort, as the interface promises: the hyperlink collection is read
        through the automation object, and any sheet whose shape is not readable
        contributes nothing rather than failing the whole read.
        """
        buttons: list[Button] = []
        with self._book(save=False) as book:
            for worksheet in book.sheets:
                try:
                    hyperlinks = list(worksheet.api.Hyperlinks)
                except Exception:  # noqa: BLE001, S112 - collection is platform-specific
                    continue
                for link in hyperlinks:
                    sub = getattr(link, "SubAddress", None)
                    if not sub:
                        continue  # an external link, not an in-workbook button
                    cell = worksheet.range(link.Range.Address)
                    label = str(cell.value) if cell.value is not None else cell.address
                    buttons.append(
                        Button(
                            label=label,
                            macro=None,
                            sheet=worksheet.name,
                            cell=cell.address.replace("$", ""),
                            kind="hyperlink",
                        )
                    )
        return buttons

    def read_vba_modules(self) -> dict[str, str]:  # pragma: no cover - live Excel only
        """Read the workbook's VBA source, one entry per module.

        Reads the VBA project's component code through the automation object.
        This needs Excel's "Trust access to the VBA project object model" option
        enabled; when it is not, the access is refused and a clear error is
        raised rather than silently returning nothing.
        """
        modules: dict[str, str] = {}
        with self._book(save=False) as book:
            try:
                components = book.api.VBProject.VBComponents
            except Exception as exc:  # trust setting or platform-specific
                raise RuntimeError(
                    "reading VBA through xlwings needs Excel's 'Trust access to "
                    "the VBA project object model' option enabled."
                ) from exc
            for component in components:
                code_module = component.CodeModule
                line_count = int(code_module.CountOfLines)
                code = code_module.Lines(1, line_count) if line_count else ""
                if code:
                    modules[str(component.Name)] = code
        return modules

    # ------------------------------------------------------------------
    # Mutation (WRITE) and execution (EXECUTE)
    # ------------------------------------------------------------------

    def write_cell(  # pragma: no cover - live Excel only
        self, sheet: str, cell: str, value: Any
    ) -> CellEdit:
        """Set one cell's value in a live Excel session and save the workbook."""
        with self._book(save=True) as book:
            worksheet = book.sheets[sheet]
            old_value = worksheet.range(cell).value
            worksheet.range(cell).value = value
        return CellEdit(sheet=sheet, cell=cell, old_value=old_value, new_value=value)

    def run_macro(  # pragma: no cover - live Excel only
        self, macro: str, args: list | None = None
    ) -> MacroRun:
        """Run a macro in a live Excel session, save, and return its result."""
        call_args = list(args or [])
        with self._book(save=True) as book:
            returned = book.macro(macro)(*call_args)
        return MacroRun(macro=macro, args=call_args, returned=self._as_json(returned))

    @staticmethod
    def _as_json(value: Any) -> Any:
        """Coerce a macro's return value to something JSON-serializable."""
        if isinstance(value, _PRIMITIVE):
            return value
        return str(value)


# Entry-point object: the registry constructs this class with a workbook path.
backend = XlwingsBackend
