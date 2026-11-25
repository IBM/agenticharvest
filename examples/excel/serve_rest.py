#!/usr/bin/env python
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
"""Serve the Excel domain's tools over the REST channel.

Run it::

    python examples/excel/serve_rest.py [HOST] [PORT]

It boots the FastAPI app from :func:`agenticharvest.gateway.channels.rest.create_app`
with a default registry: every installed domain's tools (Excel, SQLite, and,
with the ``web`` extra, the web tools), discovered through the
``agenticharvest.domains`` entry point. Write tools are served too, and the gateway
refuses them unless the call carries ``"confirm": true``. Once it is up,
explore the surface::

    curl localhost:8000/tools
    curl -X POST localhost:8000/tools/list_sheets/call \\
        -H 'content-type: application/json' \\
        -d '{"path": "orders.xlsm"}'

The ``path`` a tool call names is resolved on the server, so build a workbook
first (see ``build_orders_workbook.py``) and pass a path this process can read.
Requires the ``rest`` extra (``uv sync --extra rest``).
"""

from __future__ import annotations

import sys

import uvicorn

from agenticharvest.gateway.channels.rest import create_app


def main(argv: list[str]) -> int:
    host = argv[1] if len(argv) > 1 else "127.0.0.1"
    port = int(argv[2]) if len(argv) > 2 else 8000
    uvicorn.run(create_app(), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
