# Plan: OpenCode V2 server for AgentPool

Status: **implemented** on branch `feat/opencode-v2-server` (2026-11-20), see §7.
Goal: add a *new* protocol server that exposes AgentPool through the OpenCode **V2** HTTP API,
so the OpenCode V2 TUI / web / desktop clients can drive AgentPool agents.
The existing V1 server (`src/agentpool_server/opencode_server/`) stays as-is.
This document covers **only the concrete server task**. The broader architecture questions
(session/agent/subagent relationship, runtime dynamism, alignment with OpenCode and ACP) are in
`ARCHITECTURE_SESSIONS_AGENTS.md`. That document's migration steps 1–3 (session owns history,
explicit sessions in `run_stream`, child-session subagents) make this server much cleaner, but
the server can start without them (fallback: one agent runtime per session, see §4).

---

## 1. V1 → V2 API differences relevant to us

V1 was model-centric (`model` + `agent` sent with every message). Our V1 `GET /agent` route even has
the `model=` line commented out (`src/agentpool_server/opencode_server/routes/agent_routes.py:99`).
V2 makes agent and model **session state**. The conceptual discussion is in the architecture doc §8;
this table is the implementation-relevant diff:

| Concept | V1 | V2 | Fit for AgentPool |
|---|---|---|---|
| Agent / model selection | per message (`model`, `agent` in body) | **session state**: `Session.Info.agent?`, `Session.Info.model?`; changed via `POST /api/session/{id}/agent` and `/model`; prompt body has **no** agent/model fields | session = bound to one pool agent |
| Switch history | none | `agent-switched` / `model-switched` messages, `session.agent.selected` / `session.model.selected` events | maps to our agent switching |
| Agent schema | `agent` + deprecated `mode` maps | `Agent.Info {id, name, model?, request, system?, description?, mode: primary\|subagent\|all, hidden, color?, steps?, permissions}`. **`model` is optional** | non-native agents (Claude Code, Codex, ACP) can omit the model |
| Streaming | message/part update events | run-oriented events: `session.execution.*`, `session.step.*`, `session.text.{started,delta,ended}`, `session.reasoning.*`, `session.tool.{input.*,called,progress,success,failed}` | close to our `run_stream` events |
| Subagents | `task` tool, child sessions | `subagent` action, background child sessions reporting through a session **inbox** (`/api/session/{id}/inbox`, `session.inbox.*`) | fits our subagent/delegation toolsets |
| User questions | question routes | **forms** (`/api/session/{id}/form`, `Form.CreatePayload {title, fields}`, field kinds include choice/multiselect/string/…) + `Question` schema | maps to our `InputProvider` |
| Permissions | `permission` map | `/api/session/{id}/permission[/{requestID}/reply]`, ordered `{action, resource, effect}` rulesets, saved rules `/api/permission/saved` | maps to our tool confirmation |
| Interrupt / compact / background | partial | explicit `interrupt`, `compact`, `background` session endpoints | maps directly |
| Durable history | messages endpoint | `GET /api/experimental/session/{id}/log?after=&follow=` (replayable), plus `/message` | can be backed by our storage |
| Steering | none | prompt body `delivery: "steer" \| "queue"` | maps to message injection/queueing if we support it |

**What's still model-centric (friction to plan for):**

- `Agent.Info.request: Provider.Request` is **required**. Agents are still "prompt + permissions +
  steps on top of OpenCode's own loop", not opaque runtimes. We fill it with an empty or neutral
  request object.
- `Session.Message.Assistant` **requires** both `agent` and `model` (`Model.Ref {id, providerID, variant?}`).
  For agents without a selectable model, we need a synthetic `Model.Ref`, e.g.
  `providerID="agentpool", id=<agent_type or agent name>`.
- `model.list` / `provider.list` are still central in the UI. For non-native agents, model switching
  should either be hidden (return only the agent's synthetic model) or rejected cleanly.
- Permission rulesets assume OpenCode tool names (`shell`, `edit`, `subagent`, …), so mapping our
  tool confirmation onto them is approximate.
- The OpenCode "Harnesses" docs (Claude Code, Codex, Pi) are **not** relevant. They describe using
  OpenCode Console as an *inference gateway* and say nothing about hosting foreign agents.

---

## 2. Where the information lives

### Primary sources

- **OpenAPI spec (authoritative, committed in the repo):**
  `https://github.com/anomalyco/opencode/blob/<tag>/packages/protocol/openapi.json`
  (the repo moved from `sst/opencode` to `anomalyco/opencode`, default branch `dev`).
  - Fetch: `gh api "repos/anomalyco/opencode/contents/packages/protocol/openapi.json?ref=v2.3.0" -H "Accept: application/vnd.github.raw" > openapi.json`
  - At v2.3.0: OpenAPI 3.1.0, **139 paths, 501 schemas**, ~886 KB. Title "opencode HttpApi",
    described as *"Experimental HttpApi surface"*, so expect churn.
  - Release tags: the V2 line is `v2.x.y` (latest `v2.3.0`, 2026-11-13). `v1.19.x` is still released
    in parallel and is the "latest" GitHub release, so **don't use `releases/latest`**.
- **Schema sources (TypeScript, Effect Schema):** `packages/schema/src/*.ts` (agent.ts, session.ts,
  session-message.ts, session-event.ts, prompt.ts, prompt-input.ts, form.ts, question.ts,
  permission.ts, model.ts, provider.ts, event.ts, …). Read these when the OpenAPI spec is unclear.
- **Endpoint definitions:** `packages/protocol/src/groups/`, `packages/protocol/src/api.ts`.
- **Generated TS client (reference for the call patterns the UI uses):** `packages/client/`
  (`@opencode/client`, generated from the Effect `HttpApi` by `@opencode/httpapi-codegen`; see
  `packages/client/script/generate.ts`).
- **Server implementation (behaviour reference):** `packages/server/`.
- **Docs:**
  - API reference (rendered from the spec): https://opencode.ai/docs/api/
  - V1 → V2 migration: https://opencode.ai/docs/migrate-v1/
  - Agents config: https://opencode.ai/docs/agents/
  - Client: https://opencode.ai/docs/build/client/
  - Docs index: https://opencode.ai/docs/llms.txt
  - V1 server docs (for diffing): https://opencode.ai/docs/server/

### Python packages: nothing usable for V2

| PyPI package | State |
|---|---|
| `opencode-ai` | Official Stainless-generated SDK (`sst/opencode-sdk-python`, repo now 404). Last release `0.1.0a36` on 2025-08-27, so **V1 only, abandoned**. |
| `opencode-sdk` | Third party (fergusfettes), 0.2.0, 2025-10, V1. |
| `opencode-client` | Third party stub, 0.1.1, no description. |

OpenCode only generates TypeScript clients now (Promise and Effect flavours). **No Python SDK.**

### Our own V1 SDK

`src/opencode_sdk/` is our hand-written V1 model package (~4.1k lines in `models/`). It's used by:
- `src/agentpool_server/opencode_server/` (V1 server)
- `src/agentpool_storage/opencode_provider/` (reads OpenCode's on-disk storage)
- `src/agentpool/agents/claude_code_agent/` (one import)

Keep it untouched. V2 models go in a **separate** package (see §4).

---

## 3. Generating V2 models (spike already done)

A quick spike showed the spec can be code-generated, with caveats:

- `datamodel-code-generator` **latest** (0.9x) crashes on this spec: `TypeError: unhashable type: 'JsonSchemaObject'`.
- **`datamodel-code-generator==0.28.5` works**:
  ```bash
  uvx --python 3.13 --from "datamodel-code-generator==0.28.5" datamodel-codegen \
    --input openapi.json --input-file-type openapi \
    --output-model-type pydantic_v2.BaseModel --target-python-version 3.13 \
    --use-annotated --use-union-operator --output <dir>
  ```
  Result: 125 modules, ~1066 classes. Schema names are dotted (`Session.Info`, `Agent.ID`), so
  the generator builds a module tree (`Session/__init__.py` → `class Info`, …).
  `Session.Info` came out correctly (`agent: Agent.ID | None`, `model: Model.Ref | None`, `extra='forbid'`).
- **Problem:** the module tree has **circular imports** (e.g. `Integration` ↔ … at `MethodID`), so the
  package doesn't import as-is.
- Options to fix (pick one):
  1. Preprocess the spec: rewrite `components.schemas` keys from `Session.Info` to `SessionInfo`
     (and fix up `$ref`s) so everything lands in a **single module**. Single-module output avoids
     cycles because forward refs resolve inside one namespace. *Recommended.*
  2. Post-process the generated tree with `from __future__ import annotations` plus `model_rebuild()`.
  3. Hand-write only the subset we need, as we did for V1. More work, but the cleanest API.
- Recommendation: a script `scripts/generate_opencode_v2_models.py` that downloads the spec for a
  pinned tag, flattens names (option 1), runs the generator, and writes to `src/opencode_v2_sdk/models.py`
  (or `src/opencode_sdk/v2/`). Commit the generated file and the pinned spec version, so we can diff
  on upgrades.
- The CLAUDE.md rules ("maximum type safety", no getattr/hasattr) apply. The generated models use
  `extra='forbid'`, which is good for catching drift in tests.

---

## 4. How it fits our architecture

### Placement

```
src/opencode_v2_sdk/                 # generated V2 pydantic models (+ maybe a thin httpx client later)
src/agentpool_server/opencode_v2_server/
    server.py          # FastAPI app, inherits BaseServer (agentpool_server/base.py)
    state.py           # per-server state: pool, session registry, event bus
    events.py          # agentpool run_stream events -> V2 session.* events (SSE)
    converters.py      # ChatMessage <-> Session.Message.*, agents -> Agent.Info, models -> Model.Ref
    input_provider.py  # InputProvider -> forms / permission requests
    routes/            # one module per V2 group: session, agent, model, provider, permission, form,
                       # event, fs, project, location, command, skill, mcp, pty, vcs, config, info, ...
src/agentpool_cli/     # new `serve-opencode-v2` command (next to serve-opencode)
```

Also register the new package in `pyproject.toml` `module-name` (around line 480, where `opencode_sdk`
is listed).

### Core mapping decisions

1. **The server is pool-wide, not single-agent.** V1 `ServerState` holds a single `agent: BaseAgent`
   (`opencode_server/state.py`). In V2 every session carries its own agent:
   - `GET /api/agent` → all `pool.all_agents`, each as `Agent.Info`.
     `mode="primary"` for user-facing agents, `"subagent"` for agents only reachable via delegation,
     `hidden` for internal ones. `model` is set only for native agents with a known model.
   - `POST /api/session` → creates a session bound to the default agent (`pool.main_agent`).
   - `POST /api/session/{id}/agent` → rebinds the session to another pool agent. Emit
     `session.agent.selected` and store an `agent-switched` message.
   - Reference for per-session agent binding: the ACP server (`agentpool_server/acp_server/session_manager.py`,
     `session.py`, `switch_active_agent` around line 400; `run_stream(..., session_id=...)` around line 485).
   - **Caveat / interim:** today the agent instance holds a single `self.session_id` and
     `self.conversation` (`agentpool/agents/base_agent.py:236,624-632`). Two sessions bound to the same
     pool agent therefore share one instance and can collide, and the ACP server has the same limitation.
     Until the architecture refactor (`ARCHITECTURE_SESSIONS_AGENTS.md` §6, steps 1–3) lands, either
     serialise turns per agent instance (lock) or pass the session's history explicitly via the
     `message_history` parameter (`base_agent.py:710`). Don't build new workarounds on top of
     `self.conversation`.
2. **Model handling:**
   - Native agents: `GET /api/model` lists models from our provider discovery (reuse
     `_get_model_providers` logic from `opencode_server/routes/config_routes.py`).
     `POST /api/session/{id}/model` calls the agent's model switch.
   - Non-native agents: a synthetic `Model.Ref(providerID="agentpool", id=<agent name/type>)`.
     Reject model switches with a typed error (see `packages/protocol/src/errors.ts`), or expose
     only the models the agent supports (Claude Code and Codex agents may expose their own lists).
3. **Prompt → run:** `POST /api/session/{id}/prompt {text, files, agents, skills, metadata, delivery, resume}`
   → `agent.run_stream(...)`. `files` maps to our attachments. `agents` (agent mentions) could map to
   delegation or a team. `delivery: "steer" | "queue"` maps to injecting into or queueing behind a
   running turn.
4. **Event stream (`GET /api/event`, `text/event-stream`):** convert `agentpool.agents.events`
   (`src/agentpool/agents/events/events.py`) to V2 events:

   | AgentPool event | V2 event(s) |
   |---|---|
   | run start / end / error / cancel | `session.execution.started` / `.succeeded` / `.failed` / `.interrupted`, `session.status`, `session.idle` |
   | model request (step) start/end | `session.step.started` / `.first-token` / `.ended` / `.failed` |
   | `PartDeltaEvent` (text) | `session.text.started` / `.delta` / `.ended` |
   | thinking deltas | `session.reasoning.started` / `.delta` / `.ended` |
   | `ToolCallStartEvent` / args streaming | `session.tool.input.started` / `.delta` / `.ended`, `session.tool.called` |
   | tool progress | `session.tool.progress` |
   | `ToolCallCompleteEvent` | `session.tool.success` / `.failed` |
   | usage | `session.usage.recorded` / `.updated` |
   | compaction | `session.compaction.*` |
   | subagent spawned / result | child `session.created`, `session.inbox.enqueued` / `.delivered` |

   Reuse the structure of `agentpool_server/acp_server/event_converter.py` (a stateful converter) and
   `opencode_server/stream_adapter.py`. The docs call `/api/event` *volatile* (slow consumers are dropped,
   no replay), so the durable `/experimental/session/{id}/log` must be served from storage.
5. **Messages / history:** `GET /api/session/{id}/message` maps our `ChatMessage` storage to the
   `Session.Message.*` union (`user`, `assistant`, `system`, `synthetic`, `shell`, `skill`,
   `compaction`, `agent-switched`, `model-switched`, …) via `StorageManager`
   (`agentpool/storage/`). The storage serialization was just touched
   (`agentpool/storage/serialization.py`), so check that the needed metadata (agent name, model) is persisted.
6. **Questions and permissions:**
   - Tool confirmation → `permission.asked` + `POST .../permission/{requestID}/reply`.
   - Structured user input (`InputProvider.get_input` / `get_structured_input`, `agentpool/ui/base.py`)
     → `Form.CreatePayload` + `form.created`, answered via `.../form/{formID}/reply`.
   - The V1 server has the same split in `opencode_server/input_provider.py`
     and `state.py` (`PendingQuestion`), so port those patterns.
7. **Everything else** (fs, pty, vcs, project, worktree, mcp, skills, commands, config) can be ported
   from the V1 routes. Most are unchanged in spirit but moved under `/api/` with new schemas.
   - Note: V2 OpenCode **no longer runs LSP**. Skip LSP routes.
8. **Auth:** HTTP Basic, username always `opencode`, password from `OPENCODE_PASSWORD`
   (`OPENCODE_SERVER_PASSWORD` is still accepted).

---

## 5. Suggested implementation order

1. Models: generation script, flattening, generated `src/opencode_v2_sdk/`. Add a smoke test that
   validates fixture JSON from the spec examples, if there are any.
2. Server skeleton: `BaseServer` subclass, `/api/info`, `/api/event` SSE bus, Basic auth, CLI command.
3. Agents and models: `/api/agent`, `/api/model`, `/api/provider`, with the synthetic model refs.
4. Sessions CRUD plus agent/model switch endpoints, backed by storage.
5. Prompt → run_stream → event conversion (the core). Test with `TestModel`.
6. Messages and the durable log from storage.
7. Permissions and forms via the input provider.
8. Interrupt, compact, fork, revert, inbox and subagents.
9. Peripheral routes (fs, pty, vcs, project, worktree, mcp, skills, commands, config).
10. Manual test with the real V2 TUI / web client pointed at our server.
    Open question: how to point the V2 TUI at an external server URL instead of its auto-started
    background service. Check `opencode --help` (V2) and the `packages/client/src/promise/service.ts`
    / `service-contender.ts` logic.
11. Write an `ENDPOINTS.md` checklist like the V1 one, generated from the spec's path list.

---

## 6. Open questions

- How does the V2 client discover or attach to a non-local server (see step 10)?
- Can a prompt override agent/model per call? Per the v2.3.0 spec, **no**: the body is only
  `{id, text, files, agents, skills, metadata, delivery, resume}`, and agent/model are session state.
- How strictly does the UI depend on `/api/rpc/{rpcID}/{method}` (plugin RPC)? Probably stub it.
- The spec calls itself experimental (version `0.0.1`). Pin a tag and re-diff on every upgrade.
- Should teams be exposed as agents (`Agent.Info` with `mode: primary`)? Likely yes, since they're
  `MessageNode`s too.

---

## 7. Implementation status (branch `feat/opencode-v2-server`)

Decisions (2026-11-20): server first with stopgap (per-agent lock + session re-binding),
generated models, pinned to **v2.3.2**, scope ≈ parity with our V1 server.

Done:
- `scripts/generate_opencode_v2_models.py` → `src/opencode_v2_sdk/models.py` (pinned spec in
  `schema/opencode-v2/openapi.json`). Normalizer: flattened names, hoisted inline objects,
  inlined primitives (+ `type` aliases), free-form objects → dicts, discriminators, AST fix for
  list discriminators.
- `BaseAgent.start_new_session()` (+ overrides for Claude Code, Codex, ACP, AG-UI).
- `opencode_v2_server/`: `events.py` (builder), `bus.py` (SSE fan-out), `projector.py`
  (port of message-updater + inbox), `session.py` (event-sourced session, seq, followers,
  persistence), `event_store.py` (JSONL per session in DATA_DIR/opencode-v2/sessions),
  `run_converter.py` (run_stream → step/text/reasoning/tool events), `history.py`
  (ChatMessage replay for sessions without log), `catalog.py` (agents/models/providers),
  `interactions.py` (permissions + forms, input provider), `prompt_input.py`, `runner.py`
  (inbox drain, steer via inject_prompt, interrupt, AgentBinder), `location.py`, `state.py`.

Reference checkout: `~/dev/oss/opencode-v2.3.2` (git worktree of `~/dev/oss/opencode` at v2.3.2).

Also done (2026-11-20):
- Routes for all 173 spec operations (`routes/`: server, agents, sessions, session_ops,
  interactions, files, runtime, pty, misc). `test_every_spec_operation_has_a_route` guards this.
- Implemented: sessions (CRUD, agent/model switch, prompt/synthetic with steer/queue, interrupt,
  inbox, messages + cursors, context, durable log SSE, fork, move, compact, revert stage/commit,
  diff, generate, environment, instructions entries, stats, export/import), permissions + saved
  rules, forms, fs, vcs (git), worktrees, commands (slash commands), skills, MCP status/resources,
  shells, jobs (session shells), PTY (exxec + OpenCode ws protocol), `serve-opencode-v2` CLI.
- Answered with empty lists / typed 503 (no AgentPool counterpart): plugins + plugin RPC,
  integrations, credentials, text/image/video/decision generation, voice, client MCP, MCP
  add/remove, persistent PTYs, media/blobs, automations, artifacts, references, web search,
  usage limits, pairing, fs watch, heap dumps.
- Verified with the real OpenCode v2.3.2 CLI (`opencode run --server`, `session list`, `models`)
  and the TUI (driven via pty): startup calls all 200, prompt renders tool + text steps.
- Tests: `tests/servers/opencode_v2_server/` (28 tests).

Known limitations:
- Interim agent binding: turns on one agent instance are serialized; Claude Code/Codex native
  history can't be truncated by revert/fork (only AgentPool storage is).
- Subagents run inside the delegating tool call (shown as tool progress), not as V2 child
  sessions with inbox reports.
- Our tool names (bash, read, ...) use OpenCode's generic tool renderer.
