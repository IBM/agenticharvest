#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Excel execution backends — pluggable.

The same tool spec runs against different engines depending on need:
    xlwings     live Excel (real app, real recalc, macros)
    openpyxl    file-level read/write without Excel installed

Backends subclass ``ExcelBackend`` (``base``), declare the ``Capability`` set they
support, and register under the ``agenticharvest.backends`` entry point; ``registry``
picks one per call. A headless (LibreOffice) or sandboxed engine plugs in the
same way.
"""
