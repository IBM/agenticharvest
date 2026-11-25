# Contributing to AgenticHarvest

## Contributing in general

Our project welcomes external contributions.

To contribute code or documentation, please submit a
[pull request](https://github.com/IBM/agenticharvest/pulls).

A good way to get familiar with the codebase and the contribution process is to
look for and tackle low-hanging fruit in the
[issue tracker](https://github.com/IBM/agenticharvest/issues). Before you start
a more ambitious contribution, please [get in touch](#communication) with us
first.

**Note: we appreciate your effort, and we want to avoid a situation where a
contribution needs extensive rework (by you or by us), sits in the backlog for a
long time, or cannot be accepted at all.**

### Proposing new features

If you would like to implement a new feature, please
[raise an issue](https://github.com/IBM/agenticharvest/issues) before you send a
pull request, so the feature can be discussed first. That saves you from
spending time on a feature the maintainers may not be able to accept.

### Fixing bugs

If you would like to fix a bug, please
[raise an issue](https://github.com/IBM/agenticharvest/issues) before you send a
pull request, so it can be tracked.

### Merge approval

The project maintainers use LGTM (Looks Good To Me) in comments on the code
review to indicate acceptance. A change requires LGTMs from two of the
maintainers of each component affected, or from one when the project has only
one maintainer for that component.

For a list of the maintainers, see [MAINTAINERS.md](MAINTAINERS.md).

## Legal

### License headers

Each source file must include a license header for the Apache License 2.0.
Use the SPDX format:

```python
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
```

### Developer's Certificate of Origin

We have tried to make the legal side of contributing as easy as possible. We use
the same approach as the Linux® Kernel
[community](https://elinux.org/Developer_Certificate_Of_Origin): the
[Developer's Certificate of Origin 1.1 (DCO)](https://github.com/hyperledger/fabric/blob/master/docs/source/DCO1.1.txt).

When you submit a patch for review, include a sign-off statement in each commit
message. A `Signed-off-by` line indicates that you accept the DCO:

```
Signed-off-by: John Doe <john.doe@example.com>
```

Git adds it for you when you commit with `-s`:

```bash
git commit -s
```

The repository runs the [DCO bot](https://github.com/probot/dco), which checks
every commit in a pull request for the sign-off.

By submitting a contribution, you agree it is licensed under the
[Apache License 2.0](LICENSE), as described in section 5 of the license.

## Communication

Please use [GitHub issues](https://github.com/IBM/agenticharvest/issues) for
questions, bug reports, and feature proposals. For anything that should not be
public, contact a maintainer listed in [MAINTAINERS.md](MAINTAINERS.md). For
security issues, follow [SECURITY.md](SECURITY.md) instead.

## Setup

AgenticHarvest is a small, layered library. Most contributions fall into one of
three plugin seams (a **domain**, a **backend**, or a **miner**), and none of
them requires touching the core.

It uses [`uv`](https://github.com/astral-sh/uv) and a `src/` layout.

```bash
git clone --recurse-submodules https://github.com/IBM/agenticharvest.git && cd agenticharvest
uv sync --extra excel --extra web --extra rest --extra mcp --extra openai_compatible --extra dev
```

### The submodules

Three folders are git submodules, each its own public repository under
[github.com/aster-test-generation](https://github.com/aster-test-generation), so
people can clone and run an example on its own:

| Path | Repository | What it holds |
|---|---|---|
| `examples/systems` | `agenticharvest-systems` | The live stack: compose file, `setup.py`, the container runner, `run.sh` / `run.ps1` |
| `examples/order_fulfillment` | `agenticharvest-example-order-fulfillment` | The database + portal example |
| `examples/hire_to_onboard` | `agenticharvest-example-hire-to-onboard` | The HR example |

They track `main`. To change one, commit inside it, push it, then record the
new commit in AgenticHarvest:

```bash
cd examples/hire_to_onboard
git switch main && git pull
# … edit, test from the AgenticHarvest root …
git commit -s -am "…" && git push
cd ../.. && git add examples/hire_to_onboard && git commit -s -m "Update hire_to_onboard"
```

To pull everyone's latest: `git submodule update --remote --merge`. `tests/examples/test_cross_system.py` and `test_cross_system_agents.py` import
from these folders. A clone without them still runs: those tests skip.

## Testing

Before you open a pull request, run the same checks CI runs:

```bash
uv run ruff check .          # lint
uv run mypy src              # types (strict)
uv run pytest -q             # the suite; live-system tests skip without the stack
```

The suite avoids mocks: artifacts and web apps are real. The one exception is
the OpenAI-compatible backend's unit test, which scripts model responses over an
httpx transport. Tests marked `live` call the live web apps in
[`examples/systems`](examples/systems/). Bring them up with one compose file
and `setup.py` to run those tests. Without the stack they skip cleanly. Tests
marked `real_llm` call a live model and are deselected unless you ask for them
(`uv run pytest -m real_llm`). They read the endpoint from a `.env` file (see
`.env.example`).

If you change the web domain, run the live tests before you open the pull
request: `uv run pytest -m live`.

The bupaR miner tests need R; without it they skip cleanly. See the README
section "Optional: the mining engine" if you want to run them.

## The one rule

**Only `src/agenticharvest/domains/` knows what an artifact is.** The tool
contract, gateway, channels, event log, miners, and agent loop must stay
domain-agnostic. If a change to a generic layer mentions a spreadsheet or a
table, it belongs in a domain instead.

## Adding a domain

`src/agenticharvest/domains/sqlite/` is the smallest complete example. It is
about 450 lines, plus sample data, and uses only the standard library. A domain provides:

1. **Tool specs and handlers.** Each tool is a `ToolSpec` (name, one-sentence
   description, JSON Schema input, `Effect.READ` or `Effect.WRITE`) paired with
   a handler `dict -> JSON-serializable value`. Raise on bad input; the
   registry turns an exception into a failed `ToolResult`.
2. **An OCEL mapper** (optional but recommended). Subclass `GenericOcelMapper`
   so each call relates to typed objects. Without one, events still record
   against a generic `artifact`.
3. **A `SimpleDomain`** and an entry point:

   ```toml
   [project.entry-points."agenticharvest.domains"]
   mydomain = "my_package:domain"
   ```

4. **Tests** under `tests/domains/<name>/`, including the dry-run gate for any
   write tool.

Mark every tool that changes the artifact `Effect.WRITE`. The gateway then
requires `confirm=true` before that tool runs.

Tool names are global across domains. Before you name a tool, check
`uv run agenticharvest tools`: two domains offering the same name cannot load
together. The web domain's `get_field_flow` is named that way because Excel
already has `get_data_flow`.

A domain whose tools talk to a live system (as `domains/web/` does) keeps every
connection in one client class, and that class enforces the safety rules. Its
tests run against a real instance and skip when none is configured. See
`tests/domains/web/conftest.py`.

## Keeping examples out of `src/`

Code in `src/` is generic: it never names an example, a business object, or a
product it happens to be tested against. Correlation rules, record mappings,
and policies that describe one business process belong to that process's
example under `examples/`. If a generic module needs a constant that only one
example uses, the example should pass it in instead.

## Adding a miner

Implement the `Miner` protocol (`name`, `available()`, `mine(log) -> ProcessModel`)
and register it under `agenticharvest.miners`. `agenticharvest.mining.variants`
is a short pure-Python reference.

Do not add dependencies with GPL-family licenses (GPL, LGPL, AGPL), either as
required dependencies or as extras. This is why pm4py (AGPL-3.0) is not a
dependency; the README section "Using pm4py" shows how users can plug it in
themselves.


## Pull requests

- Keep each PR focused on one change, and add or update tests with it.
- Describe what changed and why. Link an issue if there is one.
- Sign off every commit (`git commit -s`).

## Reporting issues

Please include the AgenticHarvest version
(`python -c "import agenticharvest; print(agenticharvest.__version__)"`), your
Python version and OS, the extras you installed, and a minimal reproduction.
For security issues, see [SECURITY.md](SECURITY.md) instead.
