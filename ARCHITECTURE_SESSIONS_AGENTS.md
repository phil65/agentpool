# Architecture: Sessions, agents, subagents, runtime dynamism, and protocol alignment

Status: **thinking document, no decisions made** (prepared 2026-11-17; §10 added 2026-11-20 with
pain points found while implementing the OpenCode V2 server).

Scope: the long-standing "how do session, agent and subagent relate" question, what may change at
runtime, and how far to align our core model with the primitives of OpenCode V2 and ACP.
The concrete OpenCode V2 server task is in `OPENCODE_V2_PLAN.md`. It benefits from this refactor but
doesn't strictly require it.

Contents: 1 Background · 2 Current state · 3 Core model · 4 Subagents and session relationships ·
5 Static vs dynamic · 6 Migration sketch · 7 Open decisions · 8 Alignment with OpenCode V2 and ACP (8.1 OpenCode's Claude Agent integration, 8.2 options for us) ·
9 Object model sketch · 10 Pain points from the OpenCode V2 server

---

## 1. Background

AgentPool started with AutoGen-style **agent-to-agent communication**: agents are long-lived actors
with their own memory, wired together with connections (`add_connection`), and composed into teams
(`|`, `&`). Since then the framework has moved towards a **user/session-centric** model: a user (via
ACP, OpenCode, AG-UI, …) opens a session, talks to an agent, and that agent delegates to subagents.

Both models currently share one object, `BaseAgent`, and that causes most of the open questions:

- What *is* a session: the agent's memory, or something the agent is attached to?
- Does a subagent have state? Whose? Does it get its own session?
- What happens when the agent of a session changes?
- What's allowed to change while things run (new agents, changed definitions, new tools)?

---

## 2. Current state (evidence in code)

| Where | What it shows |
|---|---|
| `src/agentpool/agents/base_agent.py:236` | `self.conversation = MessageHistory(storage=self.storage)`: **conversation state lives on the agent instance** |
| `src/agentpool/agents/base_agent.py:592-632` (`run_stream(session_id=...)`) | the agent instance holds **one** `self.session_id`, and passing another ID *re-points the instance*. Two sessions using the same pool agent therefore collide. |
| `src/agentpool_server/acp_server/session.py:411` | commented-out `pool.get_agent(agent_name, deps_type=ACPSession)`, an earlier attempt at a per-session instance. Switching now just assigns the shared pool instance (`self.agent = agents[agent_name]`). |
| `src/agentpool/agents/base_agent.py:710,731` | `message_history` param can override `self.conversation`, an escape hatch for external state |
| `src/agentpool_toolsets/builtin/subagent_tools.py:221` (`task`) | runs `node.run_stream(...)` on the **shared pool instance**, so all callers share that subagent's conversation and parallel calls interleave into one history |
| `src/agentpool_toolsets/builtin/subagent_tools.py:177` (`list_available_nodes(only_idle=...)`) | "busy" is a property of the *agent*, which only makes sense if agent = stateful singleton |
| `src/agentpool_toolsets/builtin/workers.py:78-91` | swaps histories in and out (`set_history` / restore) to fake isolated runs, a workaround for state living in the wrong place |
| `src/agentpool/sessions/models.py:55` (`SessionData`) | already has `session_id`, `agent_name`, `parent_id`, `agent_type`, `sdk_session_id`, `cwd`, so the **data model is already halfway session-centric** |
| `src/agentpool/delegation/pool.py:514` (`add_agent`) | the pool is mutable at runtime, but nothing defines what that means for running sessions |
| `src/agentpool_server/acp_server/session_manager.py`, `session.py` | the ACP server already binds sessions to agents. It's the closest thing to the target model today. |

---

## 3. Proposed core model: split the agent into three things

| | What it is | Owns | Lifetime |
|---|---|---|---|
| **AgentDefinition** | config: prompt, tools, model, type, permissions | nothing mutable | registry (pool) or session scope (see §5) |
| **Session** | the conversation | history, cwd, bound agent name and **definition version**, `parent_id`, metadata, `sdk_session_id` | storage, resumable |
| **AgentRuntime** | a live executor attached to *one* session | native: PydanticAI loop + deps; Claude Code: subprocess + native session; ACP: connection + remote session | while the session is active, can be recreated from Session + Definition |

Invariants:

1. **Conversation state lives only in the Session.** Runtimes may cache, but the Session is the truth.
   (External agents have their own native transcript. The Session holds the `sdk_session_id` pointer
   plus our mirrored history.)
2. **A runtime serves one session.** No sharing, which makes "busy" a property of a session or runtime.
3. **Definitions are values.** They can be replaced in the registry but never mutated under a running
   turn (see §5).

Backwards compatibility: `pool.get_agent(name).run(...)` keeps working as "run in that agent's
**default session**". `agent.conversation` becomes a view onto the default session's history.

---

## 4. Subagents and the relationship between sessions

**Every delegation creates or resumes a child session** (`parent_id` = caller's session). Three variants:

| Variant | Semantics | API sketch |
|---|---|---|
| Stateless (default) | new child session, empty history | `task(agent, prompt)` |
| Forked context | new child session seeded with (part of) the parent's history; replaces `pass_message_history` in workers | `task(agent, prompt, context="fork")` |
| Stateful / continued | **resume** an existing child session by ID. Statefulness belongs to the *session handle*, not the agent | `task(agent, prompt, session_id=...)`, where `task` returns the child session ID |

Consequences:
- Parallel calls to the same subagent are safe (separate sessions).
- `only_idle` and history swapping disappear.
- UIs can show child sessions as a tree (OpenCode V2 children + inbox, ACP sessions, AG-UI threads).
- Async mode (`async_mode=True`) becomes "child session runs in the background, result is delivered to
  the parent's inbox" instead of writing to `/tasks/{id}/output.md`. The file output can remain a
  convenience view.

**Switching the agent within a session:**
- Native → native: attach a new runtime to the existing history and record an `agent-switched` marker.
- To or from an external agent (Claude Code, Codex, ACP): it can't load history it didn't write.
  Our Session stays the source of truth, and the runtime keeps the native session **in sync** using
  checkpoints, catch-up and forks (§8.1: the scheme OpenCode V2 uses for its Claude Agent provider).
  Record a marker either way. Switching still has a fidelity cost (foreign turns arrive as quoted text,
  or need transcript synthesis, §8.1), which is why **session-bound-to-agent is the default** and
  switching is the exception.

**AutoGen-style connections and teams stay as a layer on top:**
- A connection `A → B` means "deliver A's output into a session of B". It needs a per-connection policy:
  `new_session_per_message` | `standing_session` (one per (source, target) pair) | `target_default_session`.
- Teams are coordinators that create child sessions for their members. Sequential = chain of child
  sessions; parallel = sibling child sessions. A team run is itself a session (a node in the tree).

---

## 5. Static vs dynamic: what may change at runtime?

This is the second axis, independent of §3–4.

### The two poles

| | **Harness-style** (Claude Code, OpenCode, Codex) | **Auto-exploration** (AutoGen roots, current `list_available_nodes` + `task`, `pool.add_agent`) |
|---|---|---|
| Subagent set | fixed definitions, known at session start | discovered at runtime by tool call, pool can grow or shrink |
| How the model learns about them | static: names and descriptions in the tool schema or system prompt | dynamic: the model has to call a discovery tool |
| Definition changes | reload between sessions (sometimes between turns) | anytime |
| Pros | prompt caching works, reproducible and resumable, permissions can be reviewed up front, UI can list agents | flexible, agents can create specialised agents, open-ended exploration |
| Cons | rigid | cache busting, non-deterministic resume, unclear permission scope, races with running sessions |

There are really **four separate things** that can change, and they need different answers:

1. **Registry membership**: which definitions exist (`pool.add_agent`, removal)
2. **Definition content**: prompt, tools, or model of an existing agent changes (config reload, UI edit)
3. **Session tool or agent visibility**: what a *running* session can see or call
4. **Runtime binding**: which agent a session is attached to (switching, §4)

### Proposed rules

**R1: Changes take effect only at turn boundaries.** Never mid-turn. A running turn uses the snapshot
it started with.

**R2: Sessions record the definition version they ran with.** Definitions get a content hash or
version. Session (and child session) metadata stores `agent_name` + `definition_version`.
On resume with a changed definition, a policy applies (`follow_latest` with a marker message, or
`pinned`). This makes resume deterministic and explainable.

**R3: Two definition scopes.**
- **Pool scope**: from YAML or explicitly registered. Shared, listable by UIs, the "harness" agents.
- **Session scope**: agents created *at runtime by an agent* (auto-exploration). They live in the
  session that created them, are visible to it and its children, and disappear with it unless
  explicitly **promoted** to pool scope. This allows dynamic creation without polluting the global
  registry or other users' sessions.

  The set visible to a session = pool scope ∪ own session scope ∪ ancestors' session scope,
  snapshotted per turn.

**R4: Static advertisement plus append-only announcements.**
- At session start: advertise the visible agents statically (tool schema enum or system prompt). This
  is cache-friendly and harness-style.
- Agents added later are announced with an **appended synthetic message** ("new agent X available:
  …"), not by rewriting the system prompt or tool schema. That keeps the prompt cache prefix stable.
  (This is how Claude Code handles deferred tools and system reminders.)
- `list_available_nodes` remains for exploration but is no longer the only discovery path.

**R5: Push changes to clients.** Registry changes emit events, which every protocol has a slot for:
OpenCode V2 `agent.updated` + `GET /api/agent`; ACP session mode / config option updates.
UIs must not keep stale agent lists.

**R6: Permissions are evaluated against the snapshot.** An agent added at runtime does not inherit
broader permissions than its creator: session-scoped definitions are capped by the creator's
permissions.

### Nested harnesses (external agents with their own subagents)

Claude Code, Codex, and OpenCode agents have **their own internal subagents** (e.g. Claude Code's Task
tool, with sidechain transcripts). Those aren't pool members and we don't control them. Two levels
need to stay distinct:

- **Pool-level delegation**: our `task` tool, our child sessions, governed by the rules above.
- **Harness-internal delegation**: opaque to the pool. At most, **mirror** it as read-only child
  sessions for UI display (we already see the events), flagged `origin: harness-internal`.
  Never resumable or addressable from the pool.

---

## 6. Migration sketch (incremental)

1. Introduce a `Session` runtime object (on top of `SessionData`) that owns `MessageHistory`. Give
   `BaseAgent` a default session, and make `agent.conversation` delegate to it. No behaviour change.
2. Make `run` / `run_stream` accept a session (or session ID). Servers (ACP first, it's closest)
   pass explicit sessions.
3. Rework `task`: child sessions, return the child session ID, accept `session_id` to continue.
   Remove history swapping in `workers.py` and `only_idle`.
4. Definition versioning (content hash) plus recording it in session metadata.
5. Session-scoped definitions plus promotion, with the append-only announcement mechanism.
6. Connections and teams: per-connection session policy.
7. Runtime factory: `AgentDefinition + Session → AgentRuntime`, so one definition can serve N sessions
   concurrently. This is the big one for native agents (cheap) and external agents (one process or
   native session each).

Steps 1–3 already unblock a clean OpenCode V2 server (session ↔ agent binding, child sessions,
inbox). The V2 server now exists with stopgaps for exactly these gaps. §10 lists them, with the
code to remove once each step lands.

---

## 7. Open decisions (for when there's headspace)

- Default resume policy when the definition changed: `follow_latest` or `pinned`?
- Default for `task`: stateless (like Claude Code / OpenCode) or continue-by-default?
- Connection session policy default: probably `standing_session` (closest to today's behaviour).
- Should session-scoped agents be allowed at all for external agent types, or only native ones?
- Runtime pooling for external agents: one process per session (simple, expensive) vs multiplexed
  sessions over one ACP connection (ACP supports multiple sessions per connection).
- Teams as sessions: is a team run a session with its own history (a summary of members) or only a
  container node in the tree?
- How far to go with backwards compatibility for `agent.conversation` and implicit-memory `agent.run()`.
- How far to align with OpenCode V2 / ACP vocabulary (§8): adopt their names (inbox, delivery,
  fork, config options) internally, or keep our own and map at the server boundary?

---

## 8. Alignment with OpenCode V2 and ACP

### Why this matters

Both protocols moved towards the target model of §3–5 on their own: **the session is the unit**, the
agent and model are session state, and subagents are child sessions. Each protocol server today
contains glue that works around our agent-centric internals (the V1 OpenCode server was the extreme
case: a model-centric protocol on top of an agent framework). The closer the core model is to the
protocols' shared primitives, the thinner every server becomes.

### Primitive comparison

| Primitive | AgentPool today | Target (this doc) | OpenCode V2 | ACP (incl. RFDs in `acp_protocol/rfds/`) |
|---|---|---|---|---|
| Unit of conversation | agent instance (`self.conversation`, `self.session_id`) | Session | Session (`Session.Info`) | Session (`session/new`, `session/load`) |
| Agent selection | shared pool instance | session → definition (+ version) | `Session.Info.agent`, `POST /session/{id}/agent`, `agent-switched` message | one agent per connection. Agent choice is expressed as **session modes** or **config options**; we already map pool agents to modes (`acp_server/converters.py:232` `agent_to_mode`) |
| Model selection | agent attribute | runtime option of a definition | `Session.Info.model`, `POST /session/{id}/model`, `model-switched` message | **session config option** (category model, `session/set_config_option`), RFD `session-config-options.mdx` |
| Agent catalogue | `pool.all_agents`, `list_available_nodes` | pool scope ∪ session scope, versioned | `GET /api/agent` (`Agent.Info`, `mode: primary\|subagent\|all`, `hidden`), `agent.updated` event | modes / config options list, updated via session update notifications |
| Subagents | `task` on shared instance, workers swap history | child sessions (stateless / fork / continue) | background child sessions (`parentID`), results via **inbox** | no first-class primitive. RFD `session-fork.mdx` names subagents as a use case; RFD `proxy-chains.mdx` lets proxies create subagents by opening new sessions |
| Fork | — | "forked context" child session | `POST /session/{id}/fork` (`before` param) | RFD `session-fork.mdx` (`session/fork`); we already advertise it (`acp_server/acp_agent.py:240`) |
| Resume / list | storage + `load_session` | Session is the source of truth | `GET /api/session`, durable `/experimental/session/{id}/log` | RFDs `session-resume.mdx`, `session-list.mdx`; we advertise both (`acp_agent.py:224-225`) |
| Steering / queueing | staged content injected by `run_stream` | explicit delivery mode | prompt `delivery: "steer" \| "queue"`, inbox events | — (only `session/cancel`, `$/cancel_request`) |
| User input | `InputProvider` | unchanged, per session | **forms** + permission requests | `session/request_permission` (+ elicitation via extensions) |
| Event model | `run_stream` events | unchanged | `session.execution/step/text/reasoning/tool.*` | `session/update` notifications (agent message chunks, tool calls, plan, usage) |
| Runtime changes | undefined | turn boundaries, versioned definitions, append-only announcements (§5) | `agent.updated`, `config.updated` events; changes apply to "subsequent provider turns" | `current_mode_update`, `config_option_update`, `available_commands_update` notifications |

Observations:
- **OpenCode V2 is a superset of what we need** for session trees: child sessions, inbox,
  delivery modes, fork, durable log. Its vocabulary is a good candidate for our internal names.
- **ACP is deliberately minimal**: one agent per connection, no subagent primitive. Agent selection
  has to be squeezed into modes or config options (which we already do). Subagents can only be
  surfaced as separate sessions (via `_meta` and extensions) or through the proxy-chain model.
- Both treat "applies from the next turn" as the change boundary, which matches rule R1 in §5.

### Alignment options

1. **Adopt the vocabulary internally** (recommended for the session layer): `Session` with `parent_id`,
   `fork(before=...)`, an `inbox` with `delivery: steer | queue`, `agent-switched` / `model-switched`
   markers in history. Servers then map nearly 1:1 to OpenCode V2, and ACP gets a subset.
2. **Keep our names, map at the boundary**: less churn now, but every server keeps its own glue, and
   semantics drift between servers.
3. **Model selection as a config option of the runtime** (ACP style) rather than a first-class agent
   field. This fits heterogeneous agents: native agents expose a model option, Claude Code exposes its
   own models, ACP agents forward theirs, and some agents expose none. OpenCode V2's required
   `Model.Ref` is then a server-side projection (synthetic ref where the agent has no model option).

### Where we deliberately differ

- **Heterogeneous runtimes.** In OpenCode, an agent is a prompt/permissions preset on *their* loop. For
  us, an agent can be a whole foreign harness (Claude Code, Codex, Goose via ACP). Definition ≠ loop,
  which is why §3 separates AgentRuntime.
- **Pool-level delegation across runtimes.** A native agent delegating to a Claude Code agent, which
  delegates internally again (nested harnesses, §5). Neither protocol models cross-runtime session
  trees. We mirror them, using `_meta` for ACP and `parentID` + metadata for OpenCode.
- **AutoGen-style connections and teams** have no counterpart in either protocol. They stay an
  AgentPool-only layer that *produces* sessions (§4), so they show up in UIs as ordinary session trees.

### 8.1 How OpenCode V2 integrates an external agent (Claude Agent SDK)

Source (tag `v2.3.0` of `anomalyco/opencode`): design spec `specs/v2/claude-agent-provider.md`, code in
`packages/core/src/plugin/provider/claude-agent/` (`sync/`, `claude/fork.ts`,
`language-model/convert-to-claude-message.ts`), user docs `services/site/src/docs/content/claude-agent.mdx`.
It is OpenCode's **only** integration of this kind. There's no Codex provider, and the ACP code in
`packages/cli/src/acp/` makes OpenCode an ACP *agent*, not a client hosting other agents.

**Philosophy: the external agent is demoted to a model provider.** *"all of opencode UX stays the
same, and claude code becomes another provider."* Claude Code's built-in tools, settings, CLAUDE.md,
hooks, its MCP servers, skills, auto compaction, memory, plan mode and its subagents are all **off**.
OpenCode's tools are offered over MCP (`mcp__opencode__*`) and executed by OpenCode, so there is one
permission system. What remains of Claude Code is its system prompt, login (subscription), retries
and transport. The cost, which their spec admits: no Claude Code subagents, agent teams, memory, auto
mode, etc. This is the **opposite** of AgentPool, which keeps the external harness intact as an agent.

**Dual state, synced by fingerprint checkpoints:**
- OpenCode's DB is the source of truth. Claude's replies are written there from the stream. Claude's own
  native session is *also* saved, so two transcripts exist.
- Per session, the plugin stores a sync record (`sync/record.ts`):
  `{ claudeSessionID, history: [{ hash, claudeMessageID? }], interrupted? }`. Each message group
  (user / assistant / tool) is fingerprinted as the SHA-256 of its canonical visible content,
  **excluding reasoning** (`sync/fingerprint.ts`).
- Before each turn, `sync/decide.ts` compares the current history against the saved checkpoints:

  | Decision | When | Action |
  |---|---|---|
  | `fresh` | no record / nothing matches | send the whole history (as a checkpoint block, below) |
  | `continue` | only new user input since Claude's last turn | send it normally |
  | `catch-up` | other models answered meanwhile | send everything missed as **one user message** wrapped in `<conversation-checkpoint>` |
  | `changed` | edit / undo / history rewritten by a plugin | **fork** Claude's native session at the last matching checkpoint (`claude/fork.ts`, SDK `forkSession(id, {upToMessageId})`), never between a tool call and its result (`boundary()`). If no safe point exists, rebuild fresh. |
- Interrupts: Claude's own partial reply wins, and the next turn continues Claude's native session.
- **How foreign turns are rendered** (`convert-to-claude-message.ts`): *"Claude can't be given earlier
  turns, so their roles become text tags."* Every foreign message becomes text inside the one user
  message: `<user>…</user>`, `<assistant>…</assistant>`, tool calls as
  `<tool-call>{"id","name","input"}</tool-call>` and results as `<tool-result>{…}</tool-result>`.
  Thinking is dropped. Images and PDFs stay real content blocks.
- **They do not write Claude's JSONL transcripts.** Fork goes through the SDK's session API
  (`getSessionMessages`, `forkSession`, `deleteSession`), and they verify that the fork preserved the
  prefix byte-for-byte before mapping checkpoints onto the new message UUIDs.
- Processes: one long-running `claude` per session (the SDK can't multiplex sessions), stopped after
  5 min idle, resumed from the saved native session. It restarts when the tools or system prompt change.

**So yes, OpenCode can continue an OpenAI-started conversation with Claude, but only as a quoted
transcript**: Claude sees a single user turn containing tagged prior history, not real assistant turns
or real `tool_use` / `tool_result` blocks. Once Claude has replied, its own turns are native from then on,
and later switches only send the delta.

### 8.2 Options for AgentPool's external runtimes

The Agent SDK accepts only user input on its streaming interface, with no way to inject assistant turns
(the same limitation noted above). That leaves three tiers for seeding a native session with foreign
history:

1. **Quoted transcript in a user message** (OpenCode's choice). Portable, uses only the public API, works
   for every external agent (Claude Code, Codex, ACP agents). Lossy: the model knows it's quoted
   history, prior tool calls aren't real tool calls, and there's no prompt-cache continuity for
   that prefix.
2. **Transcript synthesis: write a native transcript file, then resume it.** For Claude Code this means
   writing a session JSONL (entries with `uuid` / `parentUuid` chains, `user` / `assistant` message
   records) and starting the SDK with `resume=<id>`. This is **the same mechanism the SDK's own
   `fork_session` uses**. See our port: `clawd-code-sdk/src/clawd_code_sdk/_internal/session_mutations.py`
   (`fork_session` copies transcript entries into a new JSONL with remapped UUIDs, in the host process,
   without the CLI). So it's less hacky than it looks, but still an **unpublished format** that can change
   with Claude Code versions. Constraints to verify:
   - thinking blocks carry signatures and can't be fabricated, so omit thinking from synthesized turns;
   - `tool_use` / `tool_result` IDs must pair up, and tool names in history should exist in the
     resumed session's tool set (or be mapped / rendered as text). Needs testing against the API;
   - required metadata fields (`sessionId`, `cwd`, `version`, timestamps, `message.id` / `model` on
     assistant entries) must look plausible to the CLI's loader;
   - only practical for agents whose transcript format we control or track (Claude Code via our SDK
     port; Codex rollout files would be the analogue).
3. **Fork at the last common point** (native fork API) for edit / undo within the agent's *own*
   history, plus tier 1 or 2 for whatever came after. This is OpenCode's `changed` path.

Recommendation: implement the **sync record + decide (fresh / continue / catch-up / changed)** as a
generic part of the external `AgentRuntime` (§9.3), with a pluggable "seeder" per runtime type:
`quoted` (default, all runtimes) and `synthesized` (opt-in, Claude Code first, built on the
`session_mutations.py` patterns and guarded by a Claude Code version check). The fingerprint
scheme (canonical JSON, reasoning excluded, grouped by role) can be copied almost verbatim.

### Further possibilities

- **AgentPool as ACP proxy/conductor** (RFD `proxy-chains.mdx`): the pool sits between an ACP client
  and downstream agents, injecting context and tools, and opening sessions on downstream agents as
  subagents. This is close to what the ACP server + ACP agents already do, and it fits the session-tree model.
- **Upstream contributions**: a subagent / child-session notion for ACP (building on fork + `_meta`)
  would be a natural RFD from this work.
- **Shared session store**: if Session becomes the source of truth, the OpenCode V2 durable log, ACP
  `session/load` / `session/list`, and our storage providers (`agentpool_storage/*`) can all read the
  same records.

---

## 9. Object model sketch

### 9.1 Config vs Definition: the same thing, or another layer?

**Mostly the same, so no parallel hierarchy.** The per-type config models in `src/agentpool/models/`
(`agents.py` `NativeAgentConfig`, `claude_code_agents.py`, `codex_agents.py`, `acp_agents/`,
`agui_agents.py`, `file_agents.py`) already have the properties a definition needs:
- **frozen values**: `NodeConfig` has `frozen=True` (`src/agentpool_config/nodes.py:43`);
- per-type discriminated union (`AnyAgentConfig`);
- an existing instantiation path (`from_config`, e.g. `agents/native_agent/agent.py:309`).

A second per-type hierarchy (`NativeAgentDefinition`, `ClaudeCodeAgentDefinition`, …) mirroring the
configs field by field would recreate exactly the duplication we're trying to remove.

**But config alone isn't enough.** It's YAML-shaped, so it can't hold values that exist only in Python:

| Python-only value | Today | Config field |
|---|---|---|
| callable tools (`Agent(tools=[greet])`) | ctor kwarg | `tools: Sequence[AnyToolConfig \| str]`, import paths only |
| `Model` instances | ctor kwarg | `model: AnyModelConfig \| ModelId \| str` |
| `deps_type`, Python output types | `from_config(deps_type=...)` | `output_type` is schema-based |
| history processors as callables | ctor | import paths |

Also, `from_config` today mixes three kinds of parameters that belong to different layers:

| `from_config` parameter | Belongs to |
|---|---|
| `config` | Definition |
| `deps_type` | Definition (Python extras) |
| `agent_pool` | Runtime environment (the pool attaches runtimes) |
| `input_provider` | **Session** (it's per client connection, e.g. the ACP/OpenCode server's provider) |
| `event_handlers` | Session / runtime (who listens to this run) |

**Recommendation: one thin, generic wrapper, not per type.**

```python
@dataclass(frozen=True)
class AgentDefinition[TDeps = None, TOut = str]:
    config: AnyAgentConfig                 # serialisable, frozen, per-type (existing models)
    extras: PythonExtras[TDeps, TOut]      # non-serialisable additions (callable tools, Model
                                           # instances, deps_type, output_type, processors)
    scope: Literal["pool", "session"]      # §5 R3
    version: str                           # content hash of config + stable identity of extras
                                           # (import path / qualname for callables)

    @property
    def name(self) -> str: return self.config.name
```

- YAML agents have empty `extras`. Python-constructed agents (`Agent(name=..., tools=[greet])`) build
  a config from the serialisable kwargs and put the rest into `extras`.
- Resolution of `INHERIT`, env vars, and file-based agents stays in manifest loading. A Definition
  is always **resolved**.
- `version` enables §5 R2 (sessions record which definition version they ran with). For extras,
  hash on stable identity (import path / qualname). If an extra has no stable identity (lambda,
  closure), the version is "unpinnable": fine for ad-hoc Python use, but such definitions can't be
  resumed with `pinned` policy.
- Should it turn out that every agent type always needs the same few extras, `extras` could later
  be folded into the configs as `SkipJsonSchema` fields. The wrapper keeps that option open.

### 9.2 Session

```python
class Session:
    data: SessionData             # existing model (agentpool/sessions/models.py:55): id, agent_name,
                                  # parent_id, cwd, sdk_session_id, metadata, ...
    definition_version: str       # §5 R2
    overrides: SessionOverrides   # model, mode / config options, extra tools, prompt addendum
    history: MessageHistory       # moves here from BaseAgent.conversation
    inbox: Inbox                  # queued / steered input, child-session results (§8 vocabulary)
```

- Pure state, persisted, resumable. It knows nothing about processes or connections.
- Today's runtime mutations of an agent (`set_model`, adding tools at runtime, mode switches)
  become `overrides`, which are per-session and survive resume.

### 9.3 Runtime

```python
class AgentRuntime[TDeps, TOut]:
    definition: AgentDefinition[TDeps, TOut]
    session: Session
    env: RuntimeEnv               # pool reference, shared resources (MCP manager, storage, fs)
    # live, non-serialisable resources only:
    #   native: PydanticAI agent + tool manager instance
    #   claude code / codex: subprocess + native session handle
    #   acp: connection (possibly shared, see below) + remote session id
    #   input provider + event handlers (from the session's client), cancel scope

    @property
    def model(self) -> ModelRef | None:   # effective value: override, else definition default
        return self.session.overrides.model or self.definition.config.model_ref

    async def run_stream(self, *prompts, delivery: Delivery = "queue") -> AsyncIterator[Event]: ...
```

- **No config fields of its own.** Everything is either read from the definition, read from session
  overrides, or a live resource. That removes the duplication.
- Subclassing per runtime type (native, claude code, codex, acp, agui) replaces today's `BaseAgent`
  subclasses. Most of their code survives; it just reads config from `definition` and history from
  `session` instead of `self`.

### 9.4 Lifecycle and ownership

```
definition  = pool.definitions["coder"]                 # registry lookup (pool or session scope)
session     = sessions.create(agent="coder", parent_id=..., cwd=...)   # data, persisted
runtime     = pool.attach(session)                      # definition + session + env -> live runtime
async for event in runtime.run_stream("..."): ...

runtime     = pool.switch_agent(session, "reviewer")    # same session, new runtime, marker message
runtime     = pool.attach(sessions.load(id))            # resume: new runtime, old session
```

- **The session comes first, the runtime is attached to it.** The runtime doesn't spawn the session
  and isn't the session, because of:
  1. resume: the session outlives the runtime (restart, crash);
  2. agent switch: same session, different runtime;
  3. external agents: their native session belongs to the runtime layer, and our session only holds
     the pointer (`sdk_session_id`).
- **Cardinality:** a session has at most one *attached* runtime at a time, and a runtime serves
  exactly one session. So the runtime is effectively "the live side of a session". The public API
  can present them as one handle (`pool.open_session(...)` → `.run_stream()`, `.session`,
  `.switch_agent()`), but internally they stay two objects.

### 9.5 Facade: the Python API stays ergonomic

```python
async with Agent(name="x", model="openai:gpt-4o", tools=[greet]) as agent:
    await agent.run("...")
```

`Agent(...)` (and `ClaudeCodeAgent(...)`, …) become facades: build a Definition from the kwargs, create
a default Session, attach a Runtime. `agent.conversation` → `agent.session.history`. The duplication
of fields lives in **one** convenience constructor per type, not throughout the core. `pool.get_agent(name)`
returns the same facade bound to that agent's default session (backwards compatible, §3).

### 9.6 Awkward spots

- **Resource sharing for native runtimes:** creating a PydanticAI agent per session is cheap, but MCP
  connections and toolsets may not be. Decide per resource whether it is pool-shared (in `RuntimeEnv`)
  or per runtime. Rule of thumb: stateless or connection-pooled resources are shared, and anything holding
  per-conversation state is per runtime.
- **External runtimes are expensive** (one process each). ACP supports multiple sessions per connection,
  so an ACP runtime can be a light handle on a pool-shared connection. Same interface, different
  cost model.
- **Teams:** a team run is a runtime whose resources are child sessions plus their attached runtimes.
  No special case needed.
- **Typing:** `AgentRuntime[TDeps, TOut]` generics must flow from `AgentDefinition[TDeps, TOut]`.
  Python-constructed agents keep full typing through `extras`. YAML agents stay `Any`-typed as today.

---

## 10. Pain points from the OpenCode V2 server (2026-11-20)

The OpenCode V2 server (`src/agentpool_server/opencode_v2_server/`, branch
`feat/opencode-v2-server`) is the first server that is pool-wide and multi-session by design. It
hit every gap described above. Each item below lists the symptom, the stopgap now in the code,
and the migration step (§6) that should make the stopgap unnecessary.

### 10.1 One agent instance can't serve several sessions

- **Symptom:** V2 clients keep several sessions open, each bound to a pool agent. Agent instances
  hold one `session_id`, one `conversation` and, for external agents, one native session.
- **Stopgap:** `runner.py` `AgentBinder` takes a per-agent `asyncio.Lock`, so turns of different
  sessions on the same agent are serialized. Before each turn it re-points the instance: it calls
  `load_session(session_id)` for sessions with history, and the new
  `BaseAgent.start_new_session()` for fresh ones. `start_new_session` has overrides for Claude
  Code (reconnect without resume), Codex (new thread), ACP (`new_session`) and AG-UI.
- **Cost:** no parallelism per agent. Every session switch reloads history, and for Claude Code
  it reconnects the SDK.
- **Fix:** steps 1, 2 and 7 (session owns history, explicit sessions in `run_stream`, a runtime per
  session).

### 10.2 `load_session` means different things per agent type

- **Symptom:** Claude Code sets `_sdk_session_id = session_id`, so it treats our session ID as the
  Claude transcript ID. ACP passes the ID to the remote `load_session`, so it expects the remote
  ID. Native agents load from storage. `SessionData.sdk_session_id` exists but isn't what these
  methods read.
- **Stopgap:** none. The binder calls `load_session(session_id)` and accepts the per-type behaviour.
- **Fix:** step 2 and §9.3. A runtime is created from `Session` (with `sdk_session_id`) plus
  `Definition`, so "resume native session X" becomes explicit data instead of a convention.

### 10.3 Storage doesn't keep the user message as its own record

- **Symptom:** `_run_stream_once` logs only `final_message`. The user prompt lives inside its
  pydantic-ai `ModelRequest` (`UserPromptPart`).
- **Stopgap:** `history.py` `prompt_text()` extracts user turns from the first `ModelRequest` of
  each stored assistant message when rebuilding a V2 log from storage.
- **Fix:** step 1. The session persists every message it owns, user messages included.

### 10.4 No stable message identity between protocol and storage

- **Symptom:** V2 message IDs (`msg_…`, the inbox ID of the prompt, the step ID of each assistant
  message) have no counterpart in stored `ChatMessage.message_id`s (UUIDs, one per turn rather
  than one per step).
- **Stopgaps:**
  - Fork and revert count user turns (`session_ops.py` `history_prefix()`) to decide how much
    stored history to copy or keep.
  - Session diffs and file reverts select `FileOpsTracker` changes by timestamp
    (`changes_between()`), because `FileChange.message_id` holds agent-side IDs.
- **Fix:** `run_stream` should accept a caller-supplied ID for the user message and expose stable
  IDs for each model response. That fits naturally with step 1.

### 10.5 No model-request (step) lifecycle in the event stream

- **Symptom:** V2 renders each model request as its own assistant message
  (`session.step.started` / `.ended`) with its own usage. `RichAgentStreamEvent` has no
  step-start/step-end events. Usage only arrives once, in `StreamCompleteEvent`, as a total.
- **Stopgap:** `run_converter.py` infers step boundaries ("model output after tools of the current
  step completed"). Intermediate steps report zero tokens and the last step carries the total.
- **Fix:** add `StepStartedEvent` / `StepCompletedEvent(usage, finish_reason)` to the event union
  (one per `ModelResponse`), emitted by all agent types. This is independent of the session
  refactor and useful for ACP and AG-UI too.

### 10.6 Two overlapping tool event vocabularies

- **Symptom:** native agents emit pydantic-ai part events (`PartStartEvent(ToolCallPart)`,
  `FunctionToolCallEvent`, `FunctionToolResultEvent`). Toolsets and external agents emit
  `ToolCallStartEvent` / `ToolCallProgressEvent` / `ToolCallCompleteEvent`. Native tool calls
  produce both.
- **Stopgap:** the converter accepts both and deduplicates by `tool_call_id`.
- **Fix:** define one canonical tool lifecycle in `RichAgentStreamEvent`. Native agents translate
  pydantic-ai events into it at the source, like external agents already do.

### 10.7 Subagents aren't sessions

- **Symptom:** V2 subagents are child sessions (`parentID`) that report results to the parent's
  inbox (`session.inbox.*`, jobs of kind `subagent`). Our `task` tool runs the shared pool
  instance inside the tool call. Activity arrives as `SubAgentEvent`.
- **Stopgap:** `SubAgentEvent`s are rendered as progress of the delegating tool call. No child
  sessions or subagent jobs are exposed.
- **Fix:** step 3 (child sessions, return the child session ID). The V2 server then maps child
  sessions 1:1 and reports them via the parent inbox.

### 10.8 History we can't rewrite

- **Symptom:** fork and revert can rewrite AgentPool storage
  (`replace_conversation_messages`, copied `ChatMessage`s), but not Claude Code's or Codex's
  native transcripts.
- **Stopgap:** documented limitation. After a revert the binder reloads the session, but
  external runtimes resume their untruncated native session.
- **Fix:** §8.2. Either replay our history into a fresh native session (lossy but consistent), or
  use native fork/rewind APIs where they exist (the Claude SDK has fork sessions, Codex has thread
  forks). The runtime abstraction should expose `fork(at=…)` / `rewind(to=…)` with "unsupported" as
  a valid answer.

### 10.9 Missing first-class agent operations

- **Compaction:** there's no `agent.compact()`. `runner.py` `_compact()` ports V1's flow: summarize
  prompt, then `compact_conversation(pipeline, agent.conversation)`, then
  `replace_conversation_messages`. External agents compact natively, so this only works properly
  for native agents.
- **Runtime instructions:** V2 instruction entries and skill activation want to change the model
  context of a session. There's no API to add session-scoped system instructions, so they're
  appended as text to the next prompt (`SessionRunner.pending_context`).
- **Session environment:** `session.environment` (env vars) only applies to session shell
  commands. There's no way to set env vars per session on an agent's execution environment.
- **Fix:** with a runtime per session (step 7), these become runtime methods: `compact()`,
  `set_instructions()`, environment on the runtime's exxec environment.

### 10.10 Teams have no session or model surface

- **Symptom:** `Team` / `TeamRun.run_stream(*prompts, **kwargs)` forwards kwargs to every member.
  Teams have no `session_id`, no `load_session`, no model, no `start_new_session` and no
  injection.
- **Stopgap:** teams are exposed as agents with a synthetic model (`agentpool/<team>`). A turn
  locks all member agents (`catalog.leaf_agents`) and runs without session binding. Steering falls
  back to queueing.
- **Fix:** §9.6 ("a team run is a runtime whose resources are child sessions"). Members get child
  sessions of the team session.

### 10.11 Model identity is per agent, but V2 lists models per location

- **Symptom:** V2 has one model list per location. Ours depends on the agent:
  `get_available_models()`, tokonomics IDs and `id_override`, versus `agent.model_name` strings.
  The configured model of an agent (e.g. `test`, custom or fallback models) is often missing from
  discovery.
- **Stopgaps:** `catalog.py` lists the union of all agents' models, always adds the configured
  model, and resolves `ModelRef → set_model` target per agent (switches the agent can't use fail
  with `UnsupportedModelError`). Because instances are shared, the binder re-applies the session's
  model before each turn.
- **Fix:** model selection becomes session state applied to the session's runtime (step 7), so no
  re-applying is needed. Also: a canonical `ModelRef(provider, id)` on `AgentDefinition`, so
  discovery and the configured model can't diverge.

### 10.12 What mapped well (keep as is)

- `InputProvider` → permissions (tool confirmation) and forms (MCP elicitation and structured
  input via JSON schema).
- `RichAgentStreamEvent` text, thinking and tool events → V2 `session.text.*`,
  `session.reasoning.*` and `session.tool.*`.
- Slash commands (`command_store`), `SkillsManager`, MCP status, exxec PTYs, `SessionData` storage.
- Agent modes (`thought_level` / `effort`) → V2 model variants.

### 10.13 Stopgap inventory (code to delete after the refactor)

| Stopgap | Location | Removed by |
|---|---|---|
| `AgentBinder` lock + re-binding | `opencode_v2_server/runner.py` | steps 1, 2, 7 |
| `BaseAgent.start_new_session()` and overrides | `agents/*` | step 7 (runtime per session) |
| User prompt extraction from `ModelRequest` | `opencode_v2_server/history.py` | step 1 |
| Turn-counting history cut for fork/revert | `opencode_v2_server/session_ops.py` | stable message IDs (10.4) |
| Timestamp-based file change selection | `opencode_v2_server/session_ops.py` | stable message IDs (10.4) |
| Step-boundary inference, end-of-run usage | `opencode_v2_server/run_converter.py` | step events (10.5) |
| Subagents as tool progress | `opencode_v2_server/run_converter.py` | step 3 |
| `pending_context` for skills and instructions | `opencode_v2_server/runner.py` | runtime instructions (10.9) |
| Manual compaction flow | `opencode_v2_server/runner.py` | `compact()` on runtimes (10.9) |
| Team member locking, synthetic team model | `opencode_v2_server/runner.py`, `catalog.py` | team runtimes (10.10) |

