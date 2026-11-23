# Turns as a first-class model

Status: proposal, not started. Written 2026-11-23, revised the same day (teams demoted, see
"How multi-agent work happens now"). Related: `ARCHITECTURE_SESSIONS_AGENTS.md` (sessions,
runtimes) and `ARCHITECTURE_ROUTING.md` (router, triggers, session policies).

## How multi-agent work happens now

`Team` (parallel) and `TeamRun` (sequential), with `a & b` / `a | b`, come from a time of
exploring APIs and conversation topologies in code. That is no longer how agents are used:
people mostly don't write code against the API, they configure agents and let them work.

- **An agent delegates.** An orchestrator decides mid-turn to hand work to another agent: the
  subagent tool (`agentpool_toolsets/builtin/subagent_tools.py`), background tasks, and the
  agents' own subagents (Claude Code, Codex). The topology is chosen by the model at runtime.
- **Things happen around agents.** Triggers, MCP events, connections, and protocol clients
  (Zed, OpenCode, Claude Code over MCP) driving sessions.
- **Fixed pipelines and fan-outs** are the rare case. Outside their own classes, teams appear
  in a handful of places (the OpenCode V2 runner, the MCP server's `ask_<team>` tools, the
  subagent toolset, one CLI and one command module, five docs/examples).

So the structure that matters is "this turn spawned these turns": an orchestrator's turn with
its subagent turns as children, each with its own session, outcome and cost. That is what
`Turn` models, and none of it depends on teams. Teams stay, thin: convenience for code and
YAML, reimplemented as small orchestrations over turns, without new features (session
policies keep rejecting team targets with a clear error).

## Problem

The unit agentpool actually runs is a **turn**: a prompt goes into a session of a node, the
node works (model requests, tool calls, subagents, team members), and the turn ends with an
outcome and, usually, a final answer. No type says that. The turn is spread over four
partial representations, and `ChatMessage` carries most of it while also being a message:

- **`ChatMessage`** (`agentpool/messaging/messages.py`) is a conversation message (the user's
  prompt, `role="user"`) *and* the result of a turn: an assistant `ChatMessage` holds all of
  the turn's model requests, tool calls and responses (`messages: list[ModelMessage]`), the
  turn's summed `usage`, `cost_info`, `response_time`, `finish_reason`, model and provider
  details. For a team it is also the aggregate: `content` is a list of the members' answers,
  `messages` their concatenation, `associated_messages` the members' messages (`TeamRun`).
- **`SessionRuntime`** (`agentpool/agents/runtime.py`) is the turn being executed: its task,
  cancellation, queued prompts, active-run count, cost so far.
- **Outcome types** exist only in the ACP server (`agentpool_server/acp_server/turns.py`:
  `TurnCompleted`, `TurnCancelled`, `TurnLimitReached`, `TurnFailed`, `TurnRefused`); the
  agents themselves end a turn either with a `StreamCompleteEvent` or an exception, and a
  cancelled native turn returns a partial answer like a completed one.
- **The activity feed** (`agentpool/sessions/activity.py`) re-derives the outcome as the
  `idle` entry's `stop_reason` from how the runtime's loop ended.

A turn already has an identity, only implicitly: since `8c6f6df6f` turns are stored and found
by their prompt's message ID (the answer's `parent_id`), which revert and fork rely on.

### What this costs today

- **Teams.** `Team`/`TeamRun.run()` returns one `ChatMessage` with a list as content,
  ignores team sessions (a `session=` would reach every member unchanged), and `TeamRun`
  chains its members through temporary queued connections. `run_stream(session=...)` runs
  members in child sessions but yields only their events, wrapped as `SubAgentEvent`: no team
  result, nothing announced on `message_sent`. Session policies therefore cannot target
  teams, and a team's own session never reports `running` / `idle`.
- **Outcome.** "Was this turn cancelled, did it fail, did it hit a limit" is answered
  differently by the ACP server, the OpenCode V2 runner, the activity feed and callers of
  `run()` (who mostly can't tell).
- **Relations.** A team turn, its members' turns, a subagent call and a routed run are
  related, but only through `parent_id` chains between messages and session parent IDs,
  reconstructed by each consumer.
- **`ChatMessage` growth.** Every turn-level fact lands on the message type: 42
  constructions in 24 files, 66 files mention it, and turn fields are read widely
  (`usage` ~45 places, `cost_info` ~22, `messages` ~20).

## Proposal

### The model

```python
@dataclass(kw_only=True)
class Turn[TResult]:
    turn_id: str  # the prompt's message ID (already the storage key)
    session_id: str
    node: str  # agent or team name
    parent: TurnRef | None  # what caused it: team turn, routing source, subagent call
    prompt: ChatMessage[Any]  # role "user"
    outcome: TurnOutcome  # how it ended (below)
    result: ChatMessage[TResult] | None  # the final answer; None for failed/refused
    usage: RunUsage  # the turn's, summed (children included for a team)
    cost: TokenCost | None
    started_at: datetime
    duration: float
    children: list[Turn[Any]]  # member turns of a team, subagent turns


TurnOutcome = Completed | Cancelled | LimitReached | Failed | Refused
```

- `TurnOutcome` is the ACP server's union, moved to `agentpool` (`agentpool/turns.py` or
  `agentpool/sessions/turns.py`) and shared: the ACP and OpenCode V2 servers, the activity
  feed and callers read the same thing. `Cancelled` carries the partial result if any.
- `TurnRef` is `(session_id, turn_id, node)`: enough to find the stored turn, cheap to keep.
- `parent` + `children` make the tree explicit: a team turn's children are its members'
  turns (in their child sessions); a subagent tool call's turn is a child of the calling
  turn; a routed run's `parent` is the source turn.

`ChatMessage` stays the conversation message. In the end (stage 4) it loses the turn-level
fields; until then they stay, and `Turn` reads them.

### Running a turn

- The turn loop (`SessionRuntime.run_stream`) builds the `Turn` as it ends and attaches it to
  the final event: `StreamCompleteEvent` gains `turn: Turn` (its `message` stays, =
  `turn.result`), and a cancelled or failed turn still ends with one (no more "an exception
  or a partial answer that looks complete").
- `run()` keeps returning `ChatMessage` (`turn.result`) for compatibility. A new
  `run_turn()` returns the `Turn`. Once callers have moved, `run()` can return the `Turn`
  (breaking, stage 4).
- `message_sent` keeps firing with the result message; a new `turn_completed` signal carries
  the `Turn` (the router and the activity feed move to it).

### Teams (thin, last)

Not a goal in itself (see above): the point is to delete the two half-paths and the temporary
connections, not to grow team features. When it is done, one implementation per team type,
on the streaming path, which already handles sessions:

- Members run in child sessions (`member_session`), each a child turn.
- The team turn ends with its own completion event carrying the team `Turn`: `children` are
  the member turns, `result` the combined answer (parallel: the members' results, in a typed
  container instead of a list `content`; sequential: the last member's result), `usage` the
  sum, `outcome` derived (any failed member fails a sequential team; a parallel team fails
  only if all did, unless `require_all`).
- The team turn reports `running` / `idle` for the team session to the activity feed and is
  announced (`turn_completed`, `message_sent`), so the team's connections route it.
- `run()` / `run_turn()` consume the stream; `execute()`, `execute_iter()`, the temporary
  queued connections of `TeamRun`, and `AgentResponse` / `TeamResponse` go away (or become
  views of the turn tree).
- Session policies could then accept team targets; not planned unless someone needs it.

A different meaning of "team" may fit today's use better: a named group of agents an
orchestrator can delegate to (what an agent sees through the subagent tool anyway). Open.

### Routing and triggers

- The router routes on `turn_completed`: a connection delivers `turn.result` (as now) and
  records the routed turn's `parent` as the source turn.
- Connection conditions and transforms keep getting the message; `EventContext` gains the
  turn.
- Triggers create turns with `parent=None` and the trigger in their metadata.

### Servers and the activity feed

- The activity feed's `idle` entry comes from `turn.outcome` instead of re-deriving it.
- The ACP server's `turns.py` outcome types become the shared ones; its `_stream_agent`
  reads the outcome from the final event instead of mapping exceptions.
- The OpenCode V2 runner does the same for `session.status` and its message parts; its turn
  IDs are already the prompt IDs.
- The MCP server's `ask_<name>` tools return `turn.result` (unchanged for clients).

### Storage

Nothing new is required: storage already keeps a turn's answer, with the prompt inside its
`messages`, under the prompt's ID. A turn can be rebuilt from that (prompt, result, usage,
cost). Persisting `outcome` and `parent` (as metadata of the stored answer) makes rebuilt
turns complete; children are found by `parent`.

## Migration

Each stage leaves everything working; only stage 4 breaks the public API.

1. **`Turn` and shared outcomes.** Add `Turn`, `TurnRef`, `TurnOutcome` (moved from the ACP
   server). The turn loop builds a `Turn` for every agent turn, attaches it to
   `StreamCompleteEvent`, ends cancelled and failed turns with it, emits `turn_completed`.
   Add `run_turn()`. The activity feed takes `idle` from the outcome. No behaviour change for
   existing callers.
2. **Delegation and consumers on turns.** Subagent tool calls and background tasks as child
   turns of the calling turn (the main structure today); router on `turn_completed` with
   parent links; ACP and OpenCode V2 servers on shared outcomes; storage persists outcome
   and parent.
3. **Teams, thin.** Reimplement `Team` / `TeamRun` as small orchestrations over turns (member
   turns as children, one streaming path, no temporary connections, no `execute*()`), only
   to remove the duplicated paths. No new team features.
4. **Slim `ChatMessage` (breaking).** Move `messages`, `usage`, `cost_info`,
   `response_time`, `finish_reason`, `associated_messages`, model and provider details to
   `Turn`; `run()` returns `Turn`. Storage providers and history then read turns. Needs a
   storage compatibility path for histories stored before.

Stages 1-3 are additive and each about the size of a routing step; stage 4 touches most of
the 66 files that use `ChatMessage` and is worth doing only once 1-3 have settled.

## Open questions

- **Names.** `run_turn()` vs. `run()` returning `Turn` right away (breaking earlier); signal
  `turn_completed` vs. reusing `message_sent` with a richer payload.
- **What "team" should mean.** Keep `Team` / `TeamRun` as fixed fan-out / pipeline sugar,
  or redefine a team as a group of agents an orchestrator delegates to? If kept: a parallel
  team's result is a list today, which downstream agents receive as its string form.
- **Partial results.** Should `Cancelled` carry the partial answer as `result` (as native and
  Claude Code agents return it today) or keep `result=None` and put the partial text in the
  outcome?
- **Nested turns of one agent.** A native agent calling a subagent tool runs a turn of
  another node while its own turn runs. Is the child turn's `parent` the calling turn (yes),
  and does its usage roll into the parent's `usage` (probably not: the parent's own model
  usage only, with children summed on demand)?
- **Storage of children.** Is `parent` in stored metadata enough to rebuild a team turn
  tree, or should storage index turns by parent?
- **Event stream.** Do clients need a `TurnStartedEvent` (with `turn_id`, `parent`) next to
  `RunStartedEvent`, or should `RunStartedEvent` simply gain them?
