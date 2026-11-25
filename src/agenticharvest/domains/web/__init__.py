#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The web domain — a live web app's API becomes agent-callable tools.

The third reference domain. A web app maps onto the same shapes the workbook and
the database do —

    resources     the containers of state   (a workbook's sheets, a database's tables)
    operations    what acts on them         (a workbook's macros, a database's triggers)

— and its IR is read from the app's own description of its API, so nothing about
any particular app is written by hand: an OpenAPI 3 document, or Odoo's
``/doc/<model>.json`` self-description. Every call goes to a live system; the
domain ships no recorded or simulated responses.

Apps are configured from the environment (see ``config``), so a URL or a
credential never appears in code. Each app's base URL is its allowlist.

Tools (see ``tools``):
    list_resources       resources and their operations, marked read or write
    describe_operation   one operation: method, path, parameters, body fields
    get_field_flow       the operations that produce and consume a field
    call_operation       call a read operation                       (read)
    send_operation       call a create / update / delete operation   (write, gated)

Modules:
    config   per-app connection settings, loaded from env / .env
    ir       the WebIR, built from OpenAPI 3 or Odoo's self-description
    client   the live httpx client: auth, origin allowlist, bounded responses
    tools    the specs and handlers
    ocel     the OCEL mapper (WebOCEL): webapp / resource / operation objects

Needs the ``web`` extra (``httpx``).
"""

from agenticharvest.domains.base import SimpleDomain
from agenticharvest.domains.web.ocel import WebOCEL
from agenticharvest.domains.web.tools import web_tools

# The entry-point target, mirroring ``agenticharvest.domains.sqlite:domain``.
domain = SimpleDomain(name="web", tools=web_tools())

__all__ = ["WebOCEL", "domain"]
