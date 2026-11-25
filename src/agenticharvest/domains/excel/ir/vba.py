#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Static analysis of a workbook's VBA.

Pure text analysis of VBA source that a backend has already extracted from the
artifact (``ExcelBackend.read_vba_modules``). Given the module sources, it
infers two relationships without executing anything:

    call graph   which macro invokes which other macro
    data flow    which sheet each macro reads from and writes to

The inference is deliberately conservative and pattern-based — it recognizes the
common ways code references a sheet and assigns to it, and errs toward "reads"
when a reference's direction is ambiguous. It is not a VBA interpreter; deeper
cases (heavy aliasing, computed sheet names) are left as an extension point
rather than guessed at. Nothing here touches a file: reading the workbook is the
backend's job, so the analysis is engine-agnostic and unit-testable on plain
strings.
"""

from __future__ import annotations

import re

from agenticharvest.domains.excel.ir.models import Macro

# A Sub or Function definition header, capturing the procedure name. Matches an
# optional Public/Private/Friend modifier and an optional Static, then the kind
# and the identifier. The body is delimited separately so nested constructs do
# not confuse the split.
_PROC_HEADER = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+)?(?:Static\s+)?"
    r"(?P<kind>Sub|Function)\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)

_EVENT_HANDLER = re.compile(r"^(?:Worksheet|Workbook)_", re.IGNORECASE)

# A VBA identifier used as a call target. We resolve these against the set of
# known macro names, so over-matching here is harmless — unknown identifiers
# (locals, built-ins) are simply dropped when they do not name a macro.
_IDENTIFIER = re.compile(r"\b([A-Za-z_]\w*)\b")

# References to a sheet by literal name: Sheets("X") / Worksheets("X").
_SHEET_LITERAL = re.compile(
    r"(?:Sheets|Worksheets)\s*\(\s*\"([^\"]+)\"\s*\)", re.IGNORECASE
)


def parse_macros(modules: dict[str, str]) -> list[Macro]:
    """Parse procedure definitions out of each module's source.

    Splits every module into its Sub/Function bodies and returns one ``Macro``
    per procedure, carrying its source, module, and whether it is an Excel event
    handler. Event handlers of the same name can legitimately recur across sheet
    modules; both are kept, so a later step may disambiguate by module.

    Args:
        modules: Module-name -> source mapping from :func:`extract_modules`.

    Returns:
        The parsed macros, in module then definition order.
    """
    macros: list[Macro] = []
    for module_name, code in modules.items():
        for name, body in _split_procedures(code):
            macros.append(
                Macro(
                    name=name,
                    module=module_name,
                    source=body,
                    is_event_handler=bool(_EVENT_HANDLER.match(name)),
                )
            )
    return macros


def _split_procedures(code: str) -> list[tuple[str, str]]:
    """Split module source into ``(procedure_name, body)`` pairs.

    A procedure runs from its ``Sub``/``Function`` header up to (but not
    including) the next header, or end of module. This keeps the full body —
    comments and all — with each procedure for later analysis and display.

    Args:
        code: One module's VBA source.

    Returns:
        Procedure name/body pairs in definition order.
    """
    headers = list(_PROC_HEADER.finditer(code))
    procedures: list[tuple[str, str]] = []
    for i, header in enumerate(headers):
        start = header.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(code)
        procedures.append((header.group("name"), code[start:end]))
    return procedures


def build_call_edges(macros: list[Macro]) -> list[tuple[str, str]]:
    """Infer macro-to-macro call edges from procedure bodies.

    A call is any mention of another macro's name inside a procedure's body
    (VBA calls need no parentheses, so name matching is the reliable signal).
    Self-references and references to unknown identifiers are dropped. Duplicate
    edges are collapsed.

    Args:
        macros: All macros in the workbook.

    Returns:
        Unique ``(caller_name, callee_name)`` pairs.
    """
    names = {m.name for m in macros}
    edges: set[tuple[str, str]] = set()
    for macro in macros:
        if not macro.source:
            continue
        # Skip the header line so a Sub's own name is not read as a self-call.
        body = macro.source.split("\n", 1)[-1]
        for token in _IDENTIFIER.findall(body):
            if token in names and token != macro.name:
                edges.add((macro.name, token))
    return sorted(edges)


def infer_data_flow(
    macros: list[Macro],
    codename_map: dict[str, str],
    sheet_names: list[str],
) -> dict[str, dict[str, list[str]]]:
    """Infer which sheets each macro reads from and writes to.

    Recognizes two ways VBA names a sheet — by its code name (``wsTemp.Range``)
    and by literal name (``Sheets("Temp")``) — and classifies each reference as
    a write when it assigns a value/formula, clears, deletes, or bulk-populates,
    and otherwise as a read. When a reference's direction cannot be determined
    it is treated as a read, which is the safe assumption for data-flow role
    inference (it never invents an output).

    Args:
        macros: All macros in the workbook.
        codename_map: VBA code name -> sheet display name.
        sheet_names: All sheet display names, for resolving literal references.

    Returns:
        Mapping of macro name to ``{"reads": [...], "writes": [...]}`` with
        sorted, de-duplicated sheet display names. Macros touching no sheet are
        omitted.
    """
    flows: dict[str, dict[str, list[str]]] = {}
    known_sheets = set(sheet_names)
    for macro in macros:
        if not macro.source:
            continue
        reads: set[str] = set()
        writes: set[str] = set()

        # By code name: any token that is a known code name resolves to a sheet.
        for codename, sheet in codename_map.items():
            if re.search(rf"\b{re.escape(codename)}\b", macro.source):
                _classify_reference(macro.source, codename, sheet, reads, writes)

        # By literal name: Sheets("X") / Worksheets("X").
        for match in _SHEET_LITERAL.finditer(macro.source):
            sheet = match.group(1)
            if sheet in known_sheets:
                _classify_at(macro.source, match.start(), sheet, reads, writes)

        if reads or writes:
            flows[macro.name] = {
                "reads": sorted(reads),
                "writes": sorted(writes),
            }
    return flows


# Verbs that mark a reference as a write when they follow it. Kept as a single
# alternation so the intent is legible and easy to extend.
_WRITE_MARKERS = (
    r"\.(?:Value2?|Formula(?:R1C1|Local|2)?)\s*=",  # assignment to a cell value
    r"\.ClearContents\b",
    r"\.Clear\b",
    r"\.Delete\b",
    r"\.CopyFromRecordset\b",
    r"\.Resize\b",
)
_WRITE_RE = re.compile("|".join(_WRITE_MARKERS), re.IGNORECASE)

# Where a VBA statement ends: a line break not preceded by the ``_`` line
# continuation. (A ``:`` separator is not split on: it also appears inside range
# literals such as ``"A1:B9"``.)
_STATEMENT_END = re.compile(r"(?<! _)\r?\n")


def _classify_reference(
    source: str,
    token: str,
    sheet: str,
    reads: set[str],
    writes: set[str],
) -> None:
    """Classify every reference to ``token`` in ``source`` as read and/or write.

    Args:
        source: The macro's VBA source.
        token: The identifier (code name) that resolves to ``sheet``.
        sheet: The sheet display name to record against.
        reads: Accumulator set of read sheets (mutated in place).
        writes: Accumulator set of written sheets (mutated in place).
    """
    for match in re.finditer(rf"\b{re.escape(token)}\b", source):
        _classify_at(source, match.start(), sheet, reads, writes)


def _classify_at(
    source: str,
    position: int,
    sheet: str,
    reads: set[str],
    writes: set[str],
) -> None:
    """Classify a single sheet reference at ``position`` in ``source``.

    Looks at the text immediately following the reference for a write marker; if
    one is present it is a write, otherwise it is treated as a read. A reference
    can be both when the surrounding window covers a write and a later read.

    Args:
        source: The macro's VBA source.
        position: Index where the reference begins.
        sheet: The sheet display name to record against.
        reads: Accumulator set of read sheets (mutated in place).
        writes: Accumulator set of written sheets (mutated in place).
    """
    # The rest of the statement holding the reference reveals intent (e.g.
    # ``.Range("A1").Value =``). Stopping at the statement's end keeps a write on
    # the next line from marking this reference as a write too.
    window = _STATEMENT_END.split(source[position:], maxsplit=1)[0]
    if _WRITE_RE.search(window):
        writes.add(sheet)
    else:
        reads.add(sheet)


def module_of(name: str, macros: list[Macro]) -> str | None:
    """Return the module a macro is defined in, or ``None`` if not found."""
    for macro in macros:
        if macro.name == name:
            return macro.module
    return None
