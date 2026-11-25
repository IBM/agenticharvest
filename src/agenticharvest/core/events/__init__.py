#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The event log — the evidence.

Every tapped tool call becomes an event in an OCEL (Object-Centric Event Log).
This is the substrate ``agenticharvest.mining`` consumes. Keeping the log object-centric
(files, sheets, macros as first-class objects) is what lets mining relate
activity across a whole artifact rather than force it onto a single flat case id.

The model here is domain-agnostic: it stores events and the objects they touch
and serializes to the OCEL 2.0 JSON shape. *Which* typed objects a given tool
call touches is decided by a domain-supplied :class:`OcelMapper`
(``agenticharvest.domains.excel.ocel.ExcelOCEL`` is the reference), extending the
:class:`GenericOcelMapper` default.

Public API:
    Event, Object, Relationship, ObjectAttribute, OcelLog   the OCEL data model
    OcelMapper, GenericOcelMapper, EventMapping             the mapping seam
    OcelTap                                                 the Tap that records
    CompositeMapper, Correlator, KeyRule, ValueRule,
    suggest_keys                                            join systems on business objects
    RecordMapping, ObjectRef, import_records                import a system's own history

Modules:
    correlate   route calls to their domain's mapper; relate business keys
    importers   turn audit records (JSON, CSV, JSONL) into events
"""

from agenticharvest.core.events.correlate import (
    CompositeMapper,
    Correlator,
    KeyRule,
    KeySuggestion,
    ValueRule,
    suggest_keys,
)
from agenticharvest.core.events.importers import ObjectRef, RecordMapping, import_records
from agenticharvest.core.events.mapping import (
    EventMapping,
    GenericOcelMapper,
    OcelMapper,
)
from agenticharvest.core.events.model import (
    OBJ_ARTIFACT,
    Event,
    ObjectAttribute,
    OcelLog,
    Relationship,
)
from agenticharvest.core.events.model import (
    Object as Object,
)
from agenticharvest.core.events.tap import OcelTap

__all__ = [
    "OBJ_ARTIFACT",
    "CompositeMapper",
    "Correlator",
    "Event",
    "EventMapping",
    "GenericOcelMapper",
    "KeyRule",
    "KeySuggestion",
    "Object",
    "ObjectAttribute",
    "ObjectRef",
    "OcelLog",
    "OcelMapper",
    "OcelTap",
    "RecordMapping",
    "Relationship",
    "ValueRule",
    "import_records",
    "suggest_keys",
]
