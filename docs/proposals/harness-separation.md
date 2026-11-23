# Proposal: Separate Role, Harness, Environment and Policy in agent configs

Status: draft (2026-11-23)

## Problem

Every agent type has one flat config class (`NativeAgentConfig`, `ClaudeCodeAgentConfig`,
`CodexAgentConfig`, `OpenCodeV2AgentConfig`, `AntigravityAgentConfig`, `T3AgentConfig`,
`AGUIAgentConfig`, the ACP configs). Each class mixes four things that change for
different reasons:

- **what the agent is for**: instructions, tools, output type, effort, limits
- **what runs it**: the harness and its native knobs (`setting_sources`, `sandbox`, ...)
- **where it runs**: cwd, worktree, sandbox, env vars, secrets
- **how much it may do on its own**: permission / approval / autonomy

That has three effects:

1. **Shared concepts are spelled differently per harness**, so they look harness-specific when
   they are not. Instructions are `system_prompt` in most types but
   `base_instructions` / `developer_instructions` in Codex. Reasoning effort is
   `reasoning_effort` (Claude Code, Codex), `thinking_level` (Antigravity), `variant`
   (OpenCode) or model settings (native). The permission model has seven spellings (table below).
2. **A job can't be reused across harnesses.** "Reviewer" is written once for Claude Code and
   again for Codex; switching engines means rewriting the agent.
3. **Policy can't be layered on top.** A multi-repo agent setup (the "crew" idea: a
   dormant repo vs an active library vs a private product) needs to say "same roles,
   stricter autonomy, different engine". Today that needs a copy of every agent.

The runtime side has already started normalising this: `AgentCapabilities`
(`agents/capabilities.py`) declares what a backend supports, and `modes.py` normalises
mode/model/thought level into `ModeCategory`s. The config layer hasn't caught up.

## Layers

| Layer | Contains | Changes when… | Today |
|---|---|---|---|
| **Role** (portable) | instructions, tools, skills, MCP refs, output type, model preference, effort, limits | the job is redesigned | spread over every `*AgentConfig` |
| **Harness** | engine type and its harness options | the engine changes | `type:` + engine-specific fields |
| **Environment** | cwd / worktree, execution environment, env vars, secrets | it runs somewhere else | `environment`, `env_vars`, `cwd`, `add_dir`, `worktree`, `workspaces` |
| **Policy** | autonomy / permissions, tool confirmation, escalation, budgets as a ceiling | trust changes | `requires_tool_confirmation`, `permission_mode`, `approval_policy`, ... |
| **Topology** | triggers, connections, workers, teams, event handlers | the graph is rewired | `NodeConfig` fields, `workers`, `teams` |

An **agent** is then a composition: `agent = role × harness × environment × policy`, placed
in a topology.

## Rules

### 1. When a field is portable

A field moves into Role (or Environment / Policy) only when **at least two harnesses support it
with the same meaning**. Everything else stays native.

### 2. Adapters map portable fields to harness fields, and fail loudly

Each harness has an adapter that turns portable fields into its own (`instructions` →
`system_prompt` or `developer_instructions`). The adapter declares which portable fields it
supports, as a config-time counterpart of `AgentCapabilities`. If a role sets something the
harness can't do (for example `output_type` on AG-UI), **validation fails**. Nothing is ignored
silently.

### 3. Harness options as the escape hatch; the specific value wins

Harness-only knobs go into a typed block validated by that harness's model:
`harness: {type: codex, sandbox: workspace-write}`. If the same thing is set both portably
and as a harness option (`effort` and `harness.reasoning_effort`), **the harness option wins**
and a `compatibility` warning makes it visible. This is how pydantic-ai (provider-specific
settings override `thinking`) and the AI SDK (`providerOptions` override `reasoning`) do it,
and it allows a portable default with an exact value for one harness.

### 4. Define-and-reference vs inline: identity vs value

- **Things with identity get a name.** If two references should share one running instance
  or state, or always change together, define them once and reference them: MCP servers
  (processes), storage, knowledge stores, agents, teams, harness presets.
- **Values may be inlined.** Model settings, prompts, tool configs and limits are data; a named
  version is only a shorthand.
- **Every reference field takes `str | Config`**: a string looks up the name, an object
  is inline. Resolution happens in one place (`agentpool_config/resolution.py`).
- **Repetition** is handled by `INHERIT` / YAML anchors, not by more registries.

## Field mapping (current state)

Based on the config classes as of commit `405cf2138`.

### Portable candidates

| Portable field | native | claude_code | codex | opencode_v2 | antigravity | t3 | acp | agui |
|---|---|---|---|---|---|---|---|---|
| `instructions` | `system_prompt` | `system_prompt` (+`include_builtin_system_prompt`) | `developer_instructions` (`base_instructions` replaces the built-in) | — (via `agent`) | `system_prompt` (+`include_builtin_system_prompt`) | — | — | — |
| `model` | `model` (rich `AnyModelConfig`) | `model`, `fallback_model` | `model` | `model` | `model` | `model` + `provider` | — | — |
| `effort` | model settings | `reasoning_effort` | `reasoning_effort` | `variant`? | `thinking_level` | `options`? | — | — |
| `output_type` | ✓ | ✓ | ✓ | — | ✓ | — | — | — |
| `tools` (agentpool tools) | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | `ToolConfig` only |
| `builtin_tools` allow / deny | n/a | `allowed_tools` / `disallowed_tools` / `builtin_tools` | — | `permissions` rules | `enabled_tools` / `disabled_tools` | — | `allow_file_operations` / `allow_terminal` | — |
| `limits.turns` | `usage_limits` | `max_turns` | — | — | `budget.max_model_calls` | — | — | — |
| `limits.usd` / tokens | `usage_limits` | `max_budget_usd` | — | — | `budget.*_tokens` | — | — | — |
| `subagents` | `workers` (topology) | `builtin_subagents` | — | — | `enable_subagents`, `max_subagent_depth` | — | — | — |
| `skills` | — | via settings | — | — | `skills_paths` | — | — | — |
| `mcp_servers` | base ✓ | base ✓ | base ✓ | base ✓ | base ✓ | base ✓ | base ✓ | base ✓ |

`?` = plausible mapping, to be checked against the backend.

### Environment

| Portable field | Current spellings |
|---|---|
| `cwd` / workspace | `environment` (str = cwd), ACP `cwd`, `add_dir` (CC), `workspaces` (Antigravity), `worktree` (T3), `project` (T3) |
| `env` | `env_vars` (CC, OpenCode, Antigravity, ACP) |
| `execution` | `environment` (exxec config, for agentpool's own toolsets), ACP `client_execution_environment` |
| `sandbox` | `sandbox` (Codex), `enable_sandbox` / `workspace_only` (Antigravity) |

### Policy: one concept, seven spellings

| Harness | Field(s) |
|---|---|
| all (agentpool tools) | `requires_tool_confirmation: always / never / per_tool` |
| claude_code | `permission_mode` |
| codex | `approval_policy`, `sandbox` |
| opencode_v2 | `permission_mode`, `permissions` |
| antigravity | `policy_mode`, `behavior` |
| t3 | `runtime_mode`, `interaction_mode` |
| acp | `auto_approve` |

A portable `policy.autonomy` (for example `ask | edits | full`) plus `policy.sandbox` could map to
all of these. Fine-grained rules stay native. Two parts of a policy can't be enforced by mapping:
whether pushes go to main or a PR, and token scopes. For those the policy only describes;
GitHub (branch protection, token permissions) has to enforce.

### Harness options (not portable)

- **claude_code:** `setting_sources`, `use_subscription`, `max_processes`, `prompt_suggestions`,
  `enable_tool_search`, `idle_compaction`
- **codex:** `personality`, `base_instructions`
- **opencode_v2:** `url`, `password`, `command`, `startup_timeout`, `headers`, `agent`
- **antigravity:** `service_tier`, `api_key`, `vertex`, `project`, `location`, `base_url`,
  `http_headers`, `openai_compatible`, `enable_daemons`, `command_timeout`,
  `tool_output_max_tokens`, `mcp_max_output_bytes`, `save_dir`, `app_data_dir`,
  `compaction_token_threshold`, `debug`
- **t3:** `login`, `url`, `token`, `options`, `poll_interval`, `native`, `native_token`
- **acp:** `provider`, `command`, `args`, `registry_id`, `extra_args`, `url`, `headers`
- **agui:** `endpoint`, `timeout`, `headers`, `startup_command`, `startup_delay`
- **native:** `retries`, `output_retries`, `end_strategy`, `tool_mode`, `model_providers`,
  `session`, `knowledge` (could become portable if another harness gains it)

Connection details (`url`, `headers`, `token`, `command`) recur, but they describe how to reach
the harness, not the agent, so they stay native.

## Shape

The agent *is* the role: role fields sit flat on the agent. `harness:` is the single place
where everything harness-specific connects, as a reference or inline. Reuse goes through
`extends:`.

```yaml
harnesses:
  claude_code: {use_subscription: true, setting_sources: [project], model: opus}  # type defaults

agents:
  coordinator:
    harness: claude_code                         # type name = preset
    effort: high
    instructions: file://prompts/coordinator.md
  reviewer:
    harness: {type: codex, sandbox: read-only}   # inline when unique
    model: openai:gpt-5.5                        # canonical: provider checked statically
    effort: high
    builtin_tools: [read, shell]
    instructions: file://prompts/reviewer.md
  fixer:
    harness: claude_code
    model: sonnet                                # harness-native, overrides the preset default
    instructions: file://prompts/fixer.md
  second_reviewer:
    extends: reviewer                            # inherit another agent
    harness: claude_code
```

Today's flat form stays valid as sugar:

```yaml
agents:
  old_style:
    type: claude_code
    model: opus
    system_prompt: "..."
    permission_mode: acceptEdits
```

The loader moves `type` and the harness options into `harness`, and maps renamed portable
fields (`system_prompt` → `instructions`, `reasoning_effort` → `effort`).

## Where it lives

**Extend `AgentsManifest`; don't add a parallel manifest.** A new optional `harnesses` section,
and `agents.*` accepts both the flat and the new form. A second root file would duplicate what
the manifest already owns (MCP servers, storage, resources, `INHERIT`, resolution) and leave
two sources of truth for what an agent is.

**File conventions are for loading, not for meaning.** Agents may come from markdown files with
frontmatter (as `file_agents` today), referenced explicitly (`extends: file://agents/reviewer.md`
or a declared glob). Nothing is picked up just because a file has a certain name, so a
manifest always shows everything it loads.

**The crew layer is a separate file type that consumes manifests.** Profiles, the repo list,
schedules and the GitHub label flow are about repos, not agents, so they don't belong in
`AgentsManifest`. A `crew.yml` (central profiles plus a per-repo stub that `INHERIT`s a
profile) refers to agents and harnesses in a manifest and adds what's specific to its repo.

## Migration

1. **Internal split.** Introduce `HarnessConfig` (discriminated by `type`) and a single
   `AgentConfig` with the portable fields. Parse the flat configs into them; agent classes
   consume the new form. No YAML change for users.
2. **Adapters.** One per harness: portable → harness mapping (format translation, clamping),
   declared support, validation errors for unsupported or overlapping fields. Extend or mirror
   `AgentCapabilities` for config-time checks.
3. **Expose the new YAML** (`harnesses`, `agent.harness`, `extends`) next to the flat form.
   Generate the schema and docs.
4. **Unify the registries.** Fold `file_agents` into "an agent loaded from a file"
   (`extends: file://...md`). Check the remaining manifest dicts against rule 4.
5. **Crew layer on top**, once the above works.

## Decisions

### Builtin tools: coarse groups that state intent, sandbox as the guarantee

- *Builtin tool* = a tool that runs inside the harness or provider, not through agentpool
  (Claude Code `Read`/`Bash`, Antigravity `view_file`/`run_command`, Codex's shell).
- Roles name **groups**: `builtin_tools: [read, edit, shell, web, subagents, ask]`. Each external
  harness adapter maps them to its own allow/deny lists. Tools with no counterpart elsewhere
  (`Chrome`, `generate_image`, `schedule`, …) are set in the harness options.
- Groups state **intent**, not a guarantee: a shell can read, edit and fetch anyway, so
  `[read, shell]` can't honestly mean "no edits". Combinations a harness can't map are not
  rejected; the adapter removes what it can.
- The guarantee is `policy.sandbox: read-only | workspace | full`, mapped to the strongest
  mechanism the harness has (Codex `sandbox`, Claude Code permission mode / sandbox, Antigravity
  `workspace_only`). This is **strict**: if a harness can't enforce the requested level,
  validation fails.
- Agents where agentpool runs the loop have no builtin set to restrict, so groups don't apply.
  They list agentpool tools explicitly. pydantic-ai's provider-side tools (`code_execution`,
  `image_generation`, `memory`, `file_search`, `mcp_server`, `web_search`, `web_fetch`) are
  harness options, not portable. Whether they're available depends on the model provider, so
  validation needs the resolved model.
- "Capability" keeps its existing meaning (`AgentCapabilities`: what a harness supports).

### Naming

- `type: native` is renamed (to `agentpool`: agentpool runs the loop itself; `model_api`
  was the alternative). `native` stays as a deprecated alias in the flat form.
- pydantic-ai's provider-side tools are then called *native tools*, matching upstream
  (`pydantic_ai.native_tools`). The `type: builtin` tool config follows.
- The harness-specific fields under `harness:` are called **harness options**.

### Effort: adapter Literals, pydantic-ai vocabulary, two levels of strictness

- **Each adapter owns its levels** as a Literal, used in the schema of its harness options:
  Claude Code `low … max`, Antigravity `minimal … xhigh`. A value set in the harness options
  is validated strictly against it. Codex has no static set (`codexed.ReasoningEffort` is
  a string the model advertises), so its harness option is a string and only the runtime
  check below applies.
- **The portable `effort` field uses pydantic-ai's `ThinkingEffort` plus `none`**
  (`none | minimal | low | medium | high | xhigh | max`), the superset of every harness.
  `none` turns thinking off (or uses the lowest level where it can't be turned off); unset
  means the harness default. Both SDKs have the same two cases (pydantic-ai `False` / unset,
  AI SDK `none` / `provider-default`). Agents
  where agentpool runs the loop pass it through as pydantic-ai's unified `thinking` setting.
- **Portable values that don't fit are clamped**, not rejected: each adapter maps to the
  nearest level it supports, searching downwards first and then upwards, and logs a
  `compatibility` warning (`unsupported` if nothing fits, and the setting is dropped) (`minimal → low` on Claude Code, `max → xhigh` on
  Antigravity), the same way pydantic-ai maps per provider. A role meant for several harnesses
  shouldn't break on one of them.
- **What a model supports is checked at runtime**: when the agent starts, through live model
  discovery (`AgentCapabilities.live_model_discovery`, Codex's model list). If the model
  can't do the level, warn and clamp.
- The `thought_level` mode category in `modes.py` uses the same IDs, so config and runtime
  share one vocabulary.

### YAML shape: the agent is the role, the harness is the one connection point

- **Portable agent fields:** `model`, `instructions`, `effort`, `output_type`, `tools`
  (agentpool tools), `builtin_tools` (groups), `skills`, `mcp_servers`.
- **Role fields sit flat on the agent.** They're what makes the agent, and almost always
  unique to it, so a `role:` level or a `roles:` section adds nothing. Inlining stays the default:
  one agent reads at a glance.
- **`harness: str | HarnessConfig`.** A string names a preset, an object is inline (a
  discriminated union on `type`, so the IDE completes and validates the options per harness).
- **Harness type names are presets.** `harness: codex` works without defining anything;
  `harnesses: {claude_code: {…}}` sets the defaults for every Claude Code agent; other names
  (`cc_fast: {type: claude_code, …}`) are for when several are needed.
- **The agent model is not discriminated.** It's one model; harness-specific typing lives
  only in `harness`. Portable fields are therefore not validated per harness when the harness
  is a string reference; mismatches are clamped (see Effort). If strict per-harness validation
  is wanted later, a `type/preset` reference string (`claude_code/fast`) allows static
  discrimination without breaking anything.
- **Reuse is `extends:`** (inherit another agent, or a markdown agent file). One level only.

### Model: on the agent, in two forms

- `model` stays on the agent, not in the harness. In the harness, agents sharing a preset with
  different models would need a nested override.
- **Canonical `provider:model`** (pydantic-ai format, e.g. `anthropic:claude-opus-5-5`): each
  adapter translates the format (`anthropic/claude-opus-5-5` for OpenCode) and checks the
  provider at config time (`anthropic:…` on Codex is an error).
- **No colon = harness-native** (`opus`, `gpt-5.5`): passed through, checked at runtime
  against the model list.
- A harness preset may set `model` as a default; the agent overrides it. That's a default
  plus override, not an overlap under rule 3.
- The rich `AnyModelConfig` (fallback chains, settings) stays an option of the agentpool
  harness. Aliases resolved per harness (`model: strong`, perhaps via `model_variants`) are
  left for later.

### Scope of the first version

- **No policy layer yet.** Assume a flat-rate plan: no money limits, no plan-window handling.
  `sandbox` (the guarantee from the builtin-tools decision) sits on the harness / agent for
  now and moves to a policy layer when the crew needs one.
- The focus is a well-typed, readable YAML.

## Prior art

Checked against pydantic-ai 2.68, the AI SDK 7 (provider spec V4), and AI SDK Harnesses
(`ai-sdk.dev/docs/ai-sdk-harnesses`, experimental).

**Where the line between portable and specific is drawn.** All three put about the same things
on the portable side: instructions, model, reasoning effort (not in Harnesses), structured
output, tools with allow/deny, tool approval, and a step or turn limit (`stopWhen: stepCountIs(n)`,
`UsageLimits.request_limit`). Everything else is provider- or harness-specific: AI SDK
`providerOptions` keyed by provider, pydantic-ai provider-prefixed settings keys
(`anthropic_thinking`), AI SDK Harnesses adapter factory options
(`createCodex({reasoningEffort: 'high'})`).

**AI SDK Harnesses is close to this proposal.** `HarnessAgent` holds the portable fields,
the adapter factory holds the harness options, and the sandbox is a separate object (our
Environment). `model` is on the agent with a harness-specific ID. Builtin tools get normalised
names (`read`, `write`, `edit`, `bash`, `grep`, `glob`, `webSearch`) filtered with
`activeTools` / `inactiveTools`; skills are portable; `permissionMode` has three levels
(`allow-all | allow-edits | allow-reads`), and the docs say it is not a filesystem sandbox:
isolation comes from the sandbox provider. Each adapter declares capabilities (custom tools,
skills, structured output, builtin approval, builtin filtering, image input, history).

**Mismatches.** Model settings are adapted with warnings and never fail: AI SDK
`mapReasoningToProviderEffort` (`@ai-sdk/provider-utils`) uses the provider value if set,
otherwise the nearest supported level (downwards first), with `compatibility` /
`unsupported` warnings; token-budget providers get a percentage of `maxOutputTokens`.
Harness capabilities are strict: AI SDK Harnesses throws `HarnessCapabilityUnsupportedError`
(structured output, filtering builtins, unsupported approval modes). This proposal follows the
same split: clamp settings, be strict about capabilities and guarantees.

**Usage.** AI SDK `LanguageModelV4Usage`: `inputTokens {total, noCache, cacheRead, cacheWrite}`,
`outputTokens {total, text, reasoning}`, plus the provider's `raw` usage; no cost in the core.
A reasonable shape for agentpool's usage events when limits come back.

## Open questions

- **Limits (parked with policy):** turns, USD and tokens are different currencies. Use the
  harness's limit where it has one and let agentpool enforce the rest from usage events;
  reject only where the harness reports no usage. agentpool's `agents/usage_limits.py`
  `UsageLimits` (plan windows) clashes by name with pydantic-ai's `UsageLimits` (run limits).
- **Runtime changes:** `modes.py` lets clients switch model/mode at runtime. Should a later
  `autonomy` setting map to the same mode IDs, so config and runtime speak one vocabulary?
- **Agent files:** markdown agent files (Claude / OpenCode frontmatter) as an input format
  for agents (`extends: file://…md`), with frontmatter fields mapped like flat-form fields.
