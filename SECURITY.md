# Security policy

## Supported versions

AgenticHarvest is pre-1.0. Security fixes are made on the latest release only.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Report it
privately through the repository's private vulnerability reporting
(**Security → Report a vulnerability** on
[github.com/IBM/agenticharvest](https://github.com/IBM/agenticharvest/security)),
or by email to a maintainer listed in [MAINTAINERS.md](MAINTAINERS.md).

Include a description of the issue, the steps you took to create it, the
affected versions, and, if known, mitigations. We will acknowledge receiving
your report within 3 working days. This project follows a 90-day disclosure
timeline.

## What AgenticHarvest guards, and what it does not

AgenticHarvest gives an agent typed tools over real artifacts, so a few
safeguards are part of its design:

- **The dry-run gate.** Every tool marked `Effect.WRITE` is blocked at the
  gateway unless the call carries `confirm=true`. This applies on every
  channel: REST, MCP, the CLI, and in-process adapters.
- **Read-only SQL.** The SQLite `query` tool opens the database in SQLite's
  read-only mode, so the engine itself refuses writes. `execute` accepts exactly
  one DML statement and never DDL.
- **No credentials in code or logs.** LLM endpoints and web apps are
  configured from the environment or a `.env` file, which is gitignored. Web
  credentials are attached to a request at send time. They never appear in a
  tool result, an event, or a span, and credential response headers are
  dropped.
- **Web requests stay on the configured app.** The web domain refuses any
  request whose URL leaves the app's base origin. It quotes each path
  parameter into a single segment, and it never follows redirects. A body is
  checked against the app's API description before it is sent.

Some things are **out of scope**, and you are responsible for them when you
deploy AgenticHarvest:

- **Authentication and authorization.** The REST and MCP channels have none.
  Bind them to localhost or put them behind your own auth.
- **Filesystem scope.** Tools open the `path` a caller passes. Run the gateway
  as a user that can reach only the files it should.
- **Macro execution.** `run_macro` runs VBA in a live Excel instance. Treat
  workbooks from untrusted sources as you would any untrusted macro.
- **Model behavior.** The gate stops accidental writes, not a model that has
  been told to pass `confirm=true`. Keep a human in the loop for writes that
  matter.
- **What a web app lets the configured identity do.** The web tools can call
  any operation the app's description lists, with whatever rights its token
  carries. Give AgenticHarvest a least-privilege account, and use a sandbox tenant
  until you trust the process. The bundled `examples/systems` stack uses
  well-known admin passwords and is for local use only.
- **API descriptions.** The IR is built from the description the app serves.
  A tampered description can rename or hide operations, so load descriptions
  only from apps you trust.
