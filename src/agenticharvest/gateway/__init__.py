#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The Polyglot adapter — one gateway behind every channel.

Any channel (REST, MCP, CLI, SDK, gRPC) normalizes its request into a single
ToolCall and hands it to one dispatch path. The gateway is protocol-agnostic:
it applies policy (auth, rate-limit, dry-run gate), dispatches to the tool
layer, and lets the tap observe the call. Adding a channel never touches the
tool or domain layers.

Subpackages:
    channels    per-protocol request normalizers (rest, mcp, cli, sdk, grpc)
    dispatch    the single normalize -> resolve -> execute path
    policy      auth, rate limiting, and the dry-run gate
"""
