# Routing and triggers from outside the nodes

Status: steps 2-6 done on 2026-11-23 (step 6: MCP event triggers, see `MCP_EVENTS.md`).
Written 2026-11-23.

## Done (2026-11-23)

- **Step 2** (`refactor(talk): a router owns the connections`): `agentpool/talk/router.py`
  `Router` holds every `Talk` keyed by source node, with the `ConnectionRegistry` as its name
  index. `pool.router`, or a node's own router outside a pool, whose connections move to the
  pool's when the node registers. `node.connections` is a view. `Talk.disconnect()` removes the
  connection. Before, disconnected connections stayed forever, and `TeamRun` added two per run.
- **Step 3** (`refactor(talk)!: the router routes on message_sent`): turns and team runs store
  their response, then announce it on `message_sent`. The router is subscribed there for every
  node with a connection. The per-call `wait_for_connections` is gone; the wait is the
  connection's setting.
- **Step 4** (`feat(triggers): the router runs the triggers`): `agentpool/talk/triggers.py`
  `Triggers` (`router.triggers`) runs every node's trigger sources. The pool starts them after
  its nodes are up; a node outside a pool runs its own. Nodes no longer own an `EventManager`.
  `Talk` reports `message_received`, `message_forwarded`, `message_processed`, `queue_filled`
  and `queue_triggered`, once each, to its own `on_event` callbacks and to the router's
  triggers. `type: connection` triggers validate (`NodeConfig.triggers` uses agentpool's
  union) and run their node in the background. `ConnectionEventData` is a dataclass now: as an
  `EventData` (pydantic) it could not hold a `Talk` or `ChatMessage`.
- **Step 5** (`feat(talk): session policies for connections and triggers`): connections take
  `session: target | per_source_session | standing | per_message`, triggers
  `session: target | standing | per_event`. `agentpool/talk/sessions.py` `SessionPicker`
  (`router.sessions`) opens the sessions: in `pool.sessions` in a pool, otherwise locally. Agents with one
  native session per instance take their turn lock, as team members do. Other than `target` only for
  agent targets: a team's `run_stream(session=...)` doesn't announce on `message_sent`, so its
  connections wouldn't route. The trigger configs are agentpool subclasses of evented's
  (`FileWatchTriggerConfig`, ...) to carry the field. Fixed on the way: `run` connections ignored
  `delay` and `priority`, and `TaskManager` never ran a delayed task: it cancelled the task and then
  restarted the closed coroutine.
- Left as they were: team-internal `Talk`s (`Team`, `TeamRun.execute_iter`), queued
  connections on `Talk`, and the standalone `EventManager` utility (`track` / `poll`), which
  no node uses any more.

The sections below are the original proposal. Related: `ARCHITECTURE_SESSIONS_AGENTS.md`
(item 6, per-connection session policy) and `MCP_EVENTS.md` (MCP event triggers, which need
this first).

## Problem

Each `MessageNode` (agent or team) currently owns two things that are really about how the pool
is wired together, not about the node:

- **Outgoing routing**: `node.connections`, a `ConnectionManager` holding the node's `Talk`s.
- **Trigger sources**: `node._events`, an `EventManager` that runs file, webhook, email and
  timer sources and calls `self.run(prompt)` on each event.

The agent's own turn code then calls the routing. Because of this, every agent implementation
has to know about the connection graph. The graph has no single owner, and a routed message or
a triggered event has nowhere to say which session it should run in.

## Current state (file:line as of 409b1c933)

### Events

- `src/agentpool/messaging/messagenode.py:73-85`: every node builds its own `EventManager`
  with the handler `self.run(prompt)`. Sources start and stop with the node's
  `__aenter__`/`__aexit__` (`:132-153`). A triggered run goes into the node's default session,
  and nothing lets the trigger choose another one.
- **Connection events are unwired.** `Talk._emit_connection_event` (`src/agentpool/talk/talk.py:213`)
  has no callers, so no connection event is ever emitted. If it were called, it would send the
  event to `connection.source._events.emit_event(...)` for every `Talk` in
  `pool.connection_registry`, which every pool-attached `Talk` is registered in, named or not
  (`messaging/connection_manager.py:170-175`). That would mean:
  - a node with N outgoing connections receives each event N times;
  - a node with no outgoing connection never receives one;
  - every receiving node's `_event_handler` runs the node with `ConnectionEventData.to_prompt()`
    as the prompt;
  - `Talk.on_event` callbacks (`talk.py:200-210`, registered on `self.source._events`) don't
    check `event.connection is self`, so they would fire for every connection's events.

  It also reaches into a private attribute of another class.
- **Connection triggers can't be configured.** `ConnectionTriggerConfig` and the union that
  includes it (`src/agentpool_config/events.py:31, 81`) are rendered in
  `docs/configuration/event-sources.md`. But `NodeConfig.triggers` is typed with
  `evented_config.EventConfig` (file/webhook/email/time, `agentpool_config/nodes.py:8, 80`), so
  `type: connection` fails validation ("does not match any of the expected tags"). Even if it
  passed, `EventManager.add_source` would hand it to `evented.base.EventSource.from_config`,
  which has no `connection` case.
- **Configured triggers were dropped for most node types. Fixed 2026-11-23.** `from_config`
  passed `config.triggers` only for native, Claude Code and ACP agents. AG-UI, Antigravity,
  OpenCode V2, T3 and Codex dropped them, and so did teams (`TeamConfig.get_team`, which also
  dropped `description`). Now covered by `tests/test_trigger_configs.py`.

### Routing

- `messagenode.py:83`: every node owns a `ConnectionManager`. `connect_to` (`:303-377`) wraps
  callables into agents, registers them on the pool and creates the `Talk`.
- The agent triggers its own routing at the end of each turn:
  - `src/agentpool/agents/runtime.py:393`: `SessionRuntime` ends a turn with
    `agent.connections.route_message(final_message, wait=wait_for_connections)`.
  - `src/agentpool/messaging/processing.py:61` `finalize_message` (the team path) does the same.
- `pause_routing` sits in the native agent's temporary-state overrides next to tools and
  history (`native_agent/agent.py:941`).
- `wait_for_connections` is a parameter on every run method (`base_agent.py:825, 1034, 1203,
  1311`). It is also stored per target (`ConnectionManager.set_wait_state`, set from YAML in
  `agentpool_config/forward_targets.py:114`).
- The graph has no owner:
  - edges live on each source node;
  - `pool.connection_registry` (`talk/registry.py`) holds every pool-attached `Talk` by name,
    but edges created before a node joins a pool are never registered;
  - `pool.get_mermaid_diagram` walks every agent's `connections` (`delegation/pool.py:540`).
- Delivery has no session policy. `Talk._process_for_target` calls
  `target.run_message(message)` (`talk.py:299-310`) for `run`, writes `staged_content` for
  `context`, and calls `target.connections.route_message` for `forward`.
- Callers of `node.connections`: `agentpool_commands/connections.py` (list, set wait,
  disconnect) and `agentpool_commands/commands.py:43`.

## Proposal

One pool-level component. This doc calls it `Router`; the name is not decided. It owns the
edges, the trigger sources and dispatch. Nodes go back to running turns and announcing that a
turn finished.

### 1. Edges

- The `Router` holds every `Talk`, keyed by source and named or not:
  `router.connect(source, targets, ...)`, `router.disconnect(source, target)`,
  `router.outgoing(source)`, `router.incoming(target)`.
- The current `ConnectionRegistry` (lookup by name, auto-named when unnamed; `message_flow`
  signal) becomes part of the router.
- `MessageNode.connect_to` and `>>` stay as sugar that delegates to the router.
  `node.connections` becomes a read-only view (`targets`, `get_connections`, stats), so the
  slash commands and the mermaid code keep working.
- `ForwardingTarget.connect_nodes` (`agentpool_config/forward_targets.py`) calls the router
  directly and stores `wait_for_completion` on the edge.

### 2. Dispatch on turn completion

- Agents no longer call routing. The router subscribes to each node's `message_sent` signal
  when the node is registered. The servers and the CLI already listen to this signal
  (`agentpool_cli/serve_*.py`, `watch.py`, `run.py`).
- `Signal.emit` is awaited (`runtime.py:387`), so "wait for connected agents" still works
  inline. The edge's `wait` flag decides whether the router awaits the target run or spawns
  it.
- Delete `agent.connections.route_message` in `runtime.py:393` and `processing.py:61`.
- `pause_routing` becomes `router.paused(source)`, a context manager, and leaves the agent's
  overrides.
- Per-call `wait_for_connections`: drop it in favour of the per-edge flag, or carry it in the
  run's context var (`agents/runtime.py` already activates a per-session context). Leaning
  towards dropping it; check callers first.
- `forward` edges re-dispatch through the router (`router.route(target, message)`) and don't
  call `target.connections`.

### 3. Triggers

- One trigger manager per pool, owned by the router. It replaces the per-node `EventManager`
  for configured triggers.
- `triggers:` stays on `NodeConfig` as configuration. The pool collects them from every node,
  agents and teams, so all node types are covered no matter what each `from_config` does. Each
  source is bound to its target node.
- Event to run: the router renders the prompt (`EventData.to_prompt()`) and dispatches it to
  the target with the trigger's session policy (see 4).
- Source factory: agentpool's own factory handles the types it owns (`connection`, later
  `mcp_event`) and falls back to `evented.EventSource.from_config` for file, webhook, email and
  timer sources. `NodeConfig.triggers` switches to agentpool's own union so `connection` (and
  later `mcp_event`) validate.
- Connection events are emitted (today nothing emits them) once, by the router, to subscribers. A subscriber is either a
  `ConnectionTriggerConfig` bound to a node or a programmatic callback (`Talk.on_event` becomes
  `router.on_connection_event(talk, event_type, cb)`). The `_events` access and the duplicate
  and missed deliveries go away.
- `EventManager.track` / `poll` decorators and `add_file_watch` and friends: keep them as
  router or pool API (`pool.triggers.add_file_watch(target=..., ...)`). Find out whether
  anything uses them before deciding.

### 4. Session policy

This is what item 6 of `ARCHITECTURE_SESSIONS_AGENTS.md` asks for. It can only be expressed
once dispatch happens outside the agent:

- for edges: `new_session_per_message` | `standing_session` (one per (source, target) pair) |
  `target_default_session`, plus "child of the source's session" for chains that should show up
  as one tree;
- for triggers: `new_session_per_event` | `standing_session` (one per trigger).

These settings go on `ConnectionConfig` / `ForwardingTarget` and on the trigger configs. The
router resolves the session through `pool.sessions` and runs the turn in it.

### 5. Standalone nodes (no pool)

`agent >> other` without a pool (as in README and docs examples) must keep working. A node with
no pool creates a private `Router` the first time it's needed, and nodes connected that way
share it. Joining a pool later moves the edges to the pool's router, or this case is simply not
supported. Decide which.

## What stays on the node

- Running turns, sessions and runtimes.
- `message_sent` and the other signals (announce only).
- `node.mcp` (`MCPManager`). This is a separate topic, but the trigger factory needs access to
  the node's and the pool's MCP connections for `mcp_event` triggers.
- `task_manager` for the node's own background work. Delayed or prioritised deliveries
  (`Talk` with `delay`/`priority`, now `target.task_manager.run_background`) move to the
  router's task group.

## Migration order

1. **Tests first:** connection-event delivery once events are emitted (each subscriber gets an
   event once, and only for its own connection) and a `type: connection` trigger in YAML. Both
   fail today because the feature isn't wired.
2. **Router with edges:** `connect_to` delegates, `node.connections` becomes a view, the
   registry folds in, mermaid and the commands read from the router.
3. **Dispatch on `message_sent`:** remove the `route_message` calls from `runtime.py` and
   `processing.py`, move `pause_routing`, settle `wait_for_connections`.
4. **Pool trigger manager:** collect triggers from every node config, use our own source
   factory (including `connection`), emit connection events once, remove the per-node
   `EventManager` from `MessageNode`.
5. **Session policies** for edges and triggers.
6. **MCP event triggers** (`MCP_EVENTS.md`).

Each step leaves the code working. Steps 2 and 3 change no YAML. Step 4 changes behaviour only
where it was broken or unwired.

## Open questions

- Name: `Router`, `Wiring`, `pool.routes`?
- Is `wait_for_connections` per call still used anywhere that matters?
- Who uses `Talk.on_event`, `EventManager.track` and `EventManager.poll` today? Keep or drop?
- Queued connections (`queued`, `_pending_messages`, `trigger_all`/`trigger_for`): do they stay
  on `Talk` or move to the router?
- Pool-less node joining a pool later: migrate edges, or don't support it?
- Should teams' internal member routing (`team_run`) use the router too, or stay internal to
  the team?
