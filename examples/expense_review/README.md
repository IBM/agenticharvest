# Expense review: observe → mine → agentify

A finance team reviews employee expense claims every month in a macro-enabled
workbook:

| Sheet | Role | Contents |
|---|---|---|
| `Claims` | input | one row per claim: id, employee, category, amount |
| `Policy` | input | per-category limits (Meals 75, Travel 500, Lodging 250, Supplies 100) |
| `Decisions` | output | claim id → `approved` / `flagged` + reason |

The VBA (`ReviewClaims → CheckPolicy → ApproveClaims / FlagForReview`) records
the outcomes. AgenticHarvest recovers that structure statically, without running
Excel:

| Macro | Reads | Writes |
|---|---|---|
| `LoadClaims` | Claims | — |
| `PolicyLimit` | Policy | — |
| `ApproveClaims`, `FlagForReview` | — | Decisions |

This example needs no live systems and no Excel: the workbooks are real
`.xlsm` files AgenticHarvest builds, and every tool works on the file.

```bash
uv sync --extra excel --extra openai_compatible
uv run python examples/expense_review/run.py            # stages 1-3 (a few seconds)
uv run python examples/expense_review/run.py --agent    # + stage 4 (live LLM)
```

`--agent` reads `API_BASE`, `API_KEY`, and `MODEL` from `.env` (copy
`.env.example`). Any OpenAI-compatible endpoint works.

## The four stages

**1. Observe.** A reviewer works three monthly batches through the real tools.
Each tool call goes through the gateway, so writes pass the dry-run gate, and
an `OcelTap` records it. `ExcelOCEL(qualify_sheets=True)` records business
steps such as `read_range:Policy` rather than bare tool names.

```
  batch 2026-07:
    C-101  approved  42.50 within Meals limit 75.00
    C-102  flagged   640.00 over Travel limit 500.00
    C-103  approved  18.20 within Supplies limit 100.00
  …
  36 tool calls recorded -> expense_review_out/observed.ocel.json
```

The case is the **workbook**: one per monthly batch. The reviewer's 36 calls
are 3 batches × (read Claims, read Policy, check the data flow, and three cell
writes per claim).

**2. Mine.** The 36 recorded calls fold into 3 cases with one variant:

```
  miner: bupaR · cases: 3 · events: 12
  variant x3: read_range:Claims -> read_range:Policy -> get_data_flow -> write_cell:Decisions
  BPMN: 4 nodes, 5 flows
```

`collapse_repeats` folds the per-claim writes into one step first, so a batch
of 2 claims and a batch of 4 share a variant. That leaves 12 events: 3 cases
× 4 steps. bupaR discovers the BPMN when R is installed. Without R, the
pure-Python variant miner gives the same variants and no BPMN, and the first
line says `miner: variants`. The model is saved to `process_model.json`.

**3. Compile.** `compile_plan` turns the dominant variant into a plan.
`plan.system_prompt(GOAL)` renders it, and the write step is flagged for
approval:

```
1. Call read_range on 'Claims'.
2. Call read_range on 'Policy'.
3. Call get_data_flow.
4. Call write_cell on 'Decisions'. This writes: pass confirm=true only once the checks above pass.
```

`restrict_tools` grants the agent only the 3 tools the process uses, out of the
8 the Excel domain offers.

**4. Agentify** (`--agent`). A live model reviews a batch it has never seen.
Its calls are tapped into a fresh log, then checked two ways:

- **Against the rule.** The decisions on disk must match the policy.
- **Against the process.** `check_trace` replays the agent's trace against the
  mined model.

```
decisions on disk vs. policy:
  ok  C-401: wrote 'approved', policy says 'approved'
  ok  C-402: wrote 'flagged', policy says 'flagged'
  ok  C-403: wrote 'approved', policy says 'approved'

agent trace: read_range:Claims -> read_range:Policy -> get_data_flow -> write_cell:Decisions
conforms: True · fitness: 1.00
```

## Outputs

All in `expense_review_out/` in the repo root (change it with `--out`):

| File | What it holds |
|---|---|
| `claims-2026-07.xlsm` … `claims-2026-09.xlsm` | The three observed batches, with the reviewer's decisions on `Decisions` |
| `claims-2026-10.xlsm` | The batch the agent reviewed (with `--agent`) |
| `observed.ocel.json` | The reviewer's 36 calls, as an OCEL 2.0 event log |
| `process_model.json` | The miner's output: activities, directly-follows, variants, and a BPMN with bupaR |
| `agent_prompt.txt` | The exact system prompt the agent received |

Open any `.xlsm` in Excel or LibreOffice to see the decisions. Opening works;
it's only *running* the macros that needs a workbook Excel authored (see
[`../excel/`](../excel/README.md#running-macros-needs-a-workbook-excel-authored)).

## Files

- [`workflow.py`](workflow.py): the stages as functions, also imported by
  [`tests/examples/test_workflows.py`](../../tests/examples/test_workflows.py).
- [`run.py`](run.py): prints each stage.
- The workbook comes from `agenticharvest.domains.excel.samples.build_expense_workbook`.
