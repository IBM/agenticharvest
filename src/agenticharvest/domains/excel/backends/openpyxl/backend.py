#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""openpyxl + oletools backend.

Reads workbook structure with openpyxl and VBA with oletools, directly from the
file — no Excel installation required. This is the read-only backend the IR
extractor uses by default and the safe path for anything that does not need a
live recalc. It covers .xlsx and .xlsm natively; a .xlsb reader can implement
the same :class:`ExcelBackend` interface without changing any caller.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

from agenticharvest.domains.excel.backends.base import (
    Capability,
    CellEdit,
    ExcelBackend,
)
from agenticharvest.domains.excel.ir.models import Button, NamedRange, Sheet

# Excel-internal defined names that are noise for analysis; skipped so the IR
# only carries analyst-relevant names.
_INTERNAL_NAME_PREFIXES = (
    "_xlnm.",
    "_xlfn.",
    "_FilterDatabase",
    "Print_Area",
    "Print_Titles",
)


class OpenpyxlBackend(ExcelBackend):
    """Read workbook structure and VBA, and write cell values, via the file.

    openpyxl operates directly on the file, so it reads the IR and edits a cell
    without an Excel install — but it does not execute VBA, so it advertises
    ``READ`` and ``WRITE`` but not ``EXECUTE``.
    """

    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.READ, Capability.WRITE}
    )

    @property
    def name(self) -> str:
        return "openpyxl"

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------

    def read_sheets(
        self, max_header_cols: int = 64
    ) -> tuple[list[Sheet], dict[str, str]]:
        import openpyxl

        # Not read_only: the read-only worksheet proxy omits sheet_properties,
        # and codeName lives there. codeName is what VBA refers sheets by
        # (e.g. ``shOrders``), so the data-flow analysis needs it — losing it
        # would break the mapping from macro references back to sheets.
        # Not keep_vba: we never re-save (VBA is read separately via oletools),
        # and retaining the source archive only to discard it leaves a dangling
        # ZipFile that warns on garbage collection.
        workbook = openpyxl.load_workbook(self.path, data_only=True)
        sheets: list[Sheet] = []
        codename_map: dict[str, str] = {}
        try:
            for index, name in enumerate(workbook.sheetnames):
                worksheet = workbook[name]
                codename = getattr(worksheet.sheet_properties, "codeName", None)
                if codename:
                    codename_map[codename] = name
                sheets.append(
                    Sheet(
                        name=name,
                        codename=codename,
                        headers=self._read_header_row(worksheet, max_header_cols),
                        index=index,
                    )
                )
        finally:
            workbook.close()
        return sheets, codename_map

    @staticmethod
    def _read_header_row(worksheet: Any, max_cols: int) -> list[str]:
        """Return the first row's values as strings, trailing blanks trimmed.

        Args:
            worksheet: An openpyxl read-only worksheet.
            max_cols: Maximum number of leading columns to read.

        Returns:
            The header values left to right; empty if there is no first row.
        """
        row_iter = worksheet.iter_rows(
            min_row=1, max_row=1, max_col=max_cols, values_only=True
        )
        first = next(row_iter, None)
        if not first:
            return []
        values = ["" if cell is None else str(cell).strip() for cell in first]
        while values and not values[-1]:
            values.pop()
        return values

    def read_named_ranges(self) -> list[NamedRange]:
        import openpyxl

        workbook = openpyxl.load_workbook(self.path, data_only=True)
        result: list[NamedRange] = []
        try:
            for name in workbook.defined_names:
                if self._is_internal_name(name):
                    continue
                result.append(
                    self._named_range_from(
                        name, workbook.defined_names[name], scope="workbook"
                    )
                )
            for sheet_name in workbook.sheetnames:
                worksheet = workbook[sheet_name]
                for name in worksheet.defined_names:
                    if self._is_internal_name(name):
                        continue
                    result.append(
                        self._named_range_from(
                            name, worksheet.defined_names[name], scope="sheet"
                        )
                    )
        finally:
            workbook.close()
        return result

    @staticmethod
    def _is_internal_name(name: str) -> bool:
        """Return ``True`` for Excel-internal names that should be skipped."""
        return any(name.startswith(prefix) for prefix in _INTERNAL_NAME_PREFIXES)

    @staticmethod
    def _named_range_from(name: str, defined: Any, scope: str) -> NamedRange:
        """Build a ``NamedRange`` from an openpyxl defined name.

        Args:
            name: The defined name.
            defined: The openpyxl ``DefinedName`` object.
            scope: ``"workbook"`` or ``"sheet"``.

        Returns:
            The corresponding ``NamedRange``; a name with no cell destination
            (a constant or formula name) keeps its raw value in ``ref``.
        """
        try:
            destinations = list(defined.destinations)
        except (AttributeError, TypeError):
            # openpyxl raises when the name's value is not a cell reference.
            destinations = []
        if destinations:
            sheet, ref = destinations[0]
            return NamedRange(name=name, sheet=sheet, ref=ref, scope=scope)
        return NamedRange(name=name, sheet=None, ref=defined.value, scope=scope)

    def read_controls(self) -> list[Button]:
        import openpyxl

        workbook = openpyxl.load_workbook(self.path, data_only=True)
        buttons: list[Button] = []
        try:
            for sheet_name in workbook.sheetnames:
                worksheet = workbook[sheet_name]
                for cell in self._iter_hyperlink_cells(worksheet):
                    link = cell.hyperlink
                    # A self-referencing internal link (a location within the
                    # sheet) is the standard "clickable cell" button trick.
                    if link is None or not getattr(link, "location", None):
                        continue
                    label = (
                        str(cell.value) if cell.value is not None else cell.coordinate
                    )
                    buttons.append(
                        Button(
                            label=label,
                            macro=None,
                            sheet=sheet_name,
                            cell=cell.coordinate,
                            kind="hyperlink",
                        )
                    )
        finally:
            workbook.close()
        return buttons

    @staticmethod
    def _iter_hyperlink_cells(worksheet: Any) -> Iterator[Any]:
        """Yield cells on ``worksheet`` that carry a hyperlink.

        On openpyxl's read path a hyperlink is attached to its cell, while the
        worksheet's ``_hyperlinks`` collection is a write-side accumulator that
        stays empty after a load. So this walks the used range and yields any
        cell whose ``hyperlink`` is set, rather than trusting ``_hyperlinks``.
        """
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.hyperlink is not None:
                    yield cell

    # ------------------------------------------------------------------
    # VBA
    # ------------------------------------------------------------------

    def read_vba_modules(self) -> dict[str, str]:
        try:
            from oletools.olevba import VBA_Parser
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "VBA extraction requires oletools. Install the 'excel' extra: "
                "pip install 'agenticharvest[excel]'"
            ) from exc

        modules: dict[str, str] = {}
        parser = VBA_Parser(self.path)
        try:
            if not parser.detect_vba_macros():
                return modules
            # extract_macros yields (container, stream_path, vba_filename, code).
            for _container, _stream, vba_filename, code in parser.extract_macros():
                if not code:
                    continue
                module_name = self._module_name(vba_filename)
                # Multiple streams can map to one module name; concatenate so no
                # procedure is lost.
                if module_name in modules:
                    modules[module_name] += "\n" + code
                else:
                    modules[module_name] = code
        finally:
            parser.close()
        return modules

    @staticmethod
    def _module_name(vba_filename: str) -> str:
        """Reduce a VBA stream filename to a bare module name.

        Args:
            vba_filename: The name oletools reports, e.g. ``"Module1.bas"``.

        Returns:
            The name without directory or extension.
        """
        base = vba_filename.replace("\\", "/").split("/")[-1]
        if "." in base:
            base = base.rsplit(".", 1)[0]
        return base

    # ------------------------------------------------------------------
    # Cell values
    # ------------------------------------------------------------------

    def read_range(
        self, sheet: str, ref: str | None = None, max_rows: int = 200
    ) -> list[list[Any]]:
        """Read cell values from the file, row by row.

        Loaded with ``data_only=True`` so a formula cell reports the value Excel
        last computed for it rather than the formula text — what a person
        looking at the sheet would see. A file never opened in Excel carries no
        cached values, so its formula cells read as ``None``.

        Raises:
            ValueError: If the workbook has no sheet with that display name.
        """
        import openpyxl
        from openpyxl.utils.cell import range_boundaries

        workbook = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            if sheet not in workbook.sheetnames:
                known = ", ".join(workbook.sheetnames)
                raise ValueError(f"No sheet named '{sheet}'. Sheets: {known}.")
            worksheet = workbook[sheet]
            # range_boundaries handles "B2", "A1:C9", "A:C" and "2:5" alike; an
            # open side comes back as None, which iter_rows reads as unbounded.
            bounds: tuple[int | None, ...] = (None, None, None, None)
            if ref:
                bounds = range_boundaries(ref.replace("$", ""))
            min_col, min_row, max_col, max_row = bounds
            values: list[list[Any]] = []
            for row in worksheet.iter_rows(
                min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col,
                values_only=True,
            ):
                if len(values) >= max_rows:
                    break
                values.append([self._json_value(v) for v in row])
        finally:
            workbook.close()
        # The used range can carry trailing blank rows; they say nothing.
        while values and all(v is None for v in values[-1]):
            values.pop()
        return values

    @staticmethod
    def _json_value(value: Any) -> Any:
        """Coerce a cell value to something JSON-serializable."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_cell(self, sheet: str, cell: str, value: Any) -> CellEdit:
        """Set one cell's value in the file, returning its before/after.

        The workbook is loaded with ``keep_vba=True`` so a macro-enabled
        ``.xlsm`` keeps its VBA project through the round-trip, and *without*
        ``data_only`` so formulas in other cells survive the save rather than
        being flattened to their cached values.

        Args:
            sheet: The display name of the sheet to write to.
            cell: The A1-style coordinate, e.g. ``"B1"``.
            value: The value to write.

        Returns:
            A :class:`CellEdit` with the old and new cell values.

        Raises:
            ValueError: If the workbook has no sheet with that display name.
        """
        import openpyxl

        workbook = openpyxl.load_workbook(self.path, keep_vba=True)
        try:
            if sheet not in workbook.sheetnames:
                known = ", ".join(workbook.sheetnames)
                raise ValueError(f"No sheet named '{sheet}'. Sheets: {known}.")
            target = workbook[sheet][cell]
            old_value = target.value
            target.value = value
            workbook.save(self.path)
            _drop_empty_defined_names(self.path)
        finally:
            # keep_vba holds the source archive open to copy the VBA project on
            # save; openpyxl's close() does not release it, so it would warn on
            # garbage collection ("I/O operation on closed file"). Close it here.
            vba_archive = getattr(workbook, "vba_archive", None)
            if vba_archive is not None:
                try:
                    vba_archive.close()
                except Exception:  # noqa: BLE001, S110 - best-effort cleanup
                    pass
            workbook.close()
        return CellEdit(sheet=sheet, cell=cell, old_value=old_value, new_value=value)


def _drop_empty_defined_names(path: str) -> None:
    """Remove the empty ``<definedNames/>`` openpyxl writes for a name-less workbook.

    The schema requires ``definedNames`` to hold at least one name, and Excel
    reports a workbook carrying an empty one as corrupt ("We found a problem with
    some content") and offers to repair it, which drops the VBA project. Other
    readers, openpyxl included, accept either form, so removing it is safe.
    """
    import re
    import shutil
    import tempfile
    import zipfile

    part = "xl/workbook.xml"
    with zipfile.ZipFile(path) as source:
        text = source.read(part).decode("utf-8")
        # openpyxl serializes with lxml when it is installed (``<definedNames/>``)
        # and with the standard library otherwise (``<definedNames />``).
        fixed = re.sub(r"<definedNames\s*/>|<definedNames>\s*</definedNames>", "", text)
        if fixed == text:
            return
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsm") as handle:
            temporary = handle.name
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = fixed.encode("utf-8") if item.filename == part else source.read(item)
                target.writestr(item, data)
    shutil.move(temporary, path)


# Entry-point object: the registry constructs this class with a workbook path.
backend = OpenpyxlBackend
