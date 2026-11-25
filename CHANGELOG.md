# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] — Unreleased

The first public release.

### Added

- **The full observe → mine → agentify loop.**
  - `agenticharvest.orchestration.compile` turns a mined `ProcessModel` into a
    `ProcessPlan`: the dominant path, its coverage, and the write steps that
    need approval. The plan renders as an agent system prompt and narrows the
    agent's tools to the ones the process used.
  - `agenticharvest.mining.conformance.check_trace` replays a new trace against
    the model and reports fitness and deviations.
- **An Excel domain** (`agenticharvest.domains.excel`): the workbook IR built by
  static analysis, six read tools, and the gated `write_cell` and `run_macro`
  write tools, on the openpyxl and xlwings backends.
- **A SQLite domain** (`agenticharvest.domains.sqlite`, standard library only):
  `list_tables`, `describe_table`, `list_triggers`, a read-only `query`, and a
  gated `execute`.
- **A web domain** (`agenticharvest.domains.web`, the `web` extra). It reads a live
  app's IR from its OpenAPI 3 document or from Odoo's `/doc` self-description,
  and offers four read tools (`list_resources`, `describe_operation`,
  `get_field_flow`, `call_operation`) and one gated write (`send_operation`).
  The live client supports bearer, header, OAuth2-password, and Odoo API-key
  auth. It confines requests to the app's origin, keeps credentials out of
  every result, and validates bodies against the description before sending.
  Apps are configured with `AGENTICHARVEST_WEB_*` environment variables.
- **Cross-system correlation** (`agenticharvest.core.events.correlate`).
  `CompositeMapper` routes each call to its domain's mapper. `Correlator`
  relates events to the business objects `KeyRule`s find and records values for
  carry analysis. `suggest_keys` proposes join keys from recorded calls.
- **History import** (`agenticharvest.core.events.importers`). `RecordMapping` and
  `import_records` turn audit trails (API JSON, CSV, JSON Lines) into events.
- **Cross-system analysis** (`agenticharvest.mining.analysis`): `handoffs`,
  `performance`, `object_centric_dfg`, `retyped_values`, and `check_rules`.
- **Redesign** (`agenticharvest.orchestration.redesign`). `redesign` derives a
  to-be plan with reasons, and `plan_model` makes a plan checkable by
  `check_trace`.
- **An Excel authoring kit** (`agenticharvest.domains.excel.kit`). It writes an
  `.xlsx` plus `.bas` modules to assemble in Excel when a macro must run in a
  live Excel.
- **Two miners behind one `Miner` protocol:** a bupaR bridge
  (`agenticharvest.mining.miners.bupar`) and a pure-Python miner
  (`agenticharvest.mining.variants`) that computes activities, the
  directly-follows relation, and variants without needing R. `best_miner()`
  picks bupaR when it is installed and the pure-Python miner otherwise.
  `collapse_repeats()` folds per-item repeats, so cases of different sizes
  share one variant.
- **An MCP channel** (`agenticharvest serve mcp`). Every tool is served to MCP
  clients, with read-only and destructive hints and the same dry-run gate as
  REST.
- **An `agenticharvest` CLI** with `tools`, `call`, `serve rest|mcp`, and
  `sample`.
- **Agent adapters** (`agenticharvest.orchestration.agents.adapters`):
  `registry_tools` runs tools in-process through `dispatch`, and `rest_tools`
  runs them over HTTP. Agent backends for any OpenAI-compatible endpoint, plus
  Claude, LangChain, LangGraph, and Gemini.
- **Shipped sample artifacts** (`agenticharvest.domains.excel.samples`,
  `agenticharvest.domains.sqlite.samples`) and the workbook synthesizer
  (`agenticharvest.domains.excel.synth`).
- **Examples:** `expense_review/` (Excel) and `ticket_triage/` (SQLite) run the
  whole loop, with an optional live-LLM stage.
- **Live examples:** `order_fulfillment/` (SQLite + Directus) and
  `hire_to_onboard/` (Odoo + Keycloak + Directus), with `examples/systems/`
  (a compose file and a setup script) bringing up the live stack. They are git
  submodules under github.com/aster-test-generation. Tests marked `live` run
  against the stack and skip when it is absent.
- Execution backends declare the `domain` they serve, and Excel's
  `select_backend` keeps only Excel backends, so other domains can register
  backends under the shared `agenticharvest.backends` group.
- LICENSE, NOTICE, CONTRIBUTING (with DCO sign-off), CODE_OF_CONDUCT,
  MAINTAINERS, SECURITY, `.env.example`, and CI covering lint, types, a test
  matrix, the live systems, a core-only install, R mining, and a wheel smoke
  test.
- pm4py is not a dependency or an extra: its AGPL-3.0 license is incompatible
  with this Apache-2.0 project. The README section "Using pm4py" shows how to
  install it yourself and register it as a miner.

[0.1.0]: https://github.com/IBM/agenticharvest/releases/tag/v0.1.0
