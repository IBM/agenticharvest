<div align="center">

# AgenticHarvest 🌾

### Process discovery for the AI agent era

**Turn structured artifacts into agent-callable tools, observe every call, and harvest the trace into a process an agent can follow and you can verify.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-e92063.svg)](https://docs.pydantic.dev/)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Typed: mypy strict](https://img.shields.io/badge/typed-mypy%20strict-2a6db2.svg)](https://mypy-lang.org/)

</div>

---

## Scope

AgenticHarvest helps you automate work that people already do in workbooks,
databases, and web apps. It watches how the work is done, mines that into a
process, and hands the process to an AI agent. Then it checks that the agent
followed the process.

> **A source becomes an IR. An IR exposes tools. Every tool call is an event.
> Events mine into a process. The process drives an agent.**

```
observe  ──▶  mine  ──▶  agentify  ──▶  observe ...
```

Three *domains* ship today:

- **Excel.** A macro-enabled workbook is parsed into an intermediate
  representation (IR): sheets and their data-flow roles, VBA macros, the call
  graph between them, the read/write flow between macros and sheets, and the
  cell values.
- **SQLite.** A database exposes its tables, keys, triggers, and the tables each
  trigger writes, plus a read-only query tool and a gated write tool.
- **Web apps.** A live web app's API becomes tools, built from the app's own
  description of itself: an OpenAPI 3 document, or Odoo's `/doc`
  self-description. Resources, operations, and the fields they read and write
  form the IR; every create, update, and delete is gated.

**Nothing above the domain layer knows what a spreadsheet, a table, or an API
is.** The tool contract, gateway, channels (REST, MCP, CLI), event log, miners,
plan compiler, and agent loop are shared, so adding a domain changes none of them.

A real process crosses systems, and the systems share no id. The
**correlator** relates every call, in every system, to the business object it
is about (the order, the person), so one log mines into one process. Then
**cross-system analysis** shows where the work hands off, waits, and gets
retyped by hand, and **redesign** turns that into a shorter plan for an agent.

---

## See it work: the whole loop

[`examples/expense_review`](examples/expense_review/) runs on a real approval
workflow, in which a reviewer checks expense claims against a policy sheet and
records decisions:

```text
1. OBSERVE   a reviewer works 3 claim batches through the tools   -> 36 events (OCEL 2.0)
2. MINE      bupaR (or pure Python) finds one process, 3/3 cases:
             read_range:Claims -> read_range:Policy -> get_data_flow -> write_cell:Decisions
3. COMPILE   the process becomes an agent plan; the write step is gated;
             the agent gets 3 of the 8 tools
4. AGENTIFY  a live LLM reviews a batch it has never seen
             decisions on disk vs. policy:   ok C-401 · ok C-402 · ok C-403
             agent trace vs. mined process:  conforms: True · fitness: 1.00
```

```bash
uv run python examples/expense_review/run.py --agent
```

[`examples/ticket_triage`](examples/ticket_triage/) runs the same four stages
on a SQLite support database, routing tickets by skill, load, and priority.
Only the domain import differs.

## See it work: one process across live systems

[`examples/order_fulfillment`](examples/order_fulfillment/) follows orders from
a database to a live shipping portal (Directus) and back:

```text
1. OBSERVE    a clerk fulfills 6 orders across two systems     -> one log, keyed by order
2. MINE       query:orders -> send_operation:createItemsShipments
              -> call_operation:readItemsShipments -> execute:orders            (6/6 orders)
3. ANALYZE    handoffs: database -> portal -> database, every order
              carried by hand: customer, order_id (db -> portal); shipment_ref (portal -> db)
4. REDESIGN   removed call_operation:readItemsShipments — it re-reads an id the booking returned
5. AGENTIFY   a live LLM runs the to-be plan on new orders
              ok O-2001 · ok O-2002 (portal and database)   conforms to the to-be plan: 1.00
```

[`examples/hire_to_onboard`](examples/hire_to_onboard/) mines a hiring process
across an HR system (Odoo), an identity server (Keycloak), and a task tracker
(Directus). It mines from tapped calls *and* from the three systems' own audit
trails, then checks the onboarding policy on every person:

```text
  3/4 pass  account only after the hire
  VIOLATION  alan.turing: account only after the hire — send_operation:post_users at 2026-10-05 17:00
             with no send_operation:hr.applicant.create_employee_from_applicant before it
```

Both run against real, unmodified products that
[`examples/systems`](examples/systems/) brings up with one compose file.

---

## Quick start

### Nothing installed? Run a cross-system example in containers

With only [Podman](https://podman.io/docs/installation) or Docker, plus git,
one command runs a whole example, with AgenticHarvest and every live system in
containers:

```bash
git clone https://github.com/aster-test-generation/agenticharvest-example-hire-to-onboard.git
cd agenticharvest-example-hire-to-onboard && ./run.sh          # Windows: .\run.ps1
```

[`examples/systems`](examples/systems/README.md) explains the commands, the
agent stage, and how to open the apps in a browser.

### For development

AgenticHarvest uses [`uv`](https://github.com/astral-sh/uv) and a `src/` layout. The
cross-system examples and their live stack are git submodules, so clone with
them:

```bash
git clone --recurse-submodules https://github.com/IBM/agenticharvest.git
# or, in an existing clone:
git submodule update --init

# The Excel and web domains, the REST and MCP channels, and the generic LLM backend
uv sync --extra excel --extra web --extra rest --extra mcp --extra openai_compatible --extra dev

# Optional: OpenTelemetry export for tracing (the OCEL log needs no extra)
uv sync --extra tracing
```

To try the web domain against real systems, bring up the bundled stack
(Directus, Keycloak, and Odoo) with
[`examples/systems`](examples/systems/README.md). It takes one compose file
and one setup script.

The SQLite domain and the pure-Python miner need no extra at all. bupaR mining
(which adds BPMN discovery) needs R, a *system* dependency. See
[Optional: the mining engine](#optional-the-mining-engine-r--bupar). Without R,
`best_miner()` falls back to the pure-Python miner.

### 1. Try the tools from the CLI

```bash
uv run agenticharvest sample expense claims.xlsm          # a real macro-enabled .xlsm, no Excel needed
uv run agenticharvest tools                               # every tool, its domain and effect
uv run agenticharvest call read_range path=claims.xlsm sheet=Policy
uv run agenticharvest call write_cell path=claims.xlsm sheet=Decisions cell=B2 value='"approved"'
#  -> error: "Tool 'write_cell' has a write effect and requires 'confirm: true' to run."
#     Add --confirm to apply it.
```

### 2. Serve them to an agent client

```bash
uv run agenticharvest serve rest          # http://127.0.0.1:8000  ·  GET /tools, POST /tools/{name}/call
uv run agenticharvest serve mcp           # MCP over stdio, for Claude Desktop, IDEs, and other MCP clients
```

To register AgenticHarvest with an MCP client, add it to the client's server config:

```json
{ "mcpServers": { "agenticharvest": {
    "command": "uv",
    "args": ["run", "--directory", "/path/to/agenticharvest", "--extra", "mcp", "--extra", "excel",
             "agenticharvest", "serve", "mcp"] } } }
```

Write tools carry `destructiveHint` and read tools `readOnlyHint`, so a client
can ask its user before a write.

### 3. Drive the tools with your own agent

Point AgenticHarvest at any OpenAI-compatible endpoint: OpenAI itself, a
[LiteLLM](https://github.com/BerriAI/litellm) proxy, vLLM, or an
Azure-compatible gateway. Copy `.env.example` to `.env` and fill it in.

```python
from agenticharvest.core.tools import default_registry
from agenticharvest.orchestration.agents.adapters import registry_tools
from agenticharvest.orchestration.agents.base import AgentConfig
from agenticharvest.orchestration.agents.factory import AgentFactory, Backend
from agenticharvest.utils.llm import LLMConfig

cfg = LLMConfig.from_env()                                  # endpoint from .env
agent = AgentFactory.create(
    Backend.OPENAI_COMPATIBLE,
    AgentConfig(model=cfg.model, system_prompt="Inspect the workbook with the tools."),
    tools=registry_tools(default_registry()),               # in-process, through dispatch
    llm_config=cfg,
)
result = await agent.run("In claims.xlsm, which claims exceed the policy limit?")
```

To talk to a running gateway instead, use
`rest_tools(httpx.Client(base_url="http://127.0.0.1:8000"))`.

### 4. Observe, mine, and compile a process

```python
from agenticharvest.core.events import OcelLog, OcelTap
from agenticharvest.core.tools import ToolRegistry
from agenticharvest.domains.excel.ocel import ExcelOCEL
from agenticharvest.mining import best_miner, collapse_repeats, check_trace
from agenticharvest.orchestration.agents.adapters import registry_tools
from agenticharvest.orchestration.compile import compile_plan, restrict_tools

log = OcelLog()
registry = ToolRegistry(taps=[OcelTap(mapper=ExcelOCEL(qualify_sheets=True), log=log)])
# ... register tools and run the workflow by hand a few times ...

model = best_miner().mine(collapse_repeats(log))        # bupaR if R is present, else Python
plan = compile_plan(model, registry.list_specs())       # dominant path; writes flagged
agent_prompt = plan.system_prompt("Review the claim batch.")
agent_tools = restrict_tools(registry_tools(registry), plan)   # least privilege
# ... run the agent, then: check_trace(model, agent_trace).conforms
```

[`examples/`](examples/) walks through this end to end.

### Optional: the mining engine (R + bupaR)

The default miner shells out to `Rscript` — there is **no Python dependency** to
install, but R and a few MIT-licensed [bupaverse](https://bupaverse.github.io/)
packages must be on the system. Skip this if you don't need BPMN discovery: the
bupaR miner reports itself unavailable until R is present, and `best_miner()`
uses the pure-Python miner instead.

**1. Install R.** macOS: download the official build from
[CRAN](https://cran.r-project.org/bin/macosx/) (or `brew install r`). Debian/
Ubuntu: `sudo apt-get install r-base`. Windows: the CRAN installer.

**2. Install the bupaR packages.** In an R session:

```r
# Core mining: activities, directly-follows, and trace variants.
install.packages(c("bupaR", "dplyr", "lubridate", "jsonlite"))

# BPMN discovery (optional). These live on GitHub, not CRAN:
install.packages("remotes")
remotes::install_github(c(
  "bupaverse/petrinetR", "bupaverse/bpmnR", "bupaverse/bupaRminer"
))
```

**3. Verify.**

```bash
uv run python -c "from agenticharvest.mining.miners.bupar import miner; print(miner.available())"
# True  once Rscript + bupaR are installed
```

With bupaR core present the miner always returns activities, directly-follows,
and variants; with `bupaRminer` + `bpmnR` also installed it additionally
discovers a BPMN model.

> **macOS note.** If you use Homebrew's R, source packages may compile but fail
> to load with a `symbol not found in flat namespace` error — Homebrew's `clang`
> emits libc++ symbols the system runtime lacks. Pin Apple's compiler by creating
> `~/.R/Makevars` with `CC = /usr/bin/clang` and `CXX = /usr/bin/clang++` (plus
> the `CXX11`/`CXX14`/`CXX17`/`CXX20` variants), then reinstall. The official CRAN
> build of R does not have this issue.

### Using pm4py

[pm4py](https://github.com/process-intelligence-solutions/pm4py) is a popular
Python process-mining library. It is licensed under **AGPL-3.0**, so
AgenticHarvest does **not** depend on it, bundle it, or register a pm4py miner.
Nothing in this repository installs it.

If its license terms work for you, you can install it yourself and plug it in
through the same `Miner` protocol that every engine uses:

```bash
uv pip install pm4py pandas      # your choice; review the AGPL-3.0 terms first
```

```python
# my_miners/pm4py_miner.py: lives in your own code, not in AgenticHarvest
import pandas as pd
import pm4py

from agenticharvest.core.events.model import OcelLog
from agenticharvest.mining.log import flatten
from agenticharvest.mining.model import Bpmn, BpmnFlow, BpmnNode, ProcessModel
from agenticharvest.mining.variants import VariantMiner


class Pm4pyMiner:
    name = "pm4py"

    def available(self) -> bool:
        return True

    def mine(self, log: OcelLog) -> ProcessModel:
        # Activities, directly-follows, and variants come from the built-in miner;
        # pm4py adds a BPMN discovered with the inductive miner.
        model = VariantMiner().mine(log)
        rows = [e.model_dump() for e in flatten(log)]
        df = pm4py.format_dataframe(
            pd.DataFrame(rows), case_id="case_id",
            activity_key="activity", timestamp_key="timestamp",
        )
        bpmn = pm4py.discover_bpmn_inductive(df)
        model.miner = self.name
        model.bpmn = Bpmn(
            nodes=[BpmnNode(id=n.get_id(), name=n.get_name() or "", type=type(n).__name__)
                   for n in bpmn.get_nodes()],
            flows=[BpmnFlow(source=f.get_source().get_id(), target=f.get_target().get_id())
                   for f in bpmn.get_flows()],
        )
        return model


miner = Pm4pyMiner()
```

Use it directly (`Pm4pyMiner().mine(log)`), or register it from your own
package's `pyproject.toml` so AgenticHarvest discovers it like any other miner:

```toml
[project.entry-points."agenticharvest.miners"]
pm4py = "my_miners.pm4py_miner:miner"
```

You are responsible for complying with pm4py's license when you install,
use, or distribute it alongside AgenticHarvest.

---

## The tools

Each tool is a declarative `ToolSpec` (name, one-line description, JSON Schema
input, read or write effect, domain) paired with a Python handler. A REST
client, an MCP client, and an LLM's tool declaration all see this same
contract. No tool calls an LLM. Each handler validates its input against the
domain's IR and performs one operation on the real artifact.

18 tools ship: 8 for Excel, 5 for SQLite, 5 for web apps. List them with
`uv run agenticharvest tools`.

### Excel: six read, two write

| Tool | What it does | Effect |
|------|--------------|:------:|
| `list_sheets` | Each sheet's code name, inferred data-flow role (input/output), and header row | read |
| `describe_sheet` | One sheet in detail, plus the macros that read from and write to it | read |
| `list_macros` | Every macro, its module, and whether it is an event handler | read |
| `get_call_graph` | The macros a given macro calls, and the macros that call it (transitive) | read |
| `get_data_flow` | The sheets a given macro reads from and writes to | read |
| `read_range` | The cell values in a range (or the whole used range), row by row | read |
| `write_cell` | Sets one cell's value, returning its before/after | **write** |
| `run_macro` | Runs a VBA macro and returns its result | **write** |

`write_cell` uses the file-level **openpyxl** backend, works anywhere, and
keeps the VBA project intact across the edit. `run_macro` needs a live engine
(a real Excel driven through **xlwings**). On a machine without one, it reports
itself unavailable rather than pretending to run, just as the miner does
without R.

> **Running macros in a live Excel needs a workbook Excel authored.** The
> sample workbooks are synthesized without Excel. Their VBA project is complete
> enough for AgenticHarvest to read, but Excel itself declines to load it: it offers
> to repair the file and drops the macros. To run a macro, write the workbook
> as an `.xlsx` plus `.bas` modules with `agenticharvest.domains.excel.kit.write_kit`.
> Then import the modules in Excel's VBA editor and save as `.xlsm` (about a
> minute). Excel's macro security prompts must also be answered, or the
> workbook placed in a trusted location, because a hidden Excel waits on any
> dialog.

### SQLite: four read, one write (standard library only)

| Tool | What it does | Effect |
|------|--------------|:------:|
| `list_tables` | Tables and views with their row counts | read |
| `describe_table` | Columns, primary and foreign keys, and the triggers that write the table | read |
| `list_triggers` | Every trigger: its table, its event, and the tables it writes | read |
| `query` | One `SELECT`, on a connection SQLite itself holds read-only | read |
| `execute` | One `INSERT`/`UPDATE`/`DELETE`, in a transaction with foreign keys on; never DDL | **write** |

### Web apps: five tools over any described API

| Tool | What it does | Effect |
|------|--------------|:------:|
| `list_resources` | The app's resources and the operations on each, marked read or write | read |
| `describe_operation` | One operation: method, path, parameters, and body fields | read |
| `get_field_flow` | The operations that produce a field's values and those that take it | read |
| `call_operation` | Call a read operation; refuses an operation that writes | read |
| `send_operation` | Call a create, update, or delete operation | **write** |

Every tool takes `app`, the name of a web app configured in the environment.
`setup.py` in [`examples/systems`](examples/systems/) writes these for the
bundled stack; for your own app:

```bash
AGENTICHARVEST_WEB_APPS=hris
AGENTICHARVEST_WEB_HRIS_URL=https://hr.example.com      # every request stays on this origin
AGENTICHARVEST_WEB_HRIS_SPEC=/openapi.json              # a path on the app, a URL, or a file
AGENTICHARVEST_WEB_HRIS_SPEC_KIND=openapi               # openapi | odoo
AGENTICHARVEST_WEB_HRIS_AUTH=bearer                     # bearer | header | oauth2-password | odoo | none
AGENTICHARVEST_WEB_HRIS_TOKEN=…
AGENTICHARVEST_WEB_HRIS_PREFIX=/api/v2/                 # optional: only expose operations under it
```

`oauth2-password` also takes `TOKEN_URL`, `CLIENT_ID`, `USERNAME`, and
`PASSWORD`, and refreshes the token on expiry. `odoo` takes `TOKEN` (an
Odoo API key), `DATABASE`, `USERNAME` and `PASSWORD` (to read `/doc`), and
`MODELS`. The full list is in
[`domains/web/config.py`](src/agenticharvest/domains/web/config.py).

The client guards what an agent can do with a live system:

- **Origin.** A request whose URL leaves the app's base origin is refused
  before it is sent.
- **Path parameters.** Each value is quoted into one segment, so a parameter
  cannot reach another endpoint.
- **Bodies.** A body missing a required field, or carrying an unknown one
  where the API's schema is closed, is refused with the fields the operation
  takes. Some APIs answer a malformed write with success and change nothing.
- **Credentials.** Tokens are attached at send time. They never appear in a
  result, an event, or a span, and credential response headers are dropped.
- **Size.** Responses are capped at 20,000 characters for agents. Code reading
  bulk history can lift the cap per request.

> **Every write goes through the dry-run gate.** A tool marked `Effect.WRITE` is
> blocked at the gateway until the call carries `confirm=true`, on every channel:
> REST, MCP, the CLI, and in-process. A caller cannot change an artifact by
> accident.

---

## Architecture

Every tool call, from a person, a script, or an agent, takes the same path.
Every call is recorded on the way.

```
   a person, a script, or an LLM agent
                  │ tool call: name + parameters
                  ▼
   CHANNELS       in-process · REST · MCP · CLI
                  │
   GATEWAY        dispatch(): unknown tool? → error · write without confirm=true? → blocked
                  │
   TOOL LAYER     ToolRegistry: spec + handler per tool ─────────── taps ─────┐
                  │                                                           │
   DOMAINS        excel   workbook → IR (sheets, macros, data flow)           │
   (the only      sqlite  database → IR (tables, keys, triggers)              │
    layer that    web     a live app's own API description → IR               │
    knows the             (OpenAPI 3, or Odoo /doc)                           │
    artifact)     │                                                           │
                  ▼                                                           │
                  the real file, database, or live web app                    │
                  │  their own audit trails ── import_records ───────────────┐│
                                                                             ▼▼
   EVENT LOG      OCEL 2.0 · each event related to its system and to the business
                  object it is about (CompositeMapper + Correlator: order, person)
                  │
   MINING         miner (bupaR or pure Python) → ProcessModel: variants, directly-follows, BPMN
                  analysis: handoffs · waits · carried values · object-centric DFG · policy rules
                  │
   ORCHESTRATION  compile_plan → prompt + only the tools it uses → redesign (to-be plan)
                  agent loop (any LLM) ──▶ its calls re-enter at CHANNELS, and are recorded too
                  check_trace: the agent's own log, replayed against the model
```

**Tools are code, not LLM calls.** A tool is a typed function: a schema plus a
handler that validates its input and makes one request, reads one range, or
runs one SQL statement. Each IR is built by parsing the artifact or its own API
description. Correlation, mining, analysis, policy checks, plan compilation,
and conformance are all deterministic. An LLM appears in exactly one place:
the agent loop, where it chooses which tool to call next. Only
`orchestration/agents/` and `utils/llm/` contain LLM code. That's why every
example's first stages run, and are tested, without an LLM.

**Only `domains/` knows what an artifact is.** Everything above and below it is
generic, so adding a domain changes nothing else.

- **[How it works: one hire, end to end](docs/how-it-works.md)** follows one
  real call through every layer: the tool request, the HTTP traffic, the event,
  the process model, the plan, the agent loop, and the checks.
- **[Architecture](docs/architecture.md)** describes each layer, the request
  lifecycle, and the three plugin seams.

### What ships today

| Layer | Module | What it does | Uses an LLM? |
|---|---|---|:---:|
| Channels | `gateway.channels` | REST (`GET /tools`, `POST /tools/{name}/call`), MCP over stdio, the `agenticharvest` CLI, in-process adapters | no |
| Gateway | `gateway.dispatch`, `gateway.policy` | One `dispatch()` path; the dry-run gate | no |
| Tools | `core.tools` | `ToolSpec` / `ToolResult` / `ToolRegistry`, and the tap seam | no |
| Domains | `domains.excel` | Workbook IR by static analysis (openpyxl + oletools); 6 read and 2 write tools; openpyxl and xlwings backends; `kit` for Excel-authored workbooks | no |
| | `domains.sqlite` | Tables, keys, triggers; read-only `query`, gated `execute`; standard library only | no |
| | `domains.web` | IR from a live app's OpenAPI 3 or Odoo `/doc`; 4 read and 1 write tool; 4 auth schemes | no |
| Event log | `core.events` | `OcelTap` (one event per call, OCEL 2.0); `CompositeMapper` + `Correlator` (join systems on a business key); `suggest_keys`; `RecordMapping` + `import_records` (audit trails → events) | no |
| Tracing | `core.tracing` | `TracingTap` (one span per call); `Tracer`; in-memory or OpenTelemetry sinks | no |
| Mining | `mining` | `BupaRMiner` (R, adds BPMN) and `VariantMiner` (pure Python); `collapse_repeats`; `check_trace` | no |
| | `mining.analysis` | `handoffs`, `performance`, `retyped_values`, `object_centric_dfg`, `check_rules` | no |
| Orchestration | `orchestration.compile`, `.redesign` | `compile_plan` → prompt + `restrict_tools`; `redesign` → to-be plan; `plan_model` | no |
| | `orchestration.agents` | The agent loop: `OpenAICompatibleAgent` (raw `httpx`, no vendor SDK), plus Claude, LangChain, LangGraph, and Gemini backends | **yes** |

Domains, backends, and miners register through entry points.

### On the roadmap

- More domains: Jupyter notebooks, CI pipelines, PDFs, codebases.
- Web apps without an API: a Playwright backend that drives the UI.
- Headless and sandboxed macro execution (LibreOffice).
- Human-in-the-loop review of mined steps before automation.
- Branching plans compiled from the full BPMN rather than the dominant variant.
- A gRPC/WebSocket channel.
- Auth and rate limiting at the gateway.

---

## Extending AgenticHarvest

AgenticHarvest has three plugin points, all entry-point-discovered so a third-party
package needs no changes to core:

```toml
# A new kind of artifact — implement the Domain protocol (IR + tool specs)
[project.entry-points."agenticharvest.domains"]
mydomain = "my_package:domain"

# A new way to execute a domain's tools (live, headless, sandboxed, dry-run)
[project.entry-points."agenticharvest.backends"]
mybackend = "my_package:backend"

# A new mining engine (AgenticHarvest ships bupaR and a pure-Python miner;
# see "Using pm4py" above for bringing your own)
[project.entry-points."agenticharvest.miners"]
myminer = "my_package:miner"
```

A domain author provides **tool specs and handlers** (the stable contract), an
optional **OCEL mapper** (so calls relate to typed objects), and the
entry-point line. Nothing in `gateway`, `core`, `mining`, or `orchestration`
changes. [`src/agenticharvest/domains/sqlite/`](src/agenticharvest/domains/sqlite/) is the
shortest complete example, and [CONTRIBUTING.md](CONTRIBUTING.md) walks through
it.

---

## Testing

```bash
uv run pytest -q                   # live-system tests skip unless the stack is up
uv run ruff check . && uv run mypy src
```

Nothing is mocked. Tests marked `live` call the live Directus, Keycloak, and
Odoo servers from [`examples/systems`](examples/systems/), and skip cleanly when
those systems are not configured. Tests that need an optional extra skip
cleanly without it, so a core-only install still runs. A few tests are worth
knowing about:

- [`tests/mining/test_end_to_end.py`](tests/mining/test_end_to_end.py) runs real
  tool calls through the gateway into an OCEL log and mines them.
- [`tests/examples/test_workflows.py`](tests/examples/test_workflows.py) runs
  the single-system examples' observe → mine → compile stages and asserts the
  decisions on disk, the mined variant, and the compiled plan.
- [`tests/examples/test_cross_system.py`](tests/examples/test_cross_system.py)
  runs both cross-system examples against the live systems and asserts what
  their walkthroughs show, in every system.

Opt-in tests drive a **real** model and are deselected unless you ask for them:

```bash
uv run pytest -m real_llm -v -s    # needs a configured .env; skips cleanly otherwise
```

---

## Project layout

```
src/agenticharvest/
  core/          # domain-agnostic backbone: tool contract, registry, tap
    tools/       #   ToolSpec · ToolResult · ToolRegistry · Tap
    contracts/   #   the Domain protocol
    ir/          #   generic IR graph (nodes, edges) + source resolvers
    events/      #   OCEL event log + OcelTap (analytical view),
                 #   cross-system correlation, history import
    tracing/     #   Span · Tracer · TracingTap · sinks (operational view)
  domains/       # pluggable artifact kinds — the only layer that knows the type
    base/        #   SimpleDomain + entry-point loader
    excel/       #   workbook → IR, tools, backends, OCEL mapper, samples, synth
    sqlite/      #   database tools, OCEL mapper, samples (stdlib only)
    web/         #   API description → IR, live client, tools, OCEL mapper
  gateway/       # normalize any channel to one dispatch path; enforce policy
    dispatch/    #   ToolRequest → dispatch()
    policy/      #   the dry-run gate
    channels/    #   rest/ (FastAPI) · mcp/ (stdio) · cli/ (`agenticharvest`)
  mining/        # event log → process model (pluggable engine)
    miners/      #   bupaR bridge (Rscript) + entry-point discovery
    variants/    #   pure-Python miner
    model/       #   the engine-neutral ProcessModel
    conformance  #   replay a trace against a model
    analysis     #   handoffs · waits · object-centric DFG · carries · policy rules
  orchestration/ # agents, and compiling a mined process into a plan
    agents/      #   backends · adapters (registry/REST → agent Tools)
    compile      #   ProcessModel → ProcessPlan → system prompt + toolset
    redesign     #   as-is plan → to-be plan, with reasons
  utils/llm/     # generic, provider-agnostic endpoint config (LLMConfig)
examples/        # expense_review (Excel) · ticket_triage (SQLite) · excel basics
                 # order_fulfillment · hire_to_onboard (live, cross-system)
                 # systems/ (the live stack: compose file + setup)
```

---

## Contributing

Questions and bug reports are welcome as
[GitHub issues](https://github.com/IBM/agenticharvest/issues/new). Pull requests
are very welcome too. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the checks
CI runs, how to add a domain or miner, and the Developer's Certificate of Origin
(DCO) sign-off every commit needs (`git commit -s`). The project's maintainers
are listed in [MAINTAINERS.md](MAINTAINERS.md), and everyone taking part is
expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Notable changes are
recorded in [CHANGELOG.md](CHANGELOG.md).

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). That file also lists
what AgenticHarvest does and does not guard. In particular, the REST and MCP
channels have no authentication, so bind them to localhost or put your own auth
in front.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).

All source files carry a copyright and SPDX license header:

```text
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
```

Optional third-party engines keep their own licenses. bupaR and the bupaverse
packages are MIT and are not redistributed here. pm4py is AGPL-3.0 and is not a
dependency (see [Using pm4py](#using-pm4py)).
