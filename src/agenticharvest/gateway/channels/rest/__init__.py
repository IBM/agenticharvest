#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""REST channel — an HTTP surface (FastAPI) over the dispatch path.

Public API:
    create_app      build the FastAPI app over a tool registry
"""

from agenticharvest.gateway.channels.rest.app import create_app

__all__ = ["create_app"]
