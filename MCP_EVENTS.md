# MCP Events (SEP-3415) for agentpool

> **2026-11-25: blocked on fastmcp 4.2.6.** 4.2.6 moved the events extension into its own package
> `fastmcp-events` (module `fastmcp_events`, extra `fastmcp[events]`) with a new API: event types
> are Pydantic models (`@events.event(name)` on a model, `emit()` / `reader()`) instead of
> `EventPoll` -> `EventBatch` functions with an `input_schema`. agentpool pins `fastmcp<4.2.6`
> until the server (`serve_events`), the triggers (`talk/mcp_events.py`) and the tests are migrated.

Status: client side done 2026-11-23 (fastmcp 4.2.5 ships `fastmcp.experimental.events`): `mcp_event`
triggers (`agentpool/talk/mcp_events.py`, run by the router's `Triggers`), with `events: true` on the
MCP server config, resume from a stored cursor, start-time checks of event and arguments, and the
session policies. Server side done too: `serve_events` publishes the pool's activity feed
(`agentpool/sessions/activity.py`, ACP v2 style) as `session.state_update` / `session.info_update`.
First written 2026-11-23 against fastmcp 4.2.4, before it had Events. Companion to
`MCP_BACKGROUND_TASKS.md`.

## What it is

SEP-3415 defines the **Events extension** (`io.modelcontextprotocol/events`). An MCP client
subscribes to things that happen in an upstream system, such as a Slack message, a repo push
or a PagerDuty incident, so an agent can react without a user present. It came out of the
Triggers & Events Working Group.

- `events/list`: the server lists its event types. Each has a name, an `inputSchema` for
  subscription arguments, a `payloadSchema` and the delivery modes it supports.
- Three delivery modes. A server need not offer all of them:
  - **poll**: `events/poll`. The server keeps no state, because each request carries the
    event name, the arguments and the cursor.
  - **push**: a long-lived `events/stream` request that carries events and heartbeats.
    `notifications/events/terminated` ends it, for example when access is revoked.
  - **webhook**: `events/subscribe` / `events/unsubscribe`, signed per Standard Webhooks,
    with a TTL that must be refreshed.
- Opaque **cursors** let a client resume after a disconnect or restart. Replay is optional
  for each event type. `truncated: true` signals that events were skipped.
- Events have an `eventId`, which clients use to drop duplicates after a reconnect.
- Subscription arguments annotated with `x-mcp-header` travel as `Mcp-Param-*` HTTP headers
  (added 2026-11-17). If a header disagrees with the body, the server answers `HeaderMismatch`
  (`-32020`), and the client should list event types again and retry once.
- The SEP asks for new error codes, numbered `-32023`..`-32027` provisionally: NotFound,
  Forbidden, Unsupported and others.
- It fits the `2026-07-28` protocol: the capability sits under `extensions`, `list_changed`
  is a filter field on `subscriptions/listen`, and results carry `resultType`.

## Where things stand (2026-11-23)

| Where | State |
|---|---|
| Spec (SEP-3415) | Accepted by core maintainers; merged with status **Final** on 2026-11-19 |
| fastmcp | PrefectHQ/fastmcp#6489 **merged 2026-11-23** on main, **not released** (latest release 4.2.4 is from 2026-11-21). `fastmcp.experimental.events`, documented as experimental. **poll + push only**, no webhooks |
| official `mcp` SDK | modelcontextprotocol/python-sdk#3822 **open**: `mcp.experimental.events`, **poll + webhook**, no push. Not in mcp 2.3.0 |

The two Python implementations cover different delivery modes, so not every server will offer
the modes we implement. Check which one fastmcp's released version actually ships.

### fastmcp API (from #6489)

Server:

```python
from fastmcp.experimental.events import Event, EventBatch, EventPoll, EventsExtension

events = EventsExtension()  # EventsExtension(push=False) for hosts that can't hold a stream
mcp.add_extension(events)


@events.event(name="alert.fired", description="An alert fired")
def alert_fired(poll: EventPoll) -> EventBatch:
    if poll.cursor is None:  # subscribing: no events, current position
        return EventBatch(cursor=str(len(ALERTS)))
    new = ALERTS[int(poll.cursor) :]
    return EventBatch(
        events=[Event(data=a, event_id=a["id"]) for a in new], cursor=str(len(ALERTS))
    )
```

Each event type is one check function, and it serves both modes. A poll calls it once. A push
stream calls it in a loop, so push latency equals the poll interval. Sync check functions run
in a worker thread.

Client:

```python
from fastmcp.experimental.events import (
    EventsClientExtension,
    subscribe_events,
    list_events,
    poll_events_mcp,
)

async with Client(server, extensions=[EventsClientExtension()]) as client:
    async for event in subscribe_events(client, "alert.fired"):
        ...  # event.event_id, event.data; the cursor is exposed for resuming
```

- `subscribe_events` uses push when the event type offers it and polls otherwise. When
  polling it follows the interval the server recommends (minimum one second) and drains
  backlogs that are flagged `hasMore`.
- Push reconnects with jittered exponential backoff. Four consecutive failures raise
  `MCPError`. A server termination raises and does not fall back. If the consumer can't keep
  up, it raises `EventBacklogFullError`, and the cursor stays before the lost events.
- Duplicates are dropped by `eventId`. After a gap the client sends `active` with
  `truncated: true`, and a persisted cursor never runs ahead of delivered events.

What #6489 does **not** do:

- Webhooks, `list_changed`, and a direct `emit()` with a ring buffer.
- `ProxyProvider` doesn't forward the extension, and a parent server doesn't expose the event
  types of a mounted server.
- A dead stdio server or an HTTP session the server forgot ends the subscription. The
  application has to resubscribe from its cursor with a new client.
- The push client uses private SDK dispatcher internals, so expect breakage when the SDK
  changes.

CLI for trying servers:

```bash
fastmcp experimental events list <server>
fastmcp experimental events watch <server> <event> [key=value ...] [--cursor C]
fastmcp run examples/events/jetstream.py --transport http   # demo server: Bluesky via Jetstream
fastmcp experimental events watch http://127.0.0.1:8000/mcp bsky.mention subject=bsky.app --auth none
```

## What it means for agentpool

### Client side: the main win

Agents already connect to MCP servers. An event subscription on one of those servers becomes a
**trigger**: an event arrives, and a node runs with the event payload as its prompt. This is
what the SEP is for.

Where it plugs in:

- `src/agentpool_config/events.py`: the `EventConfig` discriminated union (`FileWatchConfig |
  WebhookConfig | EmailConfig | TimeEventConfig | ConnectionTriggerConfig`). A new
  `MCPEventTriggerConfig` (`type: mcp_event`) goes here.
- `src/agentpool_config/nodes.py`: `triggers: list[EventConfig]` on nodes. Nothing else
  needs to change in this file.
- `src/agentpool/messaging/event_manager.py`: `EventManager.add_source()` calls
  `evented.base.EventSource.from_config()`, which is a closed `match` in the external
  `evented` package. An MCP source needs the pool's MCP connections, so it belongs in
  agentpool, not in `evented`. `add_source` needs a branch for it, or a small factory hook.
  `_process_events` already iterates `source.events()` and calls `emit_event`.
- `src/agentpool/mcp_server/client.py`: `MCPClient._get_client()` builds `fastmcp.Client(...,
  extensions=[...])`. `SkillsClientExtension` is already added conditionally there.
  `EventsClientExtension` goes in the same place, when the server config enables events or
  when any trigger references that server.
- `src/agentpool/mcp_server/manager.py`: `MCPManager` owns the connections. The trigger source
  should reuse the agent's or pool's connection and not open its own. Decide which one.
  Reconnecting from the cursor after the session dies (see "does not do" above) has to happen
  here.
- `src/agentpool_cli/watch.py`: `agentpool watch` runs `pool.run_event_loop()`. It needs no
  changes once the source exists.

Config sketch:

```yaml
agents:
  triager:
    mcp_servers:
      - name: pagerduty
        url: https://...
    triggers:
      - type: mcp_event
        name: incidents
        server: pagerduty            # name of an MCP server config, the node's or the pool's
        event: incident.triggered
        arguments: {service: checkout}
        mode: auto                   # auto | poll | push
        prompt: "New incident: {{ event.data }}"   # Jinja2, like other triggers
        cursor_store: storage        # where to persist cursors, so a restart resumes and doesn't miss events
```

Open points:

1. **Cursor persistence.** Without it a restart either loses events or replays them.
   Options: the storage provider, or a small file under the pool's data dir. The cursor must
   only advance after the node has handled the event (at-least-once). Duplicates are fine
   because `eventId` deduplicates them.
2. **Backpressure.** Events can arrive faster than an agent turn finishes. Options: queue
   them, coalesce them into one turn, or drop them. Overflow raises `EventBacklogFullError`,
   so we must handle it and resume from the cursor.
3. **Sessions.** Decide whether each event gets a fresh session or all go into one long-lived
   session per trigger. See `ARCHITECTURE_SESSIONS_AGENTS.md`.
4. **Validation.** `events/list` provides `inputSchema` and `payloadSchema`. Validate the
   trigger's `arguments` at startup and fail early.
5. **Webhooks.** fastmcp has no webhook delivery. If we want it, it would reuse the existing
   `WebhookConfig` / webhook event source as the receiver and add Standard Webhooks signature
   verification. Do it later, and only for servers that offer webhooks but no push or poll.
6. **Optional dependency.** Import `fastmcp.experimental.events` lazily and give a clear error
   if the installed fastmcp lacks it.

### Server side (`serve-mcp`): weaker case

We could publish pool events as MCP event types, for example `agent.run.completed`,
`session.updated` and `message.forwarded` from connection events. Background tasks
(`MCP_BACKGROUND_TASKS.md`) already cover "long run finished" better. Only worth doing if a
consumer, such as another agentpool instance, wants a feed of pool activity. A fastmcp check
function would need a cursor over a stored event log, for example storage provider message
IDs or timestamps.

`ProxyProvider` / mounting doesn't forward events, which matters if we ever aggregate external
event servers behind `serve-mcp`.

## Reasons to wait

- The fastmcp support is unreleased and experimental, and uses private SDK internals for push.
- fastmcp and the official SDK implement different delivery modes.
- Hardly any real MCP servers publish events yet. The fastmcp Jetstream example is the
  easiest thing to test against.

## Suggested order

1. Once a fastmcp release contains #6489: add `EventsClientExtension` to `MCPClient`, and
   write an `MCPEventSource` that wraps `subscribe_events` and yields `EventData`.
2. Add `MCPEventTriggerConfig` to the `EventConfig` union and wire it into
   `EventManager.add_source`.
3. Persist cursors and handle at-least-once delivery, termination and backlog.
4. Test with an in-process FastMCP server (`EventsExtension` with a list-backed check, as in
   the PR example) and `TestModel`: an event arrives, a turn runs, and a restart resumes from
   the stored cursor.
5. Later, if needed: server-side event types on `serve-mcp`, and webhook delivery.

## Links

Spec:

- SEP PR, with discussion and the vote: https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3415
- SEP file: `seps/3415-events-extension.md` in modelcontextprotocol/modelcontextprotocol
  (rendered as `docs/seps/3415-events-extension.mdx`)
- Extension docs: `docs/extensions/events/overview.mdx` in the same repo
  (https://modelcontextprotocol.io/extensions/events/overview once published)
- Working group repo: https://github.com/modelcontextprotocol/ext-events
  (`docs/design-sketch-proposal.md`, `specification/beta/events/events.mdx`)
- Related open issue on partial task output: https://github.com/modelcontextprotocol/modelcontextprotocol/issues/3237

Implementations:

- fastmcp PR: https://github.com/PrefectHQ/fastmcp/pull/6489
  - `fastmcp_slim/fastmcp/experimental/events/` (`extension.py`, `client.py`,
    `subscription.py`, `models.py`)
  - `docs/servers/events.mdx`, `docs/cli/client.mdx`
  - `examples/events/jetstream.py`
  - `tests/experimental/events/` (`test_push_lifecycle.py` is the best reference for
    reconnect behaviour)
- Official Python SDK PR (poll + webhook): https://github.com/modelcontextprotocol/python-sdk/pull/3822

How to check status again:

```bash
gh api repos/PrefectHQ/fastmcp/releases/latest --jq '.tag_name, .published_at'
uv run python -c "import fastmcp.experimental.events"     # works once our fastmcp has it
gh pr view 3822 --repo modelcontextprotocol/python-sdk --json state,mergedAt
```
