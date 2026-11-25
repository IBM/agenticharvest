# How AgenticHarvest works: one hire, end to end

This page follows a single new hire, Ada Byron, through every layer of
AgenticHarvest. You'll see what each layer receives and what it produces, with the
real request, the real HTTP traffic, the real event, and the real model. All of
it was captured from one run of
[`examples/hire_to_onboard`](../examples/hire_to_onboard/WALKTHROUGH.md)
against live Odoo, Keycloak, and Directus servers. Ids and tags differ in every
run.

If you want the layers in the abstract, read
[architecture.md](architecture.md). This page is the concrete version.

**Contents**

1. [The architecture, in one picture](#1-the-architecture-in-one-picture)
2. [Before anything runs: the apps become tools](#2-before-anything-runs-the-apps-become-tools)
   (and [what a tool is: code, never an LLM call](#what-a-tool-is-code-never-an-llm-call))
3. [One call, layer by layer](#3-one-call-layer-by-layer)
4. [Ada's whole hire, as the systems saw it](#4-adas-whole-hire-as-the-systems-saw-it)
5. [From calls to one event log](#5-from-calls-to-one-event-log)
6. [The same hire, from the systems' own history](#6-the-same-hire-from-the-systems-own-history)
7. [From events to a process model](#7-from-events-to-a-process-model)
8. [From the model to policy and a plan](#8-from-the-model-to-policy-and-a-plan)
9. [The agent loop](#9-the-agent-loop)
10. [Checking the agent](#10-checking-the-agent)
11. [What is generic, and what the example adds](#11-what-is-generic-and-what-the-example-adds)

---

## 1. The architecture, in one picture

```
                       the HR coordinator (stage 1)          the LLM agent (stage 5)
                       a person, or a script                  any OpenAI-compatible model
                                 │                                        │
                                 │ tool calls                             │ tool calls
                                 ▼                                        ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │ CHANNELS      in-process · REST · MCP · CLI       (all four reach the same path) │
   └────────────────────────────────────────┬─────────────────────────────────────────┘
                                            ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │ GATEWAY       dispatch():  unknown tool? ─▶ error                                │
   │                            write without confirm=true? ─▶ blocked (dry-run gate) │
   └────────────────────────────────────────┬─────────────────────────────────────────┘
                                            ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │ TOOL LAYER    ToolRegistry: spec + handler per tool          ─── TAPS ───────────┼──┐
   │               list_resources · describe_operation · get_field_flow ·             │  │
   │               call_operation · send_operation                                    │  │
   └────────────────────────────────────────┬─────────────────────────────────────────┘  │
                                            ▼                                            │
   ┌──────────────────────────────────────────────────────────────────────────────────┐  │
   │ WEB DOMAIN    WebIR (built from each app's own API description)                  │  │
   │   (the only  · checks the body against the IR · quotes path parameters           │  │
   │   layer that   WebClient: auth · origin allowlist · credentials kept out ·      │  │
   │   knows what   bounded responses                                                 │  │
   │   an API is)                                                                     │  │
   └───────┬───────────────────────────────┬──────────────────────────────┬───────────┘  │
           ▼                               ▼                              ▼              │
     Odoo  :8069                    Keycloak  :8080                 Directus  :8055      │
     HR system                      identity server                 task tracker         │
     chatter (history)              admin events (history)          activity (history)   │
           └───────────────────────────────┼──────────────────────────────┘              │
                                           │ stage 2: import_records(RecordMapping)      │
                                           ▼                                             │
   ┌──────────────────────────────────────────────────────────────────────────────────┐  │
   │ EVENT LOG     OCEL 2.0: events + objects, joined by qualified relationships   ◀──┼──┘
   │               OcelTap → CompositeMapper (which system) → Correlator (which person)│
   └────────────────────────────────────────┬─────────────────────────────────────────┘
                                            ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │ MINING        miner (bupaR or variants) → ProcessModel                           │
   │               analysis: handoffs · object-centric DFG · check_rules (policy)     │
   └────────────────────────────────────────┬─────────────────────────────────────────┘
                                            ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │ ORCHESTRATION compile_plan → system prompt + restrict_tools → agent loop         │
   │               check_trace: the agent's own log, replayed against the model       │
   └──────────────────────────────────────────────────────────────────────────────────┘
                                            │
                                            └──▶ the agent's calls re-enter at CHANNELS
```

The rule that holds this together: **only the domain layer knows what the
artifact is.** The gateway, the taps, the event log, the miners, and the agent
loop never mention Odoo, an API, or a person. The HR example supplies those
specifics as data: the correlation rules, the history mappings, and the policy.

---

## 2. Before anything runs: the apps become tools

Nobody wrote a tool for Odoo, Keycloak, or Directus. When a web tool first
names an app, the domain's `WebClient` loads that app's description of its own
API, and `ir.py` turns it into a **WebIR**: resources, operations, and fields.

| App | Description it serves | What the IR holds |
|---|---|---|
| Odoo | `/doc/<model>.json` for the 5 configured models | 5 resources, 51 operations (41 write) |
| Keycloak | the vendor's Admin REST API, OpenAPI 3 | 50 resources, 369 operations (181 write) |
| Directus | `/server/specs/oas`, its live OpenAPI 3 | 2 resources, 14 operations (10 write) |

Each operation records its method, path, path parameters, and body fields. One
operation, as the IR holds it:

```
name:        post_users
method:      post                       → a write: gated
path:        /admin/realms/{realm}/users
resource:    users
path_params: [realm]
body_fields: id, username, firstName, lastName, email, emailVerified, enabled, …
```

Odoo has no OpenAPI. Its self-description lists each model's fields and public
methods, and `from_odoo` maps the standard methods to the HTTP verb that
matches what they do: `search_read` is a read, `write` is a patch, and
`create_employee_from_applicant` is gated as a write. All of them are sent as
`POST /json/2/<model>/<method>`, as Odoo requires.

The agent never sees these 434 operations as 434 tools. It sees **five tools**
that take an app and an operation name, so one tool schema covers every app:

```json
{ "type": "function",
  "function": {
    "name": "send_operation",
    "description": "Call a write operation on a web app (create, update, or delete) and return its response.",
    "parameters": { "type": "object",
      "properties": {
        "app":         { "type": "string", "description": "The configured web app name." },
        "name":        { "type": "string", "description": "The operation name." },
        "path_params": { "type": "object", "description": "Values for the operation's {placeholders}, by name." },
        "query":       { "type": "object", "description": "Query parameters, by name." },
        "body":        { "description": "The JSON request body, when the operation takes one." },
        "confirm":     { "type": "boolean", "description": "Must be true to apply this change." } },
      "required": ["app", "name"] } } }
```

That is exactly what is sent to the LLM in stage 5.

### What a tool is: code, never an LLM call

Every tool is a **plain Python function**: a `ToolSpec` (name, description,
JSON Schema input, read or write) paired with a handler `dict -> JSON`. No tool
calls an LLM, and nothing that builds the tools does either:

| What | How it is made | LLM? |
|---|---|---|
| The **web IR** (resources, operations, fields) | Parsed from the app's own API description: OpenAPI 3 JSON, or Odoo's `/doc` JSON | No |
| The **Excel IR** (sheets, macros, call graph, data flow) | Static analysis of the file: openpyxl for structure, oletools for VBA, pattern matching for data flow | No |
| The **SQLite IR** (tables, keys, triggers) | SQLite's own catalog (`sqlite_master`, `PRAGMA`) | No |
| A **tool call** (`send_operation`, `read_range`, `query`, …) | A handler: validate the parameters, then make one HTTP request, read one range, or run one SQL statement | No |
| **Correlation, import, mining, analysis, policy, plan, conformance** | Rules, counting, and graph algorithms over the event log | No |
| **The agent** (stage 5 only) | An LLM chooses which tool to call next, with which arguments | **Yes, and only here** |

In the source, LLM code lives only in `orchestration/agents/` (the backends)
and `utils/llm/` (the endpoint settings). Nothing in `domains/`, `core/`,
`gateway/`, or `mining/` calls an LLM. (The web domain reuses only the `.env`
reader from `utils/llm/`.) That is why stages 1 to 4 of every example
run, and are tested, with no LLM configured, and why they give the same result
every time.

What the handler for `send_operation` actually does, in full:

```python
def send_operation(params: dict) -> dict[str, Any]:
    """Call one write operation and return its response."""
    client, operation = _operation(params)      # find the app's client, and the operation in its IR
    if not operation.writes:
        raise ValueError(f"'{operation.name}' is a read; use call_operation.")
    return _invoke(client, operation, params)   # check path params and body, build the URL, send
```

So the LLM decides **what** to do (which tool, which operation, which
arguments), and deterministic code decides **whether it is allowed** and
**does it**.

---

## 3. One call, layer by layer

Take one step of Ada's hire: **creating her sign-in account in Keycloak.**

### a. The tool request

The coordinator's script (or, in stage 5, the agent) asks for:

```python
dispatch(registry, ToolRequest(tool="send_operation", params={
    "app": "keycloak",
    "name": "post_users",
    "path_params": {"realm": "agenticharvest"},
    "body": {"username": "ada.byron+8c3a0c", "email": "ada.byron+8c3a0c@example.com",
             "firstName": "Ada", "lastName": "Byron", "enabled": True},
    "confirm": True,
}))
```

### b. The gateway

`dispatch()` looks the tool up. `send_operation` has `Effect.WRITE`, so the
**dry-run gate** checks for `confirm=true`. Without it, the call stops here,
and nothing is sent:

```
Tool 'send_operation' has a write effect and requires 'confirm: true' to run.
```

With it, the call passes to the registry.

### c. The tool and the domain

The `send_operation` handler:

1. Finds the app's client and its IR, and the operation `post_users`.
2. Checks it is a write. `call_operation` would refuse it here, so a read-only
   grant really is read-only.
3. Checks the path parameters. `realm` is required and present. Its value is
   quoted into one URL segment, so `../../master` can't reach another realm.
4. Checks the body against the operation. Keycloak's schema is open, so any
   extra fields pass; Odoo's are closed, so a wrong key is refused (see
   [section 9](#9-the-agent-loop)).
5. Builds the URL: `http://127.0.0.1:8080/admin/realms/agenticharvest/users`.

### d. The client and the wire

`WebClient` checks the URL is on the app's own origin, then adds credentials.
Keycloak is configured for `oauth2-password`, so the first call fetches an
admin token and caches it until shortly before it expires. These are the two
requests actually sent:

```
POST /realms/master/protocol/openid-connect/token   grant_type=password&client_id=admin-cli&username=admin&password=…
POST /admin/realms/agenticharvest/users                   {"username":"ada.byron+8c3a0c","email":"ada.byron+8c3a0c@example.com",
                                                     "firstName":"Ada","lastName":"Byron","enabled":true}
```

The `Authorization: Bearer …` header is added at send time and never stored
anywhere else.

### e. The result

Keycloak answers `201 Created` with no body, and a `Location` header naming
the new user. The client keeps only safe headers (never `Set-Cookie` or
`Authorization`), caps the body at 20,000 characters, and the tool returns:

```json
{ "app": "keycloak", "operation": "post_users", "resource": "users", "status": 201,
  "body": null, "truncated": false,
  "headers": { "location": "http://127.0.0.1:8080/admin/realms/agenticharvest/users/d14100db-81bc-4504-a58b-b8d7e91d8699" } }
```

### f. The taps

After the call returns, the registry hands `(tool, params, result)` to every
tap. The `OcelTap` turns it into one event, as [section 5](#5-from-calls-to-one-event-log)
shows. A tap runs after the call, so it can't block it. The built-in taps
(`OcelTap`, `TracingTap`) also catch their own errors, so a mapping bug never
turns a successful call into a failed one. A tap you write yourself should do
the same.

A handler that raises (an HTTP 404, a bad body) doesn't crash anything either:
the registry turns the exception into a failed result, `ok: false` with the
error text, which is what the caller, or the agent, reads back.

---

## 4. Ada's whole hire, as the systems saw it

Before the coordinator starts, the recruiter (outside the observed process)
creates a job position (`100`) and Ada's applicant record (`162`) in Odoo,
directly.

Then the coordinator makes **8 tool calls**, which become these **HTTP requests**:

```
 1  POST /json/2/hr.recruitment.stage/search_read          {"domain":[],"fields":["name"],"context":{"email":"ada.byron+8c3a0c@…"}}
 2  POST /json/2/hr.applicant/write                        {"ids":[162],"vals":{"stage_id":3},"context":{"email":…}}
 3  POST /json/2/hr.applicant/write                        {"ids":[162],"vals":{"stage_id":5},"context":{"email":…}}
 4  POST /json/2/hr.applicant/write                        {"ids":[162],"vals":{"stage_id":6},"context":{"email":…}}
 5  POST /json/2/hr.applicant/create_employee_from_applicant  {"ids":[162],"context":{"email":…}}
    POST /json/2/hr.applicant/read                         {"ids":[162],"fields":["employee_id"]}   (direct, untapped)
    GET  /server/specs/oas                                 (Directus's description, loaded once)
 6  POST /items/onboarding_tasks                           [{"employee_ref":"ada.byron+8c3a0c@…","task":"laptop","owner":"it",…},
                                                            {…"task":"account"…}, {…"task":"payroll"…}]
    POST /realms/master/protocol/openid-connect/token      (Keycloak login, cached)
 7  POST /admin/realms/agenticharvest/users                      {"username":"ada.byron+8c3a0c","email":…,"enabled":true}
 8  PATCH /items/onboarding_tasks/479                      {"status":"done 8c3a0c","employee_ref":…}
```

The numbered lines are the coordinator's tool calls. The others are the
domain's own work (loading a description, logging in) and one direct read the
example makes to learn the new employee id.

Things to notice:

- **Stage ids 3, 5, 6** are First Interview, Contract Proposal, and Contract
  Signed. The coordinator looked them up in call 1, rather than hard-coding
  them.
- **The email rides in Odoo's `context`** on every Odoo call. Odoo calls name
  records only by id (`162`), and Odoo ignores context keys it doesn't use. The
  email is there so the correlator can tell whose call it is.
- **The three systems gave Ada four different ids:** applicant `162`, employee
  `164`, Keycloak user `d14100db-…`, tasks `478`/`479`/`480`. None of them
  appears in another system.

---

## 5. From calls to one event log

Each of the 8 calls becomes one event. Two pieces decide what the event says.

**`CompositeMapper` decides which system.** It routes each call to the mapper
of the domain that owns the tool. For web tools that is `WebOCEL`, which
relates the event to three objects: the **web app**, the **operation**, and the
**resource**. It records the HTTP status and any record ids too.

**`Correlator` decides which person.** It wraps the mapper and applies the
example's `KeyRule`s, which say where the email appears in each system's calls:

```python
KeyRule(object_type="person", tools=["send_operation", "call_operation"],
        when={"app": "odoo"},     param="body",       pattern=EMAIL, transform="lower")
KeyRule(object_type="person", tools=["send_operation", "call_operation"],
        when={"app": "keycloak"}, param="body.email", transform="lower")
KeyRule(object_type="person", tools=["send_operation", "call_operation"],
        when={"app": "directus"}, param="body",       pattern=EMAIL, transform="lower")
```

Every match adds a `person` object to the event. A call made with
`confirm=true` relates to the person as `writes`; any other call as `reads`.

The resulting **8 events** for Ada:

```
e1  09:00  call_operation:hr.recruitment.stage.search_read              webapp:odoo      person:ada.byron
e2  09:20  send_operation:hr.applicant.write                             webapp:odoo      person:ada.byron
e3  09:40  send_operation:hr.applicant.write                             webapp:odoo      person:ada.byron
e4  10:00  send_operation:hr.applicant.write                             webapp:odoo      person:ada.byron
e5  10:20  send_operation:hr.applicant.create_employee_from_applicant    webapp:odoo      person:ada.byron
e6  10:40  send_operation:createItemsOnboardingTasks                     webapp:directus  person:ada.byron
e7  11:00  send_operation:post_users                                     webapp:keycloak  person:ada.byron
e8  11:20  send_operation:updateSingleItemsOnboardingTasks               webapp:directus  person:ada.byron
```

Each event's **activity** is the tool plus the operation
(`send_operation:post_users`), because the mapper qualifies activities. So
the mined process reads as business steps, not as "send, send, send".

And the **14 objects** the log holds after Ada's hire:

```
webapp:odoo        operation:odoo:hr.recruitment.stage.search_read    resource:odoo:hr.recruitment.stage
                   operation:odoo:hr.applicant.write                  resource:odoo:hr.applicant
                   operation:odoo:hr.applicant.create_employee_from_applicant
webapp:directus    operation:directus:createItemsOnboardingTasks      resource:directus:onboarding_tasks
                   operation:directus:updateSingleItemsOnboardingTasks
webapp:keycloak    operation:keycloak:post_users                      resource:keycloak:users
person:ada.byron+8c3a0c@example.com
```

One person object, and every event points to it, whichever system it ran in.
**That is the join.** Three systems that share no id now share a case.

The full event for `e7`, as written to `observed.ocel.json` (the OCEL 2.0
standard layout):

```json
{ "id": "e7", "type": "send_operation:post_users", "time": "2026-10-05T11:00:00Z",
  "attributes": [ { "name": "ok", "value": true }, { "name": "app", "value": "keycloak" },
                  { "name": "resource", "value": "users" }, { "name": "status", "value": 201 },
                  { "name": "domain", "value": "web" } ],
  "relationships": [
    { "objectId": "webapp:keycloak",                       "qualifier": "target" },
    { "objectId": "operation:keycloak:post_users",         "qualifier": "invokes" },
    { "objectId": "resource:keycloak:users",               "qualifier": "writes" },
    { "objectId": "person:ada.byron+8c3a0c@example.com",   "qualifier": "writes" } ] }
```

The times come from a steady clock that ticks 20 minutes per call, so the
policy's time window is measurable in a demo. In a real deployment, the tap
uses the real time.

---

## 6. The same hire, from the systems' own history

Stage 1 only sees work done **through** AgenticHarvest. Stage 2 rebuilds the same
hire from what each system recorded on its own, with no tapping.

| System | Its history | Read through |
|---|---|---|
| Odoo | chatter messages and tracked field changes on the applicant | `mail.message`, `mail.tracking.value` (JSON-2) |
| Keycloak | admin events for the realm | `GET /admin/realms/agenticharvest/admin-events` |
| Directus | the activity feed, with each change's saved data | `GET /activity` |

A `RecordMapping` per source turns each record into an event, using the same
event and object model as the tap. Ada's history, as the three systems
recorded it:

```
hris:new applicant                  ← Odoo, when the recruiter added her (the tap never saw this)
hris:stage First Interview          ← Odoo
hris:stage Contract Proposal        ← Odoo
hris:stage Contract Signed          ← Odoo
hris:note                           ← Odoo's "employee created" note
tracker:create:onboarding_tasks     ← Directus
identity:create:user                ← Keycloak
tracker:update:onboarding_tasks     ← Directus
```

These times are the systems' own timestamps. For example, Keycloak's event is
`time: 1793…430` in milliseconds, which the mapping reads with
`time_format="epoch-ms"`.

---

## 7. From events to a process model

With four hires observed, the log is mined with **`person` as the case**.

1. `collapse_repeats` folds a run of the same step on one person into one.
   Ada's three `hr.applicant.write` events become one step, so a hire with two
   interview stages and a hire with three share a variant.
2. `flatten` projects the object-centric log onto the case type: one row per
   (person, activity, time).
3. The **miner** groups rows by person, orders them by time, and counts. bupaR
   runs if R is installed (and adds a BPMN); otherwise the pure-Python variant
   miner runs. Both find the same variants.

The model, as printed:

```
from the observed calls — miner: bupaR · people: 4 · events: 24
variant x3: …stage.search_read -> …applicant.write -> …create_employee_from_applicant
            -> createItemsOnboardingTasks -> post_users -> updateSingleItemsOnboardingTasks
variant x1: post_users -> …stage.search_read -> …applicant.write -> …create_employee_from_applicant
            -> createItemsOnboardingTasks -> updateSingleItemsOnboardingTasks
BPMN: 6 nodes (6 tasks), 7 sequence flows
```

Ada is one of the three in the main variant. The fourth hire, Alan, had his
account created first, which is the deviation the policy catches next.

---

## 8. From the model to policy and a plan

**Policy.** The onboarding rules are written as `Rule`s and checked on each
person's events in time order:

```
3/4 pass  account only after the hire
4/4 pass  every hire gets an account
4/4 pass  checklist opened within a day of the hire
VIOLATION  alan.turing: account only after the hire — send_operation:post_users at 2026-10-05 17:00
           with no send_operation:hr.applicant.create_employee_from_applicant before it
```

For Ada, `precedes` checks that `create_employee_from_applicant` (e5, 10:20)
came before `post_users` (e7, 11:00). It did, so she passes.

**Plan.** `compile_plan` takes the dominant variant, the one Ada and two others
followed, and marks each step whose tool can write as needing approval. It
renders into the agent's system prompt (the full text is in the
[walkthrough](../examples/hire_to_onboard/WALKTHROUGH.md#7-stage-4-check-the-policy)).
Then `restrict_tools` keeps only the tools the plan uses: `call_operation` and
`send_operation`, of the five web tools.

The plan follows the **compliant** path, because the compliant path is what
most people did. Alan's variant is not what the agent learns.

---

## 9. The agent loop

Stage 5 hands a new hire, Katherine, to the agent. The loop, in
[`openai_compatible.py`](../src/agenticharvest/orchestration/agents/backends/openai_compatible.py):

```
messages = [ system: the compiled plan,
             user:   "Onboard Katherine Johnson (work email …). Their applicant id is 124. …" ]

repeat (at most max_retries + 1 times):
    POST /chat/completions   { model, messages, tools: [call_operation, send_operation] }
    if the reply has no tool calls:   return its text          ← done
    for each tool call in the reply:
        result = RegistryTool.run(arguments)
                   → dispatch() → gate → handler → WebClient → the live app
                   → taps record the call into the agent's own log
        append { role: "tool", tool_call_id, content: result } to messages
```

The model **never** reaches an app directly. Each of its tool calls takes the
same path as a coordinator's call in [section 3](#3-one-call-layer-by-layer),
through the same gate, checks, and taps.

**Errors come back as text, so the model can fix them.** A tool result is the
output as JSON, or `error: …`. When an agent sent Odoo's `write` with `id`
instead of `ids`, this is what it read back:

```
error: ValueError: Body for 'hr.applicant.write' is missing ids; unknown id. It takes: ids, vals, context.
```

Its next call used `ids`, and succeeded. Without that check, Odoo would have
answered `200 true` and changed nothing.

---

## 10. Checking the agent

The agent ends with a summary, but that is only the model's text. The example
checks three things for itself:

| Check | How | Katherine's run |
|---|---|---|
| **The outcome** | Read back from each live system: the Odoo applicant (stage, linked employee), Keycloak users with her email, Directus tasks with her `employee_ref` | `ok` ×4 |
| **The process** | Her calls were tapped with the same correlator, so her log is keyed by person. `check_trace` replays her trace against the mined model. | `conforms: True · fitness: 1.00` |
| **The policy** | The same `check_rules` as stage 4, on her log | `all rules pass` |

The script exits with `1` if any of them fails.

---

## 11. What is generic, and what the example adds

Everything in AgenticHarvest's `src/` is generic. The HR example adds only data and
a script:

| The example supplies | AgenticHarvest supplies |
|---|---|
| Which apps exist (via `setup.py` → `.env`) | Reading each app's API description into tools |
| `KEY_RULES`: where the email appears | `CompositeMapper`, `Correlator`, `OcelTap` |
| `ODOO_HISTORY`, `KEYCLOAK_HISTORY`, `DIRECTUS_HISTORY`: how each audit trail maps to events | `RecordMapping`, `import_records` |
| `POLICY`: the onboarding rules | `check_rules` and the rule kinds |
| `GOAL`: what the agent is for | `compile_plan`, `restrict_tools`, the agent loop |
| The coordinator's steps (`onboard_by_hand`) | The gateway, the gate, the checks, the client |
| — | Miners, `ProcessModel`, `check_trace`, `handoffs`, `object_centric_dfg` |

To mine your own systems, you write the left column; the right column is
already there.
