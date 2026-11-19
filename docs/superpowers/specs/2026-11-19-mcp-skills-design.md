# Skills over MCP (SEP-2640) — Design

Date: 2026-11-19
Status: Draft, awaiting review

## Goal

Support the MCP Skills extension (`io.modelcontextprotocol/skills`, SEP-2640, written
against base protocol `2026-07-28`) in both directions:

1. **Consume**: skills published by connected MCP servers become available to the
   agent that owns the server connection, through the existing skills tools.
2. **Serve**: `agentpool serve-mcp` publishes the pool's skills through the extension.

FastMCP 4.2.0 (already the floor in `pyproject.toml`) implements the protocol layer
on both sides; this design is about wiring it into agentpool's skill model.

## Non-goals

- Executing skill scripts. As in pydantic-ai-harness `Skills`, the skills layer only
  serves instructions and file reads; execution is the job of the agent's own
  file/shell tools, which can only reach local skills.
- Exposing skills as pydantic-ai deferred `Capability` objects (`load_capability`).
  Candidate for the later skills refactor; it would only work for native agents.
- `resources/directory/read` (`directoryRead`): FastMCP does not implement it, and
  skill entries already carry the full file list.
- Skills whose entry has `resources: "dynamic"`: they cannot be verified, so they are
  skipped with a log message (matching FastMCP's own proxy behaviour).
- Moving pool-level skills discovery into resource providers (the "bigger refactor").

## Prior art adopted from pydantic-ai-harness `Skills` (v0.70)

- Unknown frontmatter fields are accepted; known-but-unimplemented "behavioral"
  fields (`allowed-tools`, `hooks`, `shell`, `model`, ...) are recorded and reported.
- `when_to_use`, `disable-model-invocation`, `user-invocable` frontmatter fields.
- Rendering shows the skill directory only when the agent's own tools can reach it,
  and substitutes `${SKILL_DIR}` / `${CLAUDE_SKILL_DIR}` in that case.
- A `read_skill_file(skill, path, offset)` tool with path containment, a byte cap, and
  character windowing.

The harness itself is not taken as a dependency: it is bound to pydantic-ai
workspaces and native agents, and cannot read UPath/MCP sources.

## Architecture

```
            consume                                              serve
MCP server ──skills/list──▶ MCPClient ──▶ MCPResourceProvider    AgentPool.skills
           ◀─resources/read─ MCPSkillsFileSystem (verifies)          │
                                   │ get_skills()                    ▼
                                   ▼                         PoolSkillsProvider
               AggregatingResourceProvider.get_skills()      (SkillCatalogProvider)
                                   │                                 │
                                   ▼                                 ▼
          SkillsTools: list_skills / load_skill / read_skill_file   FastMCP (serve-mcp)
          (pool skills + calling agent's provider skills)
```

### 1. `Skill` model (`src/agentpool/skills/skill.py`)

Changes:

- Frontmatter: stop rejecting unknown keys. A `mode="before"` validator moves every key
  that is not a declared field into `extra_frontmatter: dict[str, JsonValue]`.
  The model keeps `extra="forbid"` for its own constructor arguments, so typos in code
  still fail.
- New fields: `when_to_use: str | None`, `disable_model_invocation: bool`
  (alias `disable-model-invocation`), `user_invocable: bool` (alias `user-invocable`).
- New field `frontmatter: dict[str, JsonValue]` (excluded from dumps): the frontmatter
  exactly as parsed, needed to serve entries whose frontmatter matches the file.
- New field `agent_accessible: bool = True`: whether the agent's own file/shell tools
  can reach `skill_path`. `False` for MCP skills.
- New property `ignored_behavioral_fields: tuple[str, ...]`: intersection of the
  frontmatter keys with the harness's behavioral field set.
- `allowed_tools`: unchanged for local skills; always `None` for MCP skills (the spec
  forbids honouring it without explicit user approval, and we have no approval flow).
- Instruction loading becomes async: `async def load_instructions() -> str` reads
  `SKILL.md` through `skill_path`'s filesystem (works for local, remote and MCP
  paths). The sync variant is removed; its three callers (registry, manager,
  `load_skill`) move to the async one.
- New async file access, shared by every source:
  - `async def list_files() -> list[str]`: relative paths of all files in the skill.
  - `async def read_file(path: str) -> bytes`: one file; raises `ValueError` for absolute
    paths or paths escaping the skill directory, `FileNotFoundError` for missing files.
- New `render() -> str`: the text `load_skill` returns (heading, description,
  license/compatibility/allowed-tools lines, body, and — only when `agent_accessible` —
  the skill directory line plus `${SKILL_DIR}`/`${CLAUDE_SKILL_DIR}` substitution).
  This replaces the formatting currently inlined in `load_skill`.

### 2. `MCPSkillsFileSystem` (`src/agentpool/mcp_server/skills_fs.py`, new)

A read-only async fsspec filesystem (`BaseAsyncFileSystem` from upathtools) over one
MCP connection's skill entries. It is the single place that enforces the spec's
host-side read rules.

- Constructed with the agentpool `MCPClient` and a mapping of skill path → `MCPSkill`
  entry; `set_entries()` replaces the mapping on refresh.
- Paths are the skill URI without the scheme: `<skill-path>/<file-path>`.
- `_ls` / `_info` / `_exists` / `_isdir` / `_isfile` are answered from the entries
  alone (directories are derived from the file list); no network traffic.
- `_cat_file(path)`:
  - refuses (`FileNotFoundError`) any file not listed in the held entry;
  - reads it with `resources/read`;
  - verifies byte size and `sha256:` digest against the entry; a mismatch raises
    `SkillVerificationError(ValueError)`;
  - caches verified bytes in memory keyed by digest.
- Write operations raise `PermissionError`.
- Instruction loading additionally checks the parsed `SKILL.md` frontmatter against the
  entry's frontmatter (spec requirement) and raises `SkillVerificationError` on mismatch.

### 3. `MCPClient` (`src/agentpool/mcp_server/client.py`)

- Pass `extensions=[SkillsClientExtension()]` to `fastmcp.Client`.
- `supports_skills: bool` property: true when the server advertises
  `io.modelcontextprotocol/skills` in `capabilities.extensions`.
- `async def list_skills() -> list[MCPSkill]`: `[]` when unsupported, otherwise the
  paginated `skills/list` result.
- `async def read_resource_bytes(uri: str) -> bytes`: text or base64 blob content as
  bytes, used by the filesystem.

### 4. `MCPResourceProvider.get_skills()` (`src/agentpool/resource_providers/mcp_provider.py`)

- Lazily builds and caches `list[Skill]` from `client.list_skills()`:
  - entries with `resources == "dynamic"` are skipped (logged);
  - each remaining entry becomes a `Skill` built from the entry's frontmatter (no file
    is fetched), with `skill_path` = a UPath on the provider's `MCPSkillsFileSystem`,
    `agent_accessible=False`, `allowed_tools=None`;
  - entries whose frontmatter fails `Skill` validation are skipped with a warning.
- Cache invalidation: on the resource list-changed notification (the spec defines no
  skills notification) and on explicit `refresh_skills_cache()`; both emit
  `skills_changed`.
- `get_skill_instructions(name)` implemented via the cached skill.
- New config flag on `BaseMCPServerConfig`: `skills: bool = True`. When `False` the
  provider does not opt into the extension and returns no skills.

### 5. `AggregatingResourceProvider.get_skills()`

Concatenates the children's skills. Each `Skill` carries its origin, so no dedup here.

### 6. Skills tools (`src/agentpool_toolsets/builtin/skills.py`)

The catalog seen by a tool call is: pool skills (`ctx.pool.skills`) plus the skills from
the calling agent's resource providers (`ctx.agent.tools.providers`).

Naming / no shadowing (spec requirement: identity is server + URI):

- Pool (local) skills keep their bare name.
- MCP skills are addressed as `<provider-name>/<skill-name>`, always — never bare, so
  an MCP skill can never shadow a local one or another server's.

Tools:

- `list_skills`: lists both groups, MCP ones under their qualified name; skills with
  `disable-model-invocation: true` are omitted.
- `load_skill(skill_name)`: resolves the name, returns `await skill.render()`; logs a
  warning listing `ignored_behavioral_fields`.
- `read_skill_file(skill_name, path, offset=0)` (new; added to `SkillsToolName`):
  - relative paths only; containment enforced by `Skill.read_file` (and for MCP skills
    additionally by the manifest);
  - files over 10 MiB refused; UTF-8 text only;
  - returns at most 50 000 characters from `offset`, with a trailing
    "read on from offset=N" hint when truncated;
  - model-fixable problems (unknown skill, bad path, missing file, offset past end,
    non-UTF-8) raise `ModelRetry`; verification failures raise `ToolError`.
  - Unlike the harness, it does not require the skill to be loaded first: path
    containment is the actual protection, and tracking loaded state would need
    per-session state we do not have in this toolset.

### 7. Serving (`src/agentpool_server/mcp_server/skills_provider.py`, new)

- `PoolSkillsProvider(SkillCatalogProvider)` whose `load_skills()` converts the pool's
  skills to FastMCP `Skill` objects via `fastmcp.skills.Skill.from_files()`, reading
  every file through `Skill.list_files()` / `Skill.read_file()` (so remote and ACP-backed
  skills work too).
- Converted skills are cached per `agentpool` `Skill` instance (registry refresh replaces
  instances, which invalidates naturally).
- Skills that FastMCP rejects (limits: 512 files / 16 MiB, name/frontmatter rules) are
  skipped with a warning instead of failing the request.
- Registered in `MCPServer.__init__` via `self.fastmcp.add_provider(...)`; the provider
  registers `SkillsExtension` itself.
- New flag on `MCPPoolServerConfig`: `skills: bool = True`.

## Error handling summary

| Situation | Behaviour |
| --- | --- |
| Server lacks the extension / `skills: false` | no MCP skills, no error |
| `skills/list` fails | logged, provider returns `[]`, retried on next call |
| Entry with `resources: "dynamic"` | skipped, logged |
| Invalid frontmatter in an entry | skipped, warning |
| File read: digest/size/frontmatter mismatch | `SkillVerificationError` → `ToolError` |
| File read: path not in entry / escapes skill dir | `ModelRetry` |
| Serving: skill violates FastMCP limits | skipped, warning |

## Testing

All MCP tests use an in-process FastMCP server (`fastmcp.Client(FastMCP(...))`) serving
skills from a temporary directory via `SkillsDirectoryProvider`.

- `Skill` model: unknown frontmatter accepted, behavioral fields reported,
  `when_to_use` / `disable-model-invocation` parsed, `render()` with and without
  `agent_accessible`, `read_file` containment.
- `MCPSkillsFileSystem`: listing from entries without network, successful verified read,
  refused unlisted path, digest mismatch (server serving altered bytes), frontmatter
  mismatch.
- `MCPResourceProvider.get_skills()`: skills discovered, `dynamic` skipped, `skills: false`
  respected, cache invalidated on resource list change.
- Skills tools: qualified naming, no shadowing between local and MCP skill of the same
  name, `read_skill_file` windowing / size cap / path errors.
- Serving: loopback — `MCPServer` with pool skills, FastMCP client `read_skill()` returns
  identical, verified content; oversize skill skipped.
- Existing `tests/test_skills/test_skills.py` updated for async `load_instructions`.

## Follow-up (out of scope)

- Pool-level skills discovery through resource providers (unify `SkillsManager` with
  provider skills).
- Deferred `Capability` exposure for native agents.
- Opt-in materialization of an MCP skill to disk so its scripts can run.
