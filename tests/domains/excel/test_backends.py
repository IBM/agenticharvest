#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Tests for backend capabilities, graceful degradation, and selection.

These cover the capability-based backend model without needing a live Excel:
the xlwings backend must import and report itself unavailable when the
``excel-live`` extra (or Excel itself) is absent, and ``select_backend`` must
pick openpyxl for a write while returning ``None`` for an execute capability no
installed backend can satisfy here — the honest-degradation path the run_macro
tool relies on.
"""

from __future__ import annotations

import pytest

from agenticharvest.domains.excel.backends.base import (
    Capability,
    ExcelBackend,
    UnsupportedOperation,
)
from agenticharvest.domains.excel.backends.openpyxl import OpenpyxlBackend
from agenticharvest.domains.excel.backends.registry import select_backend
from agenticharvest.domains.excel.backends.xlwings import XlwingsBackend


def test_xlwings_advertises_write_and_execute() -> None:
    caps = XlwingsBackend.capabilities
    assert Capability.EXECUTE in caps
    assert Capability.WRITE in caps


def test_xlwings_available_is_a_bool_and_absent_without_the_extra() -> None:
    # Importing the module never requires xlwings; availability is probed.
    available = XlwingsBackend("book.xlsm").available()
    assert isinstance(available, bool)
    try:
        import xlwings  # noqa: F401
    except ImportError:
        assert available is False


def test_default_backend_operations_are_unsupported() -> None:
    # A backend that overrides nothing inherits the raising defaults.
    class Bare(ExcelBackend):
        @property
        def name(self) -> str:
            return "bare"

        def read_sheets(self, max_header_cols: int = 64):  # pragma: no cover
            return [], {}

        def read_named_ranges(self):  # pragma: no cover
            return []

        def read_controls(self):  # pragma: no cover
            return []

        def read_vba_modules(self):  # pragma: no cover
            return {}

    bare = Bare("book.xlsm")
    with pytest.raises(UnsupportedOperation):
        bare.write_cell("Sheet1", "A1", 1)
    with pytest.raises(UnsupportedOperation):
        bare.run_macro("Foo")


def test_select_backend_picks_openpyxl_for_write() -> None:
    backend = select_backend("book.xlsm", Capability.WRITE)
    assert isinstance(backend, OpenpyxlBackend)
    assert backend.path == "book.xlsm"


def test_select_backend_returns_none_for_execute_without_a_live_engine() -> None:
    if XlwingsBackend("x").available():
        pytest.skip("a live xlwings backend is installed")
    assert select_backend("book.xlsm", Capability.EXECUTE) is None
