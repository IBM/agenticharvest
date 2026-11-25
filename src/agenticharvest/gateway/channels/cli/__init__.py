#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""CLI channel — the ``agenticharvest`` command-line entry point.

The ``agenticharvest`` console script (see pyproject) resolves to ``main`` here. Like
every channel it is a thin normalizer: ``agenticharvest call`` builds a ``ToolRequest``
and hands it to ``dispatch``, so the dry-run gate applies (pass ``--confirm`` for
a write) and the result is the same envelope REST and MCP return.

    agenticharvest tools                         list every tool, its domain and effect
    agenticharvest call TOOL [k=v ...]           call one tool; values parse as JSON
    agenticharvest serve rest [--host --port]    serve the REST channel (needs 'rest')
    agenticharvest serve mcp                     serve MCP over stdio (needs 'mcp')
    agenticharvest sample excel|expense|sqlite PATH
                                         write a sample artifact to try tools on
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from agenticharvest.core.tools.registry import default_registry
from agenticharvest.gateway.dispatch import ToolRequest, dispatch
from agenticharvest.gateway.policy import CONFIRM_PARAM


def _parse_value(raw: str) -> Any:
    """Parse ``raw`` as JSON when it is valid JSON, else keep it as a string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_params(pairs: Sequence[str]) -> dict[str, Any]:
    """Turn ``key=value`` arguments into a parameter dict."""
    params: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"error: expected key=value, got {pair!r}")
        params[key] = _parse_value(value)
    return params


def _cmd_tools(_args: argparse.Namespace) -> int:
    specs = default_registry().list_specs()
    width = max((len(s.name) for s in specs), default=0)
    for spec in specs:
        print(f"{spec.name:<{width}}  {spec.domain:<7} {spec.effect.value:<5}  {spec.description}")
    return 0


def _cmd_call(args: argparse.Namespace) -> int:
    params = _parse_params(args.params)
    if args.confirm:
        params[CONFIRM_PARAM] = True
    result = dispatch(default_registry(), ToolRequest(tool=args.tool, params=params))
    print(json.dumps(result.model_dump(), indent=2, default=str))
    return 0 if result.ok else 1


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.channel == "rest":
        try:
            import uvicorn

            from agenticharvest.gateway.channels.rest import create_app
        except ImportError:
            raise SystemExit("error: the REST channel needs the 'rest' extra") from None
        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    import anyio

    try:
        from agenticharvest.gateway.channels.mcp import serve_stdio
    except ImportError as exc:
        raise SystemExit(f"error: {exc}") from None
    anyio.run(serve_stdio)
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    if args.kind == "sqlite":
        from agenticharvest.domains.sqlite.samples import build_support_db

        path = build_support_db(args.path)
    else:
        try:
            from agenticharvest.domains.excel import samples
        except ImportError:
            raise SystemExit("error: Excel samples need the 'excel' extra") from None
        build = samples.build_orders_workbook if args.kind == "excel" else (
            samples.build_expense_workbook
        )
        path = build(args.path)
    print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the ``agenticharvest`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="agenticharvest", description="Typed, observable tools over structured artifacts."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    tools = commands.add_parser("tools", help="list every registered tool")
    tools.set_defaults(func=_cmd_tools)

    call = commands.add_parser("call", help="call one tool through the gateway")
    call.add_argument("tool", help="the tool name, e.g. list_sheets")
    call.add_argument("params", nargs="*", metavar="key=value",
                      help="parameters; each value parses as JSON when it can")
    call.add_argument("--confirm", action="store_true",
                      help="confirm a write-effect tool (the dry-run gate)")
    call.set_defaults(func=_cmd_call)

    serve = commands.add_parser("serve", help="serve the tools over a channel")
    serve.add_argument("channel", choices=["rest", "mcp"])
    serve.add_argument("--host", default="127.0.0.1", help="REST bind host")
    serve.add_argument("--port", type=int, default=8000, help="REST bind port")
    serve.set_defaults(func=_cmd_serve)

    sample = commands.add_parser("sample", help="write a sample artifact to try the tools on")
    sample.add_argument("kind", choices=["excel", "expense", "sqlite"])
    sample.add_argument("path", help="where to write it, e.g. orders.xlsm or support.db")
    sample.set_defaults(func=_cmd_sample)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
