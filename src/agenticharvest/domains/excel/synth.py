#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Synthesize a genuinely macro-bearing ``.xlsm`` without Excel or LibreOffice.

openpyxl can write worksheets but cannot author the ``vbaProject.bin`` that
carries VBA — that part is an OLE/CFB compound file whose streams are compressed
with the MS-OVBA algorithm. So that the tests and the examples can exercise
AgenticHarvest's real extraction path (openpyxl for structure, oletools/olevba for VBA)
on any machine, this module builds that binary from scratch:

    compress_ovba          MS-OVBA 2.4.1 compression (literal-only, always valid)
    write_compound_file    a minimal MS-CFB writer for small streams
    build_vba_project_bin  assemble PROJECT + VBA/dir + module streams
    build_macro_workbook   openpyxl worksheets + injected vbaProject.bin -> .xlsm

The output is a real workbook that ``oletools`` parses like any other, so the
tests validate the actual code path rather than a mock. The layouts here follow
[MS-OVBA] (VBA project) and [MS-CFB] (compound file) closely enough for
``olevba`` to read them back; they are deliberately minimal, not a general-
purpose authoring library.
"""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# ======================================================================
# MS-OVBA 2.4.1 compression
# ======================================================================

# We emit only literal tokens (no back-references). That is fully valid per the
# spec and is what olevba's decompressor expects; it just does not shrink the
# data, which does not matter for a test fixture. Each 8-byte run is preceded by
# a FlagByte of 0x00 (all eight tokens are literals).
_LITERAL_RUN = 8
# Decompressed bytes per compressed chunk. A chunk's compressed form is
# data + ceil(data/8) flag bytes; keeping the source well under 4096 leaves the
# compressed chunk comfortably within the 4098-byte cap.
_CHUNK_STEP = 2048


def compress_ovba(data: bytes) -> bytes:
    """Compress ``data`` into an MS-OVBA CompressedContainer.

    Args:
        data: The raw bytes to wrap (a VBA source or the dir stream).

    Returns:
        The compressed container: a 0x01 signature byte followed by one or more
        literal-only compressed chunks.
    """
    out = bytearray([0x01])  # CompressedContainer signature
    for start in range(0, len(data), _CHUNK_STEP):
        block = data[start : start + _CHUNK_STEP]
        chunk = bytearray()
        for run_start in range(0, len(block), _LITERAL_RUN):
            chunk.append(0x00)  # FlagByte: all eight tokens are literals
            chunk += block[run_start : run_start + _LITERAL_RUN]
        # CompressedChunkHeader: low 12 bits = (chunk size + 2 header) - 3,
        # bits 12-14 = signature 0b011, bit 15 = compressed flag.
        header = (len(chunk) - 1) & 0x0FFF
        header |= 0b011 << 12
        header |= 0x8000
        out += struct.pack("<H", header)
        out += chunk
    return bytes(out)


# ======================================================================
# MS-CFB (OLE compound file) writer
# ======================================================================

_SECTOR = 512
_MINISECTOR = 64
_MINI_CUTOFF = 4096

_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_FATSECT = 0xFFFFFFFD
_NOSTREAM = 0xFFFFFFFF

# Directory entry object types.
_TYPE_STORAGE = 1
_TYPE_STREAM = 2
_TYPE_ROOT = 5

_DIR_ENTRY_SIZE = 128


class _DirEntry:
    """One 128-byte MS-CFB directory entry (a storage, stream, or the root)."""

    def __init__(self, name: str, etype: int) -> None:
        self.name = name
        self.etype = etype
        self.color = 1  # black; olefile follows pointers and does not rebalance
        self.left = _NOSTREAM
        self.right = _NOSTREAM
        self.child = _NOSTREAM
        self.start = 0
        self.size = 0
        self.data = b""
        self.children: list[_DirEntry] = []
        self.index = 0

    def pack(self) -> bytes:
        """Serialize this entry to its 128-byte on-disk form."""
        name_field = self.name.encode("utf-16-le") + b"\x00\x00"
        name_len = len(name_field)  # includes the UTF-16 null terminator
        name_field = name_field.ljust(64, b"\x00")[:64]
        return struct.pack(
            "<64sHBBIII16sIQQIQ",
            name_field,
            name_len,
            self.etype,
            self.color,
            self.left,
            self.right,
            self.child,
            b"\x00" * 16,  # CLSID
            0,  # state bits
            0,  # creation time
            0,  # modified time
            self.start & 0xFFFFFFFF,
            self.size,
        )


def _cfb_name_key(name: str) -> tuple[int, str]:
    """Return the MS-CFB directory sort key: (length, uppercased name)."""
    return (len(name), name.upper())


def _build_sibling_tree(nodes: list[_DirEntry]) -> int:
    """Wire ``nodes`` (sorted) into a balanced BST, returning the root's index.

    MS-CFB stores each storage's children as a red-black tree keyed by name.
    A balanced BST over the name-sorted children is a valid such tree for
    readers that only follow left/right pointers (as olefile does).
    """
    if not nodes:
        return _NOSTREAM
    mid = len(nodes) // 2
    root = nodes[mid]
    root.left = _build_sibling_tree(nodes[:mid])
    root.right = _build_sibling_tree(nodes[mid + 1 :])
    return root.index


def _pad_to(data: bytes, block: int, fill: bytes = b"\x00") -> bytes:
    """Right-pad ``data`` with ``fill`` (a single byte) to a multiple of ``block``."""
    if len(fill) != 1:
        raise ValueError("fill must be exactly one byte")
    remainder = (-len(data)) % block
    return data + fill * remainder


def write_compound_file(streams: dict[str, bytes]) -> bytes:
    """Write an MS-CFB compound file containing ``streams``.

    Args:
        streams: Mapping of ``"/"``-separated stream path to its content.
            Intermediate path segments become storages (e.g. ``"VBA/dir"``
            creates a ``VBA`` storage holding a ``dir`` stream). All streams are
            assumed smaller than the 4096-byte mini-stream cutoff, so they live
            in the mini stream.

    Returns:
        The complete compound file as bytes.
    """
    root = _DirEntry("Root Entry", _TYPE_ROOT)
    entries: list[_DirEntry] = [root]
    storages: dict[str, _DirEntry] = {"": root}

    for path, data in streams.items():
        segments = path.split("/")
        parent = root
        prefix = ""
        for segment in segments[:-1]:
            prefix = f"{prefix}/{segment}" if prefix else segment
            storage = storages.get(prefix)
            if storage is None:
                storage = _DirEntry(segment, _TYPE_STORAGE)
                storages[prefix] = storage
                entries.append(storage)
                parent.children.append(storage)
            parent = storage
        leaf = _DirEntry(segments[-1], _TYPE_STREAM)
        leaf.data = data
        entries.append(leaf)
        parent.children.append(leaf)

    for index, entry in enumerate(entries):
        entry.index = index
    for entry in entries:
        if entry.children:
            ordered = sorted(entry.children, key=lambda e: _cfb_name_key(e.name))
            entry.child = _build_sibling_tree(ordered)

    mini_stream, minifat = _assemble_mini_stream(entries)

    dir_bytes = _pad_to(b"".join(e.pack() for e in entries), _SECTOR)
    dir_sectors = len(dir_bytes) // _SECTOR

    # FREESECT is 0xFFFFFFFF, so padding unused MiniFAT slots with 0xFF bytes
    # leaves them marked free.
    minifat_bytes = _pad_to(
        b"".join(struct.pack("<I", v) for v in minifat), _SECTOR, b"\xff"
    )
    minifat_sectors = len(minifat_bytes) // _SECTOR

    ministream_bytes = _pad_to(mini_stream, _SECTOR)
    ministream_sectors = len(ministream_bytes) // _SECTOR

    fat_sectors = _count_fat_sectors(dir_sectors + minifat_sectors + ministream_sectors)

    dir_start = fat_sectors
    minifat_start = dir_start + dir_sectors
    ministream_start = minifat_start + minifat_sectors
    total_sectors = ministream_start + ministream_sectors

    root.start = ministream_start if mini_stream else _ENDOFCHAIN
    root.size = len(mini_stream)
    # Repack the directory now that the root's mini-stream location is known.
    dir_bytes = _pad_to(b"".join(e.pack() for e in entries), _SECTOR)

    fat_bytes = _build_fat(
        fat_sectors,
        total_sectors,
        [
            (dir_start, dir_sectors),
            (minifat_start, minifat_sectors),
            (ministream_start, ministream_sectors),
        ],
    )

    header = _build_header(
        fat_sectors=fat_sectors,
        dir_start=dir_start,
        minifat_start=minifat_start if minifat_sectors else _ENDOFCHAIN,
        minifat_sectors=minifat_sectors,
    )

    return header + fat_bytes + dir_bytes + minifat_bytes + ministream_bytes


def _assemble_mini_stream(
    entries: list[_DirEntry],
) -> tuple[bytes, list[int]]:
    """Lay every stream into the mini stream, returning it and the MiniFAT.

    Each stream is padded to a 64-byte mini sector boundary and its chain of
    mini sectors is recorded in the MiniFAT. Each entry's ``start`` and ``size``
    are set in place.
    """
    mini_stream = bytearray()
    minifat: list[int] = []
    for entry in entries:
        if entry.etype != _TYPE_STREAM:
            continue
        entry.size = len(entry.data)
        if not entry.data:
            entry.start = _ENDOFCHAIN
            continue
        first = len(mini_stream) // _MINISECTOR
        entry.start = first
        padded = _pad_to(entry.data, _MINISECTOR)
        count = len(padded) // _MINISECTOR
        mini_stream += padded
        for k in range(count):
            minifat.append(_ENDOFCHAIN if k == count - 1 else first + k + 1)
    return bytes(mini_stream), minifat


def _count_fat_sectors(non_fat_sectors: int) -> int:
    """Return how many FAT sectors are needed to map every sector, incl. FAT."""
    fat_sectors = 1
    while True:
        total = fat_sectors + non_fat_sectors
        needed = (total * 4 + _SECTOR - 1) // _SECTOR
        if needed <= fat_sectors:
            return fat_sectors
        fat_sectors = needed


def _build_fat(
    fat_sectors: int,
    total_sectors: int,
    chains: Iterable[tuple[int, int]],
) -> bytes:
    """Build the FAT: FAT sectors self-mark, each chain links then terminates."""
    fat = [_FREESECT] * total_sectors
    for i in range(fat_sectors):
        fat[i] = _FATSECT
    for start, count in chains:
        for k in range(count):
            fat[start + k] = _ENDOFCHAIN if k == count - 1 else start + k + 1
    entries_per_fat = _SECTOR // 4
    fat += [_FREESECT] * (fat_sectors * entries_per_fat - total_sectors)
    return b"".join(struct.pack("<I", v) for v in fat)


def _build_header(
    *,
    fat_sectors: int,
    dir_start: int,
    minifat_start: int,
    minifat_sectors: int,
) -> bytes:
    """Build the 512-byte MS-CFB header for a version-3 (512-byte sector) file."""
    header = bytearray(_SECTOR)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # signature
    struct.pack_into("<H", header, 24, 0x003E)  # minor version
    struct.pack_into("<H", header, 26, 0x0003)  # major version (3)
    struct.pack_into("<H", header, 28, 0xFFFE)  # byte order (little-endian)
    struct.pack_into("<H", header, 30, 0x0009)  # sector shift (2^9 = 512)
    struct.pack_into("<H", header, 32, 0x0006)  # mini sector shift (2^6 = 64)
    struct.pack_into("<I", header, 40, 0)  # number of directory sectors (0 in v3)
    struct.pack_into("<I", header, 44, fat_sectors)
    struct.pack_into("<I", header, 48, dir_start)
    struct.pack_into("<I", header, 52, 0)  # transaction signature
    struct.pack_into("<I", header, 56, _MINI_CUTOFF)
    struct.pack_into("<I", header, 60, minifat_start)
    struct.pack_into("<I", header, 64, minifat_sectors)
    struct.pack_into("<I", header, 68, _ENDOFCHAIN)  # first DIFAT sector (none)
    struct.pack_into("<I", header, 72, 0)  # number of DIFAT sectors
    difat = [i if i < fat_sectors else _FREESECT for i in range(109)]
    struct.pack_into("<" + "I" * 109, header, 76, *difat)
    return bytes(header)


# ======================================================================
# VBA project (PROJECT stream + VBA/dir + module streams)
# ======================================================================

# dir-stream record ids we emit, in the order olevba reads them.
_MODULENAME = 0x0019
_MODULESTREAMNAME = 0x001A
_MODULEOFFSET = 0x0031
_MODULETYPE_PROCEDURAL = 0x0021
_MODULETYPE_DOCUMENT = 0x0022
_MODULE_TERMINATOR = 0x002B
_PROJECTMODULES = 0x000F
_PROJECTCOOKIE = 0x0013

_CODEPAGE = 1252  # cp1252; the sources here are ASCII


def _record(record_id: int, payload: bytes) -> bytes:
    """A ``id (2) + size (4) + payload`` dir-stream record."""
    return struct.pack("<HI", record_id, len(payload)) + payload


def _fixed(record_id: int, size: int, value: bytes) -> bytes:
    """A dir-stream record whose size field is a fixed constant, not len(value)."""
    return struct.pack("<HI", record_id, size) + value


def _build_dir_stream(module_names: list[str], project_name: str) -> bytes:
    """Build the uncompressed ``VBA/dir`` stream olevba will parse.

    The byte sequence mirrors ``VBA_Project.__init__`` / ``VBA_Module.__init__``
    exactly: the version-independent PROJECT* records, an empty REFERENCE array
    (terminated by the PROJECTMODULES id), then one MODULE record per module.
    """
    name_bytes = project_name.encode("ascii")
    stream = bytearray()

    # PROJECTINFORMATION records --------------------------------------
    stream += _fixed(0x0001, 4, struct.pack("<I", 0x00000001))  # SYSKIND: 32-bit
    stream += _fixed(0x0002, 4, struct.pack("<I", 0x00000409))  # LCID
    stream += _fixed(0x0014, 4, struct.pack("<I", 0x00000409))  # LCIDINVOKE
    stream += _fixed(0x0003, 2, struct.pack("<H", _CODEPAGE))  # CODEPAGE
    stream += _record(0x0004, name_bytes)  # PROJECTNAME
    # PROJECTDOCSTRING: empty MBCS + reserved 0x0040 + empty unicode
    stream += struct.pack("<HI", 0x0005, 0) + struct.pack("<HI", 0x0040, 0)
    # PROJECTHELPFILEPATH: empty helpfile1 + reserved 0x003D + empty helpfile2
    stream += struct.pack("<HI", 0x0006, 0) + struct.pack("<HI", 0x003D, 0)
    stream += _fixed(0x0007, 4, struct.pack("<I", 0))  # HELPCONTEXT
    stream += _fixed(0x0008, 4, struct.pack("<I", 0))  # LIBFLAGS
    # PROJECTVERSION: id, reserved(4)=0x00000004, major(4), minor(2)
    stream += struct.pack("<HIIH", 0x0009, 0x00000004, 1, 0)
    # PROJECTCONSTANTS: empty MBCS + reserved 0x003C + empty unicode
    stream += struct.pack("<HI", 0x000C, 0) + struct.pack("<HI", 0x003C, 0)

    # Empty REFERENCE array: the reader loops until it sees PROJECTMODULES id.
    stream += struct.pack("<H", _PROJECTMODULES)

    # PROJECTMODULES ---------------------------------------------------
    stream += struct.pack("<I", 0x00000002)  # size
    stream += struct.pack("<H", len(module_names))  # module count
    stream += struct.pack("<HI", _PROJECTCOOKIE, 0x00000002) + struct.pack("<H", 0xFFFF)

    for name in module_names:
        stream += _build_module_record(name)
    return bytes(stream)


def _build_module_record(name: str) -> bytes:
    """Build one MODULE record; the stream name equals the module name."""
    name_bytes = name.encode("ascii")
    unicode_bytes = name.encode("utf-16-le")
    module_type = _MODULETYPE_DOCUMENT if is_document_module(name) else _MODULETYPE_PROCEDURAL

    record = bytearray()
    record += _record(_MODULENAME, name_bytes)
    # MODULESTREAMNAME: MBCS name + reserved 0x0032 + unicode name
    record += _record(_MODULESTREAMNAME, name_bytes)
    record += struct.pack("<H", 0x0032)
    record += struct.pack("<I", len(unicode_bytes)) + unicode_bytes
    # MODULEOFFSET: size 4, text offset 0 (source starts at the stream's front)
    record += _fixed(_MODULEOFFSET, 4, struct.pack("<I", 0))
    # MODULETYPE: id, reserved(4)
    record += struct.pack("<HI", module_type, 0)
    # Terminator: id, reserved(4)
    record += struct.pack("<HI", _MODULE_TERMINATOR, 0)
    return bytes(record)


# Module names Excel stores as document modules rather than procedural (.bas).
_DOCUMENT_MODULES = {"ThisWorkbook"}


def is_document_module(name: str) -> bool:
    """Return True for document modules (ThisWorkbook, sheet code names)."""
    return name in _DOCUMENT_MODULES or name.startswith(("Sheet", "sh"))


def _project_stream_text(module_names: list[str], project_name: str) -> bytes:
    """Build the PROJECT stream text (drives module file extensions in olevba)."""
    lines = [f'ID="{{{_ZERO_GUID}}}"']
    for name in module_names:
        key = "Document" if is_document_module(name) else "Module"
        suffix = "/&H00000000" if key == "Document" else ""
        lines.append(f"{key}={name}{suffix}")
    lines.append(f'Name="{project_name}"')
    lines.append('HelpContextID="0"')
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


_ZERO_GUID = "00000000-0000-0000-0000-000000000000"

# A tiny placeholder _VBA_PROJECT stream. olevba only checks that it exists; its
# PerformanceCache content is never parsed when extracting source.
_VBA_PROJECT_STREAM = struct.pack("<HHB", 0xCC61, 0xFFFF, 0x00) + b"\x00\x00"


def build_vba_project_bin(modules: dict[str, str], *, project_name: str = "VBAProject") -> bytes:
    """Assemble a ``vbaProject.bin`` compound file from module sources.

    Args:
        modules: Mapping of module name to its VBA source text.
        project_name: The VBA project name recorded in the dir/PROJECT streams.

    Returns:
        The ``vbaProject.bin`` bytes: a compound file with a ``PROJECT`` stream
        and a ``VBA`` storage holding ``_VBA_PROJECT``, ``dir``, and one
        compressed stream per module.
    """
    module_names = list(modules)
    streams: dict[str, bytes] = {
        "PROJECT": _project_stream_text(module_names, project_name),
        "VBA/_VBA_PROJECT": _VBA_PROJECT_STREAM,
        "VBA/dir": compress_ovba(_build_dir_stream(module_names, project_name)),
    }
    for name, source in modules.items():
        # Source is stored at MODULEOFFSET 0, so the whole stream is the
        # compressed source with no leading PerformanceCache.
        streams[f"VBA/{name}"] = compress_ovba(source.encode("cp1252"))
    return write_compound_file(streams)


# ======================================================================
# .xlsm assembly (worksheets via openpyxl + injected vbaProject.bin)
# ======================================================================

_CT_XLSX_WORKBOOK = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)
_CT_XLSM_WORKBOOK = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
_CT_VBA = "application/vnd.ms-office.vbaProject"
_REL_VBA = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"


class SheetSpec:
    """Specification for one worksheet in a synthesized workbook.

    Args:
        name: The sheet's display name.
        codename: The VBA code name macros refer to the sheet by.
        headers: The header row, written as row 1.
        rows: Data rows written below the header, one list of cell values each.
    """

    def __init__(
        self,
        name: str,
        *,
        codename: str | None = None,
        headers: list[str] | None = None,
        rows: list[list[Any]] | None = None,
    ) -> None:
        self.name = name
        self.codename = codename
        self.headers = headers or []
        self.rows = rows or []


def build_macro_workbook(
    dest: str | Path,
    *,
    sheets: list[SheetSpec],
    modules: dict[str, str],
    named_ranges: dict[str, str] | None = None,
    hyperlink: tuple[str, str, str, str] | None = None,
    project_name: str = "VBAProject",
) -> Path:
    """Build a macro-enabled ``.xlsm`` at ``dest``.

    Args:
        sheets: The worksheets to create, in order, with code names and headers.
        modules: VBA module name -> source, embedded as a real ``vbaProject.bin``.
        named_ranges: Optional workbook-scoped defined names -> reference
            (e.g. ``{"TaxCell": "Config!$A$1"}``).
        hyperlink: Optional clickable-cell control as
            ``(sheet_name, cell, location, label)`` — an internal hyperlink the
            backend recovers as a control.
        project_name: The VBA project name.

    Returns:
        The path written.
    """
    workbook_bytes = _build_worksheets_xlsx(sheets, named_ranges, hyperlink)
    vba_bin = build_vba_project_bin(modules, project_name=project_name)
    xlsm_bytes = _inject_vba(workbook_bytes, vba_bin)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(xlsm_bytes)
    return dest


def _build_worksheets_xlsx(
    sheets: list[SheetSpec],
    named_ranges: dict[str, str] | None,
    hyperlink: tuple[str, str, str, str] | None,
) -> bytes:
    """Create the worksheet content as a standard .xlsx byte buffer."""
    from openpyxl import Workbook
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.hyperlink import Hyperlink

    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    worksheets = {}
    for spec in sheets:
        worksheet = workbook.create_sheet(title=spec.name)
        if spec.codename:
            worksheet.sheet_properties.codeName = spec.codename
        if spec.headers:
            worksheet.append(spec.headers)
        for row in spec.rows:
            worksheet.append(row)
        worksheets[spec.name] = worksheet

    for name, ref in (named_ranges or {}).items():
        workbook.defined_names.add(DefinedName(name=name, attr_text=ref))

    if hyperlink is not None:
        sheet_name, cell, location, label = hyperlink
        target = worksheets[sheet_name][cell]
        target.value = label
        target.hyperlink = Hyperlink(ref=cell, location=location, display=label)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _inject_vba(workbook_bytes: bytes, vba_bin: bytes) -> bytes:
    """Turn a plain .xlsx byte buffer into a macro-enabled .xlsm.

    Rewrites the package so it declares the macro-enabled workbook content type,
    registers the vbaProject relationship, and carries the vbaProject.bin part.
    """
    source = zipfile.ZipFile(io.BytesIO(workbook_bytes), "r")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = _patch_content_types(data)
            elif item.filename == "xl/_rels/workbook.xml.rels":
                data = _patch_workbook_rels(data)
            archive.writestr(item, data)
        archive.writestr("xl/vbaProject.bin", vba_bin)
    source.close()
    return output.getvalue()


def _patch_content_types(data: bytes) -> bytes:
    """Declare the macro-enabled workbook type and the vbaProject part type."""
    text = data.decode("utf-8")
    text = text.replace(_CT_XLSX_WORKBOOK, _CT_XLSM_WORKBOOK)
    if 'Extension="bin"' not in text:
        default = f'<Default Extension="bin" ContentType="{_CT_VBA}"/>'
        text = text.replace("</Types>", default + "</Types>")
    return text.encode("utf-8")


def _patch_workbook_rels(data: bytes) -> bytes:
    """Add the workbook -> vbaProject.bin relationship."""
    text = data.decode("utf-8")
    relationship = (
        f'<Relationship Id="rIdVbaProject" Type="{_REL_VBA}" '
        f'Target="vbaProject.bin"/>'
    )
    return text.replace("</Relationships>", relationship + "</Relationships>").encode(
        "utf-8"
    )
