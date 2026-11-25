#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Domains — the dimensions of use case.

A domain is a self-contained bundle: an IR builder (source -> IR), a set of tool
specs, and one or more execution backends. Excel is the first domain; the
architecture is deliberately not spreadsheet-specific, so PDFs, databases, web
apps, and codebases can each become a domain without touching core.

Domains are discovered two ways:
    - built-ins imported from this package (e.g. ``agenticharvest.domains.excel``)
    - third-party packages registered under the ``agenticharvest.domains`` entry point

Subpackages:
    base    the Domain scaffolding + registry that both discovery paths use
    excel   the reference domain: Excel -> Workbook IR
"""
