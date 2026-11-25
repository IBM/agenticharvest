# Ticket triage: the same loop, a different domain

A support team works out of a SQLite database. Each morning a triager assigns
the open tickets:

- **High priority first.**
- **Skill match.** The agent's `skill` equals the ticket's `product`.
- **Lightest load.** Among matching agents, pick the lowest `open_load`, which
  changes as tickets are assigned.

An `AFTER INSERT` trigger on `assignments` marks the ticket assigned, bumps the
agent's load, and writes the audit log, as a production schema would.

```bash
uv run python examples/ticket_triage/run.py            # stages 1-3, no extras needed
uv run python examples/ticket_triage/run.py --agent    # + stage 4 (live LLM)
```

`--agent` needs `uv sync --extra openai_compatible` and reads `API_BASE`,
`API_KEY`, and `MODEL` from `.env` (copy `.env.example`).

## Why this example exists

It is the same four stages as [`expense_review/`](../expense_review/), and the
code is nearly line for line the same. **Only the domain import changes:**

```python
from agenticharvest.domains.sqlite.ocel import SqliteOCEL      # instead of ExcelOCEL
from agenticharvest.domains.sqlite.tools import sqlite_tools   # instead of excel_tools
```

The tap, the miners, `compile_plan`, `check_trace`, the agent adapters, and the
agent loop are all shared. That is the claim "nothing above the domain layer
knows what the artifact is", running.

## What it produces

```
variant x3: query:tickets -> query:agents -> execute:assignments -> query:tickets

1. Call query on 'tickets'.
2. Call query on 'agents'.
3. Call execute on 'assignments'. This writes: pass confirm=true only once the checks above pass.
4. Call query on 'tickets'.

tools granted: ['query', 'execute']  (of 5 available)
```

With `--agent`, a live model triages a new queue. Its assignments are checked
against `route()`, the same rule the triager followed, and its trace is
checked against the mined process:

```
assignments on disk vs. routing rule:
  ok  #4002: agent 2, rule says agent 2
  ok  #4001: agent 5, rule says agent 5
  ok  #4003: agent 3, rule says agent 3
  ok  #4004: agent 2, rule says agent 2

agent trace: query:tickets -> query:agents -> execute:assignments -> query:tickets
conforms: True · fitness: 1.00
```

The last assignment is the hard one. Marco's load went from 0 to 1 after
#4002, which is still below Priya's 2, so #4004 goes to Marco as well. The
agent has to track the changing load to get it right.

## Safety

`query` opens the database read-only at the SQLite level, so even a writing CTE
is refused. `execute` accepts exactly one INSERT, UPDATE, DELETE, or REPLACE,
never DDL, and runs it in a transaction with foreign keys enforced. It is a
write tool, so the gateway blocks it unless the call carries `confirm=true`.
