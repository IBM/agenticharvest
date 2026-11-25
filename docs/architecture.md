# AgenticHarvest architecture

This page describes each layer in general. For the same layers followed
through one real call, with the actual request, HTTP traffic, event, and
model, see [How it works: one hire, end to end](how-it-works.md).

## The loop AgenticHarvest closes

```
observe  ──▶  mine  ──▶  agentify  ──▶  observe ...
```

Every tool call is observed (tapped into an OCEL event and a span), the log
is mined into a process model, and the model drives an agent whose calls are, in
turn, observed. The codebase is organized around this loop, and around one
invariant that makes it extensible: **only `domains/` knows what the artifact
is.** Everything above and below it is generic.

## Layers

| Layer | Package | Responsibility | Knows the artifact? | Shipped? |
|-------|---------|----------------|:-------------------:|:--------:|
| Channels | `gateway.channels` | Accept a native protocol, normalize to a `ToolRequest` | no | REST ✅ · MCP ✅ · CLI ✅ |
| Gateway | `gateway.dispatch`, `gateway.policy` | One dispatch path; policy (dry-run gate today; auth, rate-limit to come) | no | ✅ |
| Tool layer | `core.tools` | Stable contract (`ToolSpec`/`ToolResult`) + registry + the tap point | no | ✅ |
| Contracts | `core.contracts` | The `Domain` protocol every domain satisfies | no | ✅ |
| Observability | `core.events`, `core.tracing` | One call → one OCEL event (+ one span); cross-system correlation; history import | no | events ✅ · tracing ✅ · correlate ✅ · import ✅ |
| IR | `core.ir` | Graph + data-flow abstractions a domain can reuse (the Excel IR does) | no | ✅ |
| **Domains** | `domains.*` | source → IR, tool specs, execution backends, OCEL mapper | **only here** | Excel ✅ · SQLite ✅ · Web ✅ |
| Mining | `mining` | Event log → process model (pluggable miner); conformance; cross-system analysis | no | bupaR ✅ · variants ✅ · analysis ✅ |
| Orchestration | `orchestration` | Tools + LLM → agent loop; process model → plan (`compile`) → to-be plan (`redesign`) | no | ✅ |
| LLM config | `utils.llm` | Provider-agnostic endpoint settings | no | ✅ |

## The request lifecycle

A single call travels one path, whatever channel it entered through:

```
client → channel → ToolRequest → dispatch(registry, request)
                                     │
                                     ├─ registry.get(name)      unknown → ToolResult(ok=False)
                                     ├─ policy.gate(spec, params)  write without confirm → blocked
                                     └─ registry.call(name, params)
                                            │  handler runs; exceptions become ToolResult(ok=False)
                                            └─ every registered Tap.record(tool, params, result)
                                     ▼
                                 ToolResult → channel → client
```

- A **tool error is a normal result**, not a transport failure: the REST channel
  returns HTTP 200 with `{"ok": false, "error": ...}`. Only an *unknown tool* is
  a 404.
- The **tap** is the observability seam. `InMemoryTap` lets a test assert a tool
  actually ran; `OcelTap` (`core.events`) turns each call into an OCEL event and
  `TracingTap` (`core.tracing`) turns the same call into a span — one call, two
  consumers: dashboards watch spans, mining consumes the event log.

## The tool contract

`ToolSpec` is declarative and serializable — the one shape a REST client, an MCP
server, or an LLM tool declaration all consume:

```python
class ToolSpec(BaseModel):
    name: str
    description: str          # one plain capability sentence — no internals
    input_schema: dict        # JSON Schema for the parameters
    effect: Effect = Effect.READ   # Effect is a str enum: READ | WRITE
    domain: str = ""          # stamped by the domain when left blank
```

`ToolResult` is the uniform envelope: `ok`, `output`, `error`. A domain assembles
its `(spec, handler)` pairs into a `SimpleDomain`, which satisfies the
`core.contracts.Domain` protocol (`name`, `tool_specs()`, `handlers()`) and
stamps its name onto each spec.

## The dry-run gate

The policy layer is small but real. Any `ToolSpec` with `effect == "write"` must
carry `params["confirm"] is True`, or `dispatch` returns a failed result and the
handler never runs. Read tools pass straight through. The gate guards every
write tool: `write_cell` and `run_macro` (Excel), `execute` (SQLite), and
`send_operation` (web), so none can change an artifact until the call is
explicitly confirmed.

## The event log (OCEL)

`core.events` is the first consumer of the tap seam. `OcelTap` implements the
`Tap` protocol, so attaching one to a registry turns every tool call into an
event in an **Object-Centric Event Log** — live, at dispatch time. Object-centric
means one event can relate to many typed objects at once (a workbook, the sheets
it touched, the macros it inspected), each via a qualified relationship, rather
than being forced onto a single flat case id. That is the shape `mining` needs to
reason about how an artifact is actually used.

The split mirrors the domain invariant:

- **`core.events`** owns the generic model — `Event`, `Object`, their qualified
  `Relationship`s, and an `OcelLog` that serializes to the OCEL 2.0 JSON document
  (`objectTypes` / `eventTypes` / `events` / `objects`). It knows nothing about
  spreadsheets.
- An **`OcelMapper`** decides *which* typed objects a call touched. The generic
  default (`GenericOcelMapper`) attaches the artifact named by the call's `path`;
  a domain extends it. `domains.excel.ocel.ExcelOCEL` is the reference: it is the
  only place that knows an Excel call touches a `workbook` made of `sheet`s and
  `macro`s, and it maps, say, a `get_data_flow` event to the sheets it `reads`
  and `writes`. It maps the two write tools the same way — a `write_cell` event
  `writes` a sheet, a `run_macro` event `runs` a macro — so they land on the same
  `workbook` object as the reads rather than falling through to a generic
  `artifact`; that is what keeps the mutation visible in a workbook-keyed process
  model, so the mined process reads as *inspect, then write*. A new domain ships
  its own mapper the same way — nothing in `core.events` changes.

The tap never raises: observation must not break the call it observes.

## Processes that cross systems

A domain mapper relates a call to its own artifact. A business process crosses
artifacts that share no id: an order is read from a database and booked on a
web portal, and a hire is recorded in an HR system, an identity server, and a
task tracker. Two generic pieces in `core.events.correlate` join them, and
neither knows what any system is:

```
tool call ──▶ CompositeMapper ──▶ the owning domain's mapper   (database, webapp, workbook …)
                   │
                   ▼
              Correlator  ──▶ + business objects its KeyRules find   (order:O-1001, person:ada@…)
                          ──▶ + values its ValueRules record          (for carry analysis)
```

- **`CompositeMapper`** routes each tool to the mapper of the domain that owns
  it (tool name → domain, read from the registry's specs), so one registry and
  one log span every system.
- **`Correlator`** wraps any mapper. Each `KeyRule` says where a business key
  appears: a parameter path, result paths, or a regular expression over either.
  Every value it finds becomes an object of the rule's type, related as
  `writes` for a confirmed call and `reads` otherwise. Artifact types listed in
  `inherit` lend the keys last written into them to calls that name none, so a
  macro run joins the traces of the orders pasted into its workbook.
- **`suggest_keys`** scans recorded calls for identifier-shaped values that
  appear in more than one system, for when nobody knows the join key yet.

Mining then takes the business type as its case (`traces(log, case_type="order")`),
and `mining.analysis` reads the same log for what discovery does not show:
handoffs between systems, the wait on each transition, values carried by hand,
an object-centric DFG, and ordering and timing rules.

The log does not have to come from tapping. `core.events.importers` turns a
system's own history (an audit table, an activity feed, admin events) into
events through a declarative `RecordMapping`. Imported events land on the same
model, so a process can be mined from systems nobody routes through AgenticHarvest.

## Tracing (spans)

`core.tracing` is the operational, real-time twin of the event log. The same
tapped call that becomes an OCEL event also becomes a **span** — one call, two
observers: mining consumes the analytical event log, dashboards watch the live
spans. The package is domain-agnostic and, like `core.events`, dependency-free at
its core; OpenTelemetry is optional.

Three pieces, mirroring the events split:

- **`Span`** is one operation over time — a `name` (the tool, like an event's
  activity), `start_time`/`end_time`, an `OK`/`ERROR` status, an error message,
  and free-form attributes. `duration_ms` is derived.
- **`TracingTap`** implements the `core.tools.Tap` protocol, so attaching it to a
  registry emits one span per tool call with no handler changes. Because the tap
  seam fires *once, after* a call completes, a tap-made span marks the completed
  call (`start_time == end_time`) and carries its status and parameters. When a
  caller owns the execution and wants a real duration, it brackets the work with
  **`Tracer.span(...)`** instead — a context manager that stamps entry and exit,
  marks `ERROR` (and re-raises) if the block throws, and yields the mutable span
  so the block can add attributes as it learns them.
- A **`SpanSink`** decides where finished spans land. `InMemorySink` (always
  available) keeps them in a list for tests and local inspection; `OtelSink`
  bridges each span into the OpenTelemetry API, so an OTLP exporter — a
  Collector, Jaeger, Langfuse — configured through the OTel SDK receives it.
  `OtelSink` imports `opentelemetry` lazily and raises a clear error if the
  optional `tracing` extra is not installed, so nothing here forces the
  dependency.

Like `OcelTap`, `TracingTap` never raises: a tap observes, it must not turn its
own failure into a failed tool call.

## Mining (bupaR)

`mining` consumes the event log and returns a `ProcessModel` — the activities and
their frequencies, the directly-follows relation, the trace variants, and (when a
discovery engine produces one) a BPMN model. The model is engine-neutral, so
anything downstream consumes it without knowing which engine ran.

The default engine is `BupaRMiner`, a bridge to the MIT-licensed R
[bupaverse](https://bupaverse.github.io/). It shells out to `Rscript` with a
bundled, self-contained script rather than embedding an R runtime:

```
OcelLog ──▶ flatten() ──▶ CSV ──▶ Rscript bupar.R ──▶ JSON ──▶ ProcessModel
         (object-centric →      (bupaR eventlog;
          case-centric)          discover() when bupaRminer present)
```

Two impedance mismatches are handled at the seam:

- **Object-centric → case-centric.** bupaR (like most engines) keys every event
  on a single case id, while an OCEL relates one event to many objects.
  `mining.log.flatten` projects the log onto one object *type* chosen as the case
  — `infer_case_type` defaults to the type the most events relate to (the
  `workbook` for an Excel session), and a caller can override it.
- **Graceful degradation.** With bupaR core present the miner always returns
  activities, directly-follows, and variants. When `bupaRminer` + `bpmnR` are
  also installed it additionally discovers a BPMN model. When `Rscript` is not on
  the `PATH`, `available()` returns `False` and `mine()` raises `MinerError` with
  install guidance — so nothing requires R to be installed, and tests skip
  cleanly on a machine without it.

Only bupaRminer's `discover()` path is used. Its optional conformance helpers
pull in pm4py, whose license AgenticHarvest avoids, so they are never invoked. The
miner registers under the `agenticharvest.miners` entry point and is constructed
lazily, so R is touched only when `mine()` actually runs.

`VariantMiner` (`mining.variants`) is the pure-Python fallback. It returns the
same activities, directly-follows relation, and variants without a BPMN, and
needs nothing beyond Python. `best_miner()` returns bupaR when it is usable
and this miner otherwise.

Work over a collection repeats one step per item: four claims produce four
decision writes. `collapse_repeats(log)` folds runs of the same activity on a
case before mining, so cases of any size share one variant.

## Closing the loop: compile and conformance

`orchestration.compile` turns a `ProcessModel` into what an agent runs with:

```
ProcessModel ──▶ compile_plan() ──▶ ProcessPlan ──▶ .system_prompt(goal)   → AgentConfig
                 (dominant variant;               ──▶ restrict_tools(...)   → least-privilege tools
                  write steps flagged)
```

The plan follows the **dominant variant**, the path most cases took, so it works
with any miner. An activity may be qualified by its domain mapper
(`read_range:Policy`, `query:tickets`). The tool to call is the part before the
colon, and the target goes into the prompt. A step whose tool has
`Effect.WRITE` is marked `requires_approval` and is rendered with the
`confirm=true` instruction.

The agent's own calls go through the same tapped registry, so they land in a
fresh log. `mining.conformance.check_trace(model, trace)` replays each trace
against the model's directly-follows relation. It reports unseen steps, unseen
transitions, and a fitness score, which tells you whether the agent did what
the observed process does and where it departed from it.

The as-is plan replays what people did, including steps that only exist
because two systems do not talk to each other. `orchestration.redesign` derives
a to-be plan by removing or replacing named steps, records the reason for each
change, and keeps every remaining write gated. A to-be plan was never observed,
so `plan_model(plan)` turns it into a model that `check_trace` can measure an
agent against.

## The generic LLM backend

`OpenAICompatibleAgent` talks the standard Chat Completions HTTP shape directly
via `httpx` — **no vendor SDK**. That makes "generic" structural rather than
aspirational: there is nothing vendor-specific to import, just the request/
response shape that OpenAI, LiteLLM proxies, vLLM, and Azure-compatible gateways
all speak. The concrete endpoint and model are supplied only at the call site,
through `utils.llm.LLMConfig` (`api_base` / `api_key` / `model`, loaded from the
environment or a `.env` file). The generic backend and `utils.llm` name no
model or vendor. Credential values are used only to connect and are never logged.

It sits alongside the existing `claude`, `langchain`, `langgraph`, and `gemini`
backends behind `AgentFactory.create(Backend.…, config, tools, llm_config=…)`.

## The three extension seams

AgenticHarvest has exactly three plugin points, all discovered via entry points so
third-party packages need no changes to core:

1. **Domains** (`agenticharvest.domains`) — a new *kind of artifact*. Implement the
   `Domain` protocol (tool specs + handlers, plus an optional IR builder and
   backends). Excel, SQLite, and web apps ship today. Notebooks, PDFs, and
   codebases are future domains.
2. **Backends** (`agenticharvest.backends`) — a new *way to execute* a domain's tools
   (live, file-level, headless, dry-run, sandboxed). Each backend declares a
   `Capability` set (`READ`/`WRITE`/`EXECUTE`) and the `domain` it serves, and
   implements only what it supports. The entry-point group is shared, so each
   domain's selector keeps only its own backends. A write tool asks
   `backends.registry.select_backend(path, cap)` for one that both advertises
   the capability and is available in this environment, so `run_macro` degrades
   gracefully when no live Excel is installed. openpyxl covers read + file-level
   write; xlwings adds live execution.
3. **Miners** (`agenticharvest.miners`) add a new *mining engine*. AgenticHarvest ships two:
   `bupaR` (MIT) and the pure-Python `variants`. The common `pm4py` engine is
   AGPL-3.0, so it is not a dependency at all. Users who accept its terms can
   install it themselves and register it, or any other engine, the same way.

## Going beyond Excel: a checklist

To add a domain, an author provides:

- **Tool specs and handlers.** These are typed, effect-annotated capabilities
  (the stable contract). Mark anything that changes the artifact
  `Effect.WRITE`.
- **An OCEL mapper** (recommended). Subclass `GenericOcelMapper` so calls
  relate to typed objects, and qualify activities with their target if you want
  business-level steps.
- Optionally, an **IR builder** (raw source → graph) and **backends**, when the
  artifact needs analysis or several execution engines.
- An entry-point registration under `agenticharvest.domains`.

Nothing in `gateway`, `core`, `mining`, or `orchestration` changes.
`domains/sqlite/` and `domains/web/` are the proof: each was added without
touching any of them.

## The web domain

`domains/web/` turns a live web app's API into tools without anything written
by hand for that app. Its IR (`WebIR`) is resources, operations, and the fields
operations read and write, built from one of two descriptions the app serves:

- **OpenAPI 3** (`from_openapi`). Each path + method is an operation. A
  resource is the literal path segment just before the first parameter, so
  `/items/orders/{id}` and its sub-paths belong to `orders`. Request-body
  schemas give the fields, following `$ref`s, `oneOf` arrays, and `allOf`.
- **Odoo's self-description** (`from_odoo`). Odoo 17+ serves
  `/doc/<model>.json`. ORM methods map to the HTTP verb matching their effect
  (`search_read` reads, `write` patches); argument-free `action_*` methods, and
  any business methods a deployment names, are exposed as gated writes.

`WebClient` is the only code that opens a connection. It authenticates
(`bearer`, `header`, `oauth2-password` with refresh, `odoo` API keys), refuses
any URL off the app's origin, attaches credentials at send time and strips
them from results, and caps response size. The tools validate a call against
the IR before it is sent: path parameters are quoted into one segment, and a
body is checked for required fields and, where the schema is closed, unknown
ones. `WebOCEL` relates each call to its `webapp`, `operation`, and `resource`,
and records the ids a call addressed or returned.
