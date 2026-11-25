#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The protocols that define every extension seam in AgenticHarvest.

These are the interfaces a new domain implements to become first-class. Keeping
them in one place makes the "what do I build to go beyond Excel?" answer a
single file to read.

Public API:
    Domain      the tool-contributing source type (tool_specs + handlers)

Further protocols (IRBuilder, Backend) live with the layers they belong to:
    IRBuilder   each domain's ``build_ir`` (see ``domains.excel.ir``)
    Backend     execution engine (see ``domains.excel.backends.base``)
    Tool        the model-callable tool (see ``orchestration.agents.base``)
    Tap         call -> span + event (see ``core.tools.tap``)
"""

from agenticharvest.core.contracts.domain import Domain

__all__ = ["Domain"]
