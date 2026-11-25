#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Author a macro workbook that real Excel runs: an ``.xlsx`` plus ``.bas`` modules.

:mod:`agenticharvest.domains.excel.synth` writes a ``.xlsm`` whose VBA project is
complete enough for ``olevba`` to read, so IR extraction, reads, and cell writes
work everywhere. Excel itself, however, will not load that synthesized project:
it offers to "recover" the workbook and drops the macros. Running a macro in a
live Excel therefore needs a workbook Excel authored.

This module writes the parts a person assembles in Excel in a minute: the sheets
as a plain ``.xlsx`` (with the VBA code names the macros refer to) and one
``.bas`` file per module. Importing the ``.bas`` files in the VBA editor and
saving as ``.xlsm`` yields a workbook Excel both opens cleanly and runs.

    write_kit(dest_dir, sheets=..., modules=...)   ->  book.xlsx + modules/*.bas

Document modules (``ThisWorkbook``, sheet code names) cannot be imported as
``.bas`` files: Excel creates them with the workbook. Their source is written to
``*.cls.txt`` instead, to paste into the existing module.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agenticharvest.domains.excel.synth import SheetSpec, is_document_module


class Kit(BaseModel):
    """The files :func:`write_kit` produced.

    Attributes:
        workbook: The ``.xlsx`` holding the sheets.
        modules: One ``.bas`` file per standard module, to import.
        documents: One ``.cls.txt`` file per document module, to paste.
    """

    workbook: Path
    modules: list[Path] = []
    documents: list[Path] = []


def write_kit(
    dest_dir: str | Path,
    *,
    sheets: list[SheetSpec],
    modules: dict[str, str],
    name: str = "book",
) -> Kit:
    """Write ``name.xlsx`` and the VBA module files into ``dest_dir``.

    Args:
        dest_dir: The directory to write into (created if missing).
        sheets: The worksheets, with the code names the macros refer to.
        modules: VBA module name -> source. Each source starts with its
            ``Attribute VB_Name`` line, as an exported ``.bas`` file does.
        name: The workbook's base file name.

    Returns:
        The paths written.
    """
    from openpyxl import Workbook

    dest = Path(dest_dir)
    (dest / "modules").mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    workbook.remove(workbook.active)
    for spec in sheets:
        worksheet = workbook.create_sheet(title=spec.name)
        if spec.codename:
            worksheet.sheet_properties.codeName = spec.codename
        if spec.headers:
            worksheet.append(spec.headers)
        for row in spec.rows:
            worksheet.append(row)
    book = dest / f"{name}.xlsx"
    workbook.save(book)

    kit = Kit(workbook=book)
    for module, source in modules.items():
        # Excel's importer expects CRLF line endings and the Windows code page.
        text = source.replace("\r\n", "\n").replace("\n", "\r\n").encode("cp1252")
        if is_document_module(module):
            path = dest / "modules" / f"{module}.cls.txt"
            kit.documents.append(path)
        else:
            path = dest / "modules" / f"{module}.bas"
            kit.modules.append(path)
        path.write_bytes(text)
    return kit


__all__ = ["Kit", "write_kit"]
