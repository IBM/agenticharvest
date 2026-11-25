#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Shared fixtures for the Excel domain tests.

Builds the canonical "orders" demo workbook once per test session into a temp
file, so the several tests that read it do not each pay the synthesis cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole Excel test tree cleanly when the optional deps are absent,
# rather than erroring on import, so `pytest` still runs for a core-only install.
pytest.importorskip("openpyxl", reason="Excel domain tests need the 'excel' extra")
pytest.importorskip("oletools", reason="Excel domain tests need the 'excel' extra")

from agenticharvest.domains.excel.samples import build_orders_workbook


@pytest.fixture(scope="session")
def orders_workbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the orders demo workbook once and return its path."""
    dest = tmp_path_factory.mktemp("orders") / "orders.xlsm"
    return build_orders_workbook(dest)
