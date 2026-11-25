#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Sanity checks that the synthesized workbook is really macro-bearing.

The extraction tests are only meaningful if the fixture is a genuine
macro-enabled workbook that oletools accepts. These tests validate the
synthesizer itself — the from-scratch MS-OVBA + MS-CFB writer — by round-tripping
its output back through olevba, independently of AgenticHarvest's own extraction code.
"""

from __future__ import annotations

from pathlib import Path

from oletools.olevba import VBA_Parser

from agenticharvest.domains.excel.synth import SheetSpec, build_macro_workbook


def test_synth_workbook_is_detected_as_macro_bearing(tmp_path: Path) -> None:
    dest = build_macro_workbook(
        tmp_path / "mini.xlsm",
        sheets=[SheetSpec("Sheet1", codename="Sheet1")],
        modules={"modMini": 'Attribute VB_Name = "modMini"\nSub A()\nEnd Sub\n'},
    )
    parser = VBA_Parser(str(dest))
    try:
        assert parser.detect_vba_macros() is True
    finally:
        parser.close()


def test_synth_workbook_round_trips_module_source(tmp_path: Path) -> None:
    code = 'Attribute VB_Name = "modMini"\nSub Greet()\n    Debug.Print 1\nEnd Sub\n'
    dest = build_macro_workbook(
        tmp_path / "mini.xlsm",
        sheets=[SheetSpec("Sheet1", codename="Sheet1")],
        modules={"modMini": code},
    )
    parser = VBA_Parser(str(dest))
    try:
        extracted = {
            vba_filename: vba_code
            for _c, _s, vba_filename, vba_code in parser.extract_macros()
            if vba_code
        }
    finally:
        parser.close()
    # olevba reports one stream per module; the module's body survives intact.
    joined = "\n".join(extracted.values())
    assert "Sub Greet()" in joined
    assert "Debug.Print 1" in joined
