# Examples

Each example runs from the repo root. None of them needs Excel, and only the
`--agent` stages need an LLM.

## One system

| Example | Domain | What it shows |
|---|---|---|
| [`expense_review/`](expense_review/) | Excel | **The whole loop on a real approval workflow.** A reviewer checks expense claims against policy. AgenticHarvest observes the work, mines the process, compiles it into an agent plan, then has a live agent review a new batch and checks it against the mined process. |
| [`ticket_triage/`](ticket_triage/) | SQLite | **The same loop on a different artifact.** Support tickets are routed by skill, load, and priority in a database with triggers. Only the domain import differs from `expense_review`. |
| [`excel/`](excel/) | Excel | The building blocks: synthesize a macro-enabled `.xlsm`, print its IR, and serve the tools over REST. |

```bash
uv sync --extra excel --extra openai_compatible

uv run python examples/expense_review/run.py           # observe → mine → compile
uv run python examples/expense_review/run.py --agent   # … → a live agent runs it

uv run python examples/ticket_triage/run.py --agent
```

## Across systems, on live web apps

A real process rarely stays inside one system. These two examples run against
live, unmodified open-source products. Set them up once with
[`systems/`](systems/). With only Podman or Docker installed, `run.sh` in any of
these folders runs everything in containers.

`order_fulfillment/`, `hire_to_onboard/`, and `systems/` are git submodules,
each its own repository (see
[CONTRIBUTING.md](../CONTRIBUTING.md#the-submodules)). Clone AgenticHarvest with `--recurse-submodules`, or run
`git submodule update --init`.

| Example | Systems | What it shows |
|---|---|---|
| [`order_fulfillment/`](order_fulfillment/) | SQLite + Directus | **Joining systems into one process, then redesigning it.** The correlator relates every call to the order it is about, wherever it ran. Analysis finds the handoffs and the values a clerk copies between systems. The redesign drops the step that only carries data. An agent runs the to-be plan and is checked in both systems. |
| [`hire_to_onboard/`](hire_to_onboard/) | Odoo + Keycloak + Directus | **Mining an HR estate.** The same hiring process is mined twice: from tapped calls, and from the three systems' own audit trails with no tapping. The onboarding policy is checked on every person, and the one violation shows up in both views. An agent onboards a new hire across all three systems. |
| [`systems/`](systems/) | — | A compose file and a setup script that bring up the live systems and write their settings to `.env`. |

Each cross-system example has a **`WALKTHROUGH.md`** that goes through every
stage: what runs, the full output line by line, the event logs and the miner's
output files, where they land, how to inspect the results in the live apps,
and how to change the example.
[order_fulfillment/WALKTHROUGH.md](order_fulfillment/WALKTHROUGH.md) ·
[hire_to_onboard/WALKTHROUGH.md](hire_to_onboard/WALKTHROUGH.md)

```bash
uv sync --extra web --extra openai_compatible
podman compose -f examples/systems/compose.yaml up -d
uv run python examples/systems/setup.py

uv run python examples/order_fulfillment/run.py --agent
uv run python examples/hire_to_onboard/run.py --agent
```

The `--agent` stage reads `API_BASE` / `API_KEY` / `MODEL` from `.env` (copy
`.env.example`). Any OpenAI-compatible endpoint works.

## Why these workflows

A workflow is worth agentifying when it is **repeated, rule-governed, and ends
in a write**:

- **Repeated.** Many cases (claim batches, ticket queues, orders, hires) follow
  the same steps, so there is a process to mine and a dominant path to compile.
- **Rule-governed.** Each decision follows from the data: approve within the
  limit, route to the least-loaded agent with the matching skill, discount by
  quantity tier. That lets the examples check the agent's output against the
  rule, not just read its prose.
- **Ends in a write.** The last step changes the artifact, so the dry-run gate
  matters and the compiled plan flags that step for approval.

The cross-system examples add a fourth property: **the case crosses systems
that do not share an id.** That is where most of the waiting and retyping in a
real process happens, and where a single system's logs show only a fragment.

All the examples give the same kind of result:

```
agent trace: query:orders -> send_operation:createItemsShipments -> execute:orders
conforms to the to-be plan: True · fitness: 1.00
ok  O-2001: booked net 135.0 (rule 135.0), status shipped, shipment_ref 32
```

## Writing your own

For one system, copy `ticket_triage/workflow.py`. It is the shortest complete
version. You need four things:

1. **An observed registry.** Wrap your domain's tools with an `OcelTap` and its
   mapper, and turn on activity qualification so steps read as business actions.
2. **A demonstration.** Run the workflow by hand through `dispatch` across a few
   cases, one artifact per case.
3. **Mining.** Run `collapse_repeats` and then a miner. Use `best_miner()` or
   pick one.
4. **Compile and run.** Call `compile_plan(model, specs)`, then
   `plan.system_prompt(goal)` and `restrict_tools(...)`, then any agent backend.
   Check the agent's trace with `check_trace`.

For several systems, copy `order_fulfillment/workflow.py` and add two pieces:
a `CompositeMapper` that routes each tool to its domain's mapper, and a
`Correlator` with one `KeyRule` per place your business key appears. To mine
a system's history instead of tapping it, see the `RecordMapping`s in
`hire_to_onboard/workflow.py`.
