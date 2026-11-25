#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Sample macro-bearing workbooks, shipped with the package.

One place defines the example workbooks that the tests, the README quick start,
and the ``examples/`` walkthroughs build on, so the fixtures and the demos can
never drift apart. Each is synthesized as a genuine ``.xlsm`` (see
:mod:`agenticharvest.domains.excel.synth`) — no Excel needed.

``build_orders_workbook`` is the small "orders" workflow:

    Orders   (input)   raw order rows the user supplies
    Summary  (output)  totals a macro computes and writes back
    Config   (input)   a tax-rate lookup the macros read

The VBA wires these together into a call graph and a read/write data flow:

    Workbook_Open -> RefreshSummary -> ClearSummary        (writes Summary)
                                    -> WriteTotals          (writes Summary)
                                       -> ComputeTotal      (reads Orders)
                                          -> TaxRate        (reads Config)
    ConfigName                                              (reads Config, literal)

Each procedure is kept small and single-purpose so the conservative data-flow
heuristic classifies every reference unambiguously — reads live in their own
functions that return a value, writes live in their own procedures. That is also
just good macro style, so the example doubles as a realistic workbook.
"""

from __future__ import annotations

from pathlib import Path

from agenticharvest.domains.excel.synth import SheetSpec, build_macro_workbook

# --- Sheets ----------------------------------------------------------------
# codeName is what the VBA refers sheets by (``shOrders`` etc.); the data-flow
# analysis maps those code names back to these display names.
SHEETS: list[SheetSpec] = [
    SheetSpec("Orders", codename="shOrders", headers=["OrderID", "Qty", "Price"]),
    SheetSpec("Summary", codename="shSummary", headers=["Metric", "Value"]),
    SheetSpec("Config", codename="shConfig", headers=["TaxRate"]),
]

# --- VBA modules -----------------------------------------------------------
_MOD_ORDERS = '''\
Attribute VB_Name = "modOrders"
Option Explicit

' Entry point: rebuild the Summary sheet from scratch.
Public Sub RefreshSummary()
    ClearSummary
    WriteTotals
End Sub

' Blank out the old summary values (a write to Summary).
Sub ClearSummary()
    shSummary.Range("B1:B10").ClearContents
End Sub

' Write the freshly computed total back into Summary.
Sub WriteTotals()
    shSummary.Range("B1").Value = ComputeTotal()
End Sub

' Read Orders and fold in the tax rate. Reads Orders (never writes).
Function ComputeTotal() As Double
    ComputeTotal = shOrders.Range("A2").Value * TaxRate()
End Function

' Read the tax rate from Config by code name.
Function TaxRate() As Double
    TaxRate = shConfig.Range("A1").Value
End Function

' Read Config by literal sheet name, to exercise the Sheets("X") path.
Function ConfigName() As String
    ConfigName = Sheets("Config").Range("A1").Value
End Function
'''

_THIS_WORKBOOK = '''\
Attribute VB_Name = "ThisWorkbook"
' Opening the workbook rebuilds the summary — an event handler.
Private Sub Workbook_Open()
    RefreshSummary
End Sub
'''

MODULES: dict[str, str] = {
    "modOrders": _MOD_ORDERS,
    "ThisWorkbook": _THIS_WORKBOOK,
}

# --- Other structure -------------------------------------------------------
NAMED_RANGES: dict[str, str] = {"TaxCell": "Config!$A$1"}

# A self-referencing hyperlink on Summary acts as a clickable "back" control.
# It sits below the header row so it does not land in the header read.
HYPERLINK: tuple[str, str, str, str] = ("Summary", "D2", "Orders!A1", "Back to Orders")


def build_orders_workbook(dest: str | Path) -> Path:
    """Write the orders demo workbook to ``dest`` and return its path."""
    return build_macro_workbook(
        dest,
        sheets=SHEETS,
        modules=MODULES,
        named_ranges=NAMED_RANGES,
        hyperlink=HYPERLINK,
    )


# ==========================================================================
# Expense-claim review — a real approval workflow
# ==========================================================================
#
# The workbook a finance team actually passes around each month. An employee's
# claims land on Claims; Policy holds the per-category limits; a reviewer checks
# each claim against policy and records a decision on Decisions; ApproveClaims /
# FlagForReview are the macros that, in the live workbook, stamp those decisions.
#
#     Claims    (input)   one row per claim: id, employee, category, amount
#     Policy    (input)   category -> per-claim limit
#     Decisions (output)  claim id -> decision + reason, written by the macros
#
#     ReviewClaims -> CheckPolicy  -> PolicyLimit     (reads Policy)
#                  -> ApproveClaims                    (writes Decisions)
#                  -> FlagForReview                    (writes Decisions)
#     LoadClaims                                       (reads Claims)

EXPENSE_POLICY: dict[str, float] = {
    "Meals": 75.0,
    "Travel": 500.0,
    "Lodging": 250.0,
    "Supplies": 100.0,
}

# Each claim batch is one case: (claim id, employee, category, amount). Each
# batch mixes within-policy and over-limit claims, so a review always has both
# approvals and flags to record.
EXPENSE_BATCHES: dict[str, list[tuple[str, str, str, float]]] = {
    "2026-07": [
        ("C-101", "Ada", "Meals", 42.50),
        ("C-102", "Ada", "Travel", 640.00),
        ("C-103", "Lin", "Supplies", 18.20),
    ],
    "2026-08": [
        ("C-201", "Lin", "Lodging", 199.00),
        ("C-202", "Sam", "Meals", 96.00),
        ("C-203", "Sam", "Travel", 310.00),
        ("C-204", "Ada", "Supplies", 140.00),
    ],
    "2026-09": [
        ("C-301", "Sam", "Lodging", 275.00),
        ("C-302", "Lin", "Meals", 31.75),
    ],
}

EXPENSE_SHEETS: list[SheetSpec] = [
    SheetSpec(
        "Claims", codename="shClaims", headers=["ClaimID", "Employee", "Category", "Amount"]
    ),
    SheetSpec("Policy", codename="shPolicy", headers=["Category", "Limit"]),
    SheetSpec("Decisions", codename="shDecisions", headers=["ClaimID", "Decision", "Reason"]),
]

_MOD_REVIEW = '''\
Attribute VB_Name = "modReview"
Option Explicit

' Entry point: review every claim against policy and record decisions.
Public Sub ReviewClaims()
    CheckPolicy
    ApproveClaims
    FlagForReview
End Sub

' Load the claim rows. Reads Claims (never writes).
Function LoadClaims() As Variant
    LoadClaims = shClaims.Range("A2:D200").Value
End Function

' Compare each claim with its category limit.
Sub CheckPolicy()
    Dim limit As Double
    limit = PolicyLimit()
End Sub

' Read the limits table. Reads Policy (never writes).
Function PolicyLimit() As Double
    PolicyLimit = shPolicy.Range("B2").Value
End Function

' Stamp within-policy claims as approved (a write to Decisions).
Sub ApproveClaims()
    shDecisions.Range("B2").Value = "approved"
End Sub

' Stamp over-limit claims for human review (a write to Decisions).
Sub FlagForReview()
    shDecisions.Range("B3").Value = "flagged"
End Sub
'''


def build_expense_workbook(
    dest: str | Path, claims: list[tuple[str, str, str, float]] | None = None
) -> Path:
    """Write an expense-claim review workbook to ``dest`` and return its path.

    Args:
        dest: Where to write the ``.xlsm``.
        claims: The claim rows for this batch, as ``(id, employee, category,
            amount)``. Defaults to the first batch in :data:`EXPENSE_BATCHES`.
    """
    rows = claims if claims is not None else next(iter(EXPENSE_BATCHES.values()))
    sheets = [
        SheetSpec(
            spec.name,
            codename=spec.codename,
            headers=spec.headers,
            rows={
                "Claims": [list(row) for row in rows],
                "Policy": [[category, limit] for category, limit in EXPENSE_POLICY.items()],
            }.get(spec.name, []),
        )
        for spec in EXPENSE_SHEETS
    ]
    return build_macro_workbook(dest, sheets=sheets, modules={"modReview": _MOD_REVIEW})
