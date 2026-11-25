# Tests

The layout mirrors `src/agenticharvest/`: `core/`, `gateway/`, `domains/excel/`,
`domains/sqlite/`, `domains/web/`, `mining/`, and `orchestration/`. One more
directory, `examples/`, runs the shipped examples' stages so the walkthroughs
cannot rot.

- **Real artifacts and live systems, not mocks.** Workbooks come from
  `agenticharvest.domains.excel.samples` (genuine `.xlsm` files with real VBA, built
  without Excel), and databases come from `agenticharvest.domains.sqlite.samples`.
  The web domain is tested against live Directus, Keycloak, and Odoo servers,
  and against Keycloak's published API description. No web-app response is
  recorded or simulated. (The OpenAI-compatible backend's unit test scripts its
  model responses.)
- **Live systems skip when absent.** Tests marked `live` run by default. They
  read their endpoints from `.env` and skip, not fail, when a system is not
  configured or not answering. Bring the systems up with
  [`examples/systems`](../examples/systems/) to run them. Each live test
  removes every record it creates.
- **Optional extras skip cleanly.** Tests that need an optional extra call
  `pytest.importorskip(..., reason="needs the 'X' extra")`, so a core-only
  install still runs the rest of the suite.
- **R is optional.** The bupaR integration tests skip when `Rscript` and
  bupaR are not installed.
- **Live models are opt-in.** Tests marked `real_llm` are deselected unless you
  run `pytest -m real_llm`, and they skip when no `.env` endpoint is configured.

```bash
uv run pytest                                  # everything but real_llm; live tests skip without the stack
uv run pytest -m live                          # only the live-system tests
uv run pytest -m "not live and not real_llm"   # only the hermetic tests
uv run pytest -m real_llm                      # the live-LLM agent stages
```

Passing `-m` replaces the default `-m 'not real_llm'`, so name `real_llm`
yourself whenever you filter by marker.
