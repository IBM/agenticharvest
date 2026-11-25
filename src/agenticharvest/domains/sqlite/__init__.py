#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The SQLite domain — a database becomes agent-callable tools.

The second reference domain, and the proof of AgenticHarvest's one rule: only
``domains/`` knows what the artifact is. A SQLite database maps onto the same
shapes the workbook does —

    tables / views      the containers of state      (a workbook's sheets)
    triggers            code that reacts and writes  (a workbook's macros)

— and exposes them through the same ``ToolSpec`` contract, the same gateway, the
same OCEL tap and miners, and the same agent loop. Nothing outside this package
changed to add it. It uses only the standard-library ``sqlite3``, so it needs no
extra to install.

Tools (see ``tools``):
    list_tables      tables and views, with row counts
    describe_table   columns, keys, foreign keys, and the triggers on a table
    list_triggers    every trigger: its table, event, and the tables it writes
    query            run a read-only SELECT                          (read)
    execute          run one INSERT / UPDATE / DELETE statement      (write, gated)

Modules:
    tools       the specs and handlers
    ocel        the OCEL mapper (SqliteOCEL): database / table objects
    samples     a sample support-ticket database for demos and tests
"""

from agenticharvest.domains.base import SimpleDomain
from agenticharvest.domains.sqlite.ocel import SqliteOCEL
from agenticharvest.domains.sqlite.tools import sqlite_tools

# The entry-point target, mirroring ``agenticharvest.domains.excel:domain``.
domain = SimpleDomain(name="sqlite", tools=sqlite_tools())

__all__ = ["SqliteOCEL", "domain"]
