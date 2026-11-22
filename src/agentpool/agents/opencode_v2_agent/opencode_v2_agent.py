"""OpenCode V2 agent - wraps an OpenCode v2.3+ HTTP/SSE server."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from pathlib import Path
import secrets
import socket
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, assert_never
from uuid import uuid4

from anyenv.processes import hard_kill
import anyio
import httpx

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import AgentNotInitializedError
from agentpool.agents.opencode_v2_agent.converters import (
    elicitation_to_form_answer,
    form_to_elicitation,
    format_model_ref,
    from_usage_info,
    mcp_config_to_v2,
    parse_model_ref,
    sanitize_mcp_name,
    to_mcp_server_status,
    to_session_data,
    to_tokonomics_model_info,
    user_content_to_prompt_body,
    v2_messages_to_chat_messages,
)
from agentpool.agents.opencode_v2_agent.events import V2EventTracker
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.messaging.messages import TokenCost
from opencode_v2_sdk.client import OpenCodeV2Client, OpenCodeV2Error
from opencode_v2_sdk.models import (
    FormCreated,
    LocationPublicRef,
    McpRemoteConfigEncoded,
    PermissionAsked,
    PermissionRule,
    SessionCreateBody,
    SessionUpdateBody,
    V2EventServerConnected,
)


if TYPE_CHECKING:
    from asyncio.subprocess import Process
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from pydantic_ai import UserContent
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ConfigOption
    from agentpool.agents.usage_limits import UsageLimits
    from agentpool.common_types import AnyEventHandlerType, MCPServerStatus, StrPath
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.opencode_v2_agents import (
        OpenCodePermissionMode,
        OpenCodePermissionRuleConfig,
        OpenCodeV2AgentConfig,
    )
    from agentpool.resource_providers import ResourceProvider
    from agentpool.sessions import SessionData
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig
    from opencode_v2_sdk.client import PermissionDecision
    from opencode_v2_sdk.models import (
        AgentInfo,
        FormInfo1,
        ModelInfo,
        PermissionAskedData,
    )


logger = get_logger(__name__)

_SSE_CONNECT_GRACE_SECONDS = 0.5


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class OpenCodeV2Agent[TDeps = None](BaseAgent[TDeps, str]):
    """Agent backed by an OpenCode V2 (v2.3+) HTTP/SSE server.

    Either spawns and manages a private ``opencode serve`` process or connects
    to an existing OpenCode V2 server at ``url``. One AgentPool session maps to
    one OpenCode V2 session, streaming live events from ``GET /api/event`` while
    bridging permissions, forms, and AgentPool toolsets (via MCP).
    """

    AGENT_TYPE: ClassVar = "opencode_v2"
    TITLES_OWN_SESSIONS: ClassVar = True

    def __init__(
        self,
        *,
        url: str | None = None,
        password: str | None = None,
        command: str = "opencode",
        startup_timeout: float = 15.0,
        headers: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        agent: str | None = None,
        model: str | None = None,
        variant: str | None = None,
        permission_mode: OpenCodePermissionMode = "allow",
        permissions: Sequence[OpenCodePermissionRuleConfig] | None = None,
        toolsets: list[ResourceProvider] | None = None,
        client: OpenCodeV2Client | None = None,
        session_id: str | None = None,
        deps_type: type[TDeps] | None = None,
        name: str = "opencode_v2",
        description: str | None = None,
        display_name: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        env: ExecutionEnvironment | StrPath | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        input_provider: InputProvider | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
    ) -> None:
        """Create an OpenCode V2 agent; connects or spawns ``opencode serve`` on enter.

        Args:
            url: Base URL of an existing OpenCode V2 server (spawns one when omitted)
            password: Basic-auth password for ``url``
            command: Executable name/path for ``opencode serve`` when ``url`` is omitted
            startup_timeout: Seconds to wait for a managed server to accept ``/api/info``
            headers: Additional HTTP headers sent with API requests
            env_vars: Extra environment variables for a managed ``opencode serve`` subprocess
            agent: OpenCode agent ID to select on the session
            model: Model ID (``provider/model`` or bare model ID)
            variant: Model variant (e.g. reasoning effort)
            permission_mode: Tool permission handling (``allow``, ``ask``, ``deny``)
            permissions: Explicit OpenCode V2 permission rules (overrides ``permission_mode`` rules)
            toolsets: AgentPool resource providers exposed via an in-process MCP bridge
            client: Pre-configured ``OpenCodeV2Client`` (its owner manages lifecycle)
            session_id: Existing OpenCode V2 session ID to resume on connect
            deps_type: Dependency type
            name: Agent name
            description: Agent description
            display_name: Human-readable display name
            mcp_servers: External MCP server configurations registered on the server
            env: Execution environment or working directory path
            agent_pool: Pool owning this agent
            enable_logging: Whether to log to storage
            event_configs: Event trigger configurations
            input_provider: Provider for tool confirmations and form elicitations
            event_handlers: Stream event handlers
            commands: Slash commands
            hooks: Agent lifecycle hooks
        """
        from agentpool.mcp_server.tool_bridge import ToolManagerBridge
        from agentpool_config.mcp_server import BaseMCPServerConfig

        super().__init__(
            name=name,
            deps_type=deps_type,
            description=description,
            display_name=display_name,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            env=env,
            input_provider=input_provider,
            event_handlers=event_handlers,
            commands=commands,
            hooks=hooks,
        )
        self._url = url
        self._password = password
        self._command = command
        self._startup_timeout = startup_timeout
        self._headers = dict(headers or {})
        self._env_vars = dict(env_vars or {})
        self._opencode_agent = agent
        self._model = model
        self._variant = variant
        self._permission_mode: OpenCodePermissionMode = permission_mode
        self._explicit_permissions = list(permissions) if permissions is not None else None
        self._toolsets = toolsets or []
        self._external_mcp_servers = [
            BaseMCPServerConfig.from_string(s) if isinstance(s, str) else s
            for s in mcp_servers or []
        ]
        self._tool_bridge = ToolManagerBridge(node=self, injection_manager=self._injection_manager)
        self._bridge_started = False
        self._registered_mcp_names: list[str] = []

        self._client = client
        self._owns_client = client is None
        self._process: Process | None = None
        self._sdk_session_id: str | None = session_id
        self._cached_models: list[ModelInfo] | None = None
        self._active_model_name: str | None = model

    @classmethod
    def from_config(
        cls,
        config: OpenCodeV2AgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create an OpenCode V2 agent from its configuration model."""
        handlers: list[AnyEventHandlerType] = [
            *config.get_event_handlers(),
            *(event_handlers or []),
        ]
        return cls(
            url=config.url,
            password=config.get_password(),
            command=config.command,
            startup_timeout=config.startup_timeout,
            headers=config.get_headers(),
            env_vars=config.get_env_vars(),
            agent=config.agent,
            model=config.model,
            variant=config.variant,
            permission_mode=config.permission_mode,
            permissions=config.permissions,
            toolsets=config.get_tool_providers(),
            mcp_servers=config.get_mcp_servers(),
            name=config.name or "opencode_v2",
            deps_type=deps_type,
            description=config.description,
            display_name=config.display_name,
            env=config.get_execution_environment(),
            agent_pool=agent_pool,
            input_provider=input_provider,
            event_handlers=handlers or None,
            hooks=config.hooks.get_agent_hooks(),
        )

    # --- Connection & subprocess lifecycle -------------------------------------------

    def _directory(self) -> str:
        return str(self.env.cwd or Path.cwd())

    async def _spawn_server(self) -> tuple[str, str]:
        """Spawn a private ``opencode serve`` subprocess and wait until ``/api/info`` responds."""
        port = _pick_free_port()
        password = secrets.token_urlsafe(24)
        url = f"http://127.0.0.1:{port}"
        proc_env = {
            **os.environ,
            **self._env_vars,
            "OPENCODE_PASSWORD": password,
            "OPENCODE_SERVER_PASSWORD": password,
        }
        self._process = await asyncio.create_subprocess_exec(
            self._command,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=self._directory(),
            env=proc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        deadline = time.monotonic() + self._startup_timeout
        probe = OpenCodeV2Client(url, password=password, directory=self._directory(), timeout=2.0)
        try:
            await self._wait_for_server(probe, url, deadline)
        except Exception:
            await self._stop_process()
            raise
        return url, password

    async def _wait_for_server(self, probe: OpenCodeV2Client, url: str, deadline: float) -> None:
        async with probe:
            while time.monotonic() < deadline:
                if self._process is not None and self._process.returncode is not None:
                    stderr = b""
                    if self._process.stderr is not None:
                        stderr = await self._process.stderr.read()
                    msg = (
                        f"`{self._command} serve` exited with code {self._process.returncode}: "
                        f"{stderr.decode('utf-8', errors='replace').strip()}"
                    )
                    raise RuntimeError(msg)
                try:
                    await probe.info()
                except (httpx.HTTPError, OpenCodeV2Error):
                    await anyio.sleep(0.1)
                else:
                    return
        msg = (
            f"`{self._command} serve` did not become ready at {url} within {self._startup_timeout}s"
        )
        raise TimeoutError(msg)

    async def _stop_process(self) -> None:
        if self._process is None:
            return
        proc = self._process
        self._process = None
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                await hard_kill(proc)
                with suppress(Exception):
                    await proc.wait()

    async def _register_mcp_servers(self, client: OpenCodeV2Client) -> None:
        servers_to_add: list[tuple[str, Any]] = []
        if self._toolsets:
            for provider in self._toolsets:
                self.tools.add_provider(provider)
            await self._tool_bridge.start()
            self._bridge_started = True
            bridge_name = sanitize_mcp_name(self._tool_bridge.resolved_server_name)
            servers_to_add.append((
                bridge_name,
                McpRemoteConfigEncoded(url=self._tool_bridge.url, oauth=False),
            ))
        servers_to_add.extend(
            converted
            for cfg in self._external_mcp_servers
            if (converted := mcp_config_to_v2(cfg)) is not None
        )

        for name, mcp_cfg in servers_to_add:
            try:
                await client.add_mcp_server(name, mcp_cfg)
                self._registered_mcp_names.append(name)
                with suppress(OpenCodeV2Error, httpx.HTTPError):
                    await client.connect_mcp_server(name)
            except OpenCodeV2Error as exc:
                self.log.warning(
                    "OpenCode V2 server declined MCP registration",
                    server=name,
                    status_code=exc.status_code,
                    error=str(exc),
                )

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        if self._owns_client:
            if self._url is None:
                base_url, password = await self._spawn_server()
            else:
                base_url = self._url
                password = (
                    self._password
                    or os.environ.get("OPENCODE_PASSWORD")
                    or os.environ.get("OPENCODE_SERVER_PASSWORD")
                )
            self._client = OpenCodeV2Client(
                base_url,
                password=password,
                directory=self._directory(),
                headers=self._headers,
            )
            await self._client.__aenter__()
        if self._client is None:
            raise AgentNotInitializedError
        info = await self._client.info()
        self.log.info(
            "Connected to OpenCode V2 server",
            url=self._client.base_url,
            version=info.version,
        )
        await self._register_mcp_servers(self._client)
        if self._sdk_session_id is not None:
            await self.load_session(self._sdk_session_id)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if self._client is not None:
                for name in self._registered_mcp_names:
                    with suppress(OpenCodeV2Error, httpx.HTTPError):
                        await self._client.remove_mcp_server(name)
                self._registered_mcp_names.clear()
                if self._owns_client:
                    await self._client.aclose()
                    self._client = None
        finally:
            if self._bridge_started:
                with suppress(Exception):
                    await self._tool_bridge.stop()
                self._bridge_started = False
            await self._stop_process()
            await super().__aexit__(exc_type, exc_val, exc_tb)

    def _require_client(self) -> OpenCodeV2Client:
        if self._client is None:
            raise AgentNotInitializedError
        return self._client

    # --- Session helpers --------------------------------------------------------------

    def _permission_rules(self) -> list[PermissionRule]:
        if self._explicit_permissions is not None:
            return [
                PermissionRule(action=r.action, resource=r.resource, effect=r.effect)
                for r in self._explicit_permissions
            ]
        effect: Literal["allow", "deny", "ask"] = self._permission_mode
        return [PermissionRule(action="*", resource="*", effect=effect)]

    async def _fetch_models_raw(self) -> list[ModelInfo]:
        if self._client is None:
            return []
        try:
            self._cached_models = await self._client.list_models()
        except Exception:
            self.log.exception("Failed to list OpenCode V2 models")
            return self._cached_models or []
        return self._cached_models

    async def _fetch_server_agents(self) -> list[AgentInfo]:
        if self._client is None:
            return []
        try:
            return await self._client.list_agents()
        except Exception:
            self.log.exception("Failed to list OpenCode V2 agents")
            return []

    async def _current_model_variants(self) -> list[str]:
        models = self._cached_models or await self._fetch_models_raw()
        target = self._model or self._active_model_name
        if not target:
            return []
        for model in models:
            if target in (f"{model.provider_id}/{model.id}", model.id, model.model_id):
                return [v.id for v in model.variants]
        return []

    async def _resolve_configured_model_ref(self) -> Any:
        if self._model is None:
            return None
        models = self._cached_models or await self._fetch_models_raw()
        return parse_model_ref(self._model, variant=self._variant, available_models=models)

    async def _sync_session_model(self) -> None:
        if self._client is None or self._sdk_session_id is None or self._model is None:
            return
        ref = await self._resolve_configured_model_ref()
        if ref is not None:
            await self._client.switch_model(self._sdk_session_id, ref)
            self._active_model_name = format_model_ref(ref)

    async def _sync_session_permissions(self) -> None:
        if self._client is None or self._sdk_session_id is None:
            return
        await self._client.update_session(
            self._sdk_session_id,
            SessionUpdateBody(permissions=self._permission_rules()),
        )

    async def _ensure_session(self, client: OpenCodeV2Client) -> str:
        if self._sdk_session_id is not None:
            return self._sdk_session_id
        model_ref = await self._resolve_configured_model_ref()
        body = SessionCreateBody(
            agent=self._opencode_agent,
            model=model_ref,
            location=LocationPublicRef(directory=self._directory()),
            permissions=self._permission_rules(),
        )
        info = await client.create_session(body)
        self._sdk_session_id = info.id
        if info.model is not None:
            self._active_model_name = format_model_ref(info.model)
        if self.storage is not None and self.session_id is not None:
            await self.storage.update_sdk_session_id(self.session_id, info.id)
        return info.id

    # --- Streaming turns --------------------------------------------------------------

    async def _handle_permission(
        self,
        client: OpenCodeV2Client,
        data: PermissionAskedData,
        provider: InputProvider | None,
    ) -> None:
        decision: PermissionDecision
        if self._permission_mode == "allow":
            decision = "once"
        elif self._permission_mode == "deny" or provider is None:
            decision = "reject"
        else:
            tool_name = data.action
            call_id = data.source.id if data.source else data.id
            tool_input: dict[str, Any] = {
                "action": data.action,
                "resources": data.resources,
                **(data.metadata or {}),
            }
            context = self.get_context(
                input_provider=provider,
                tool_call_id=call_id,
                tool_input=tool_input,
                tool_name=tool_name,
            )
            result = await provider.get_tool_confirmation(
                context, tool_description=data.message or ""
            )
            match result:
                case "allow":
                    decision = "once"
                case "skip":
                    decision = "reject"
                case "abort_run" | "abort_chain":
                    decision = "reject"
                    await self._interrupt()
                case _ as unreachable:
                    assert_never(unreachable)
        try:
            await client.reply_permission(data.session_id, data.id, decision)
        except Exception:
            self.log.exception("Failed to reply to OpenCode V2 permission", request_id=data.id)

    async def _handle_form(
        self,
        client: OpenCodeV2Client,
        form: FormInfo1,
        provider: InputProvider | None,
    ) -> None:
        if provider is None:
            self.log.warning("OpenCode V2 created a form, but no input provider is configured")
            with suppress(Exception):
                await client.cancel_form(form.session_id, form.id)
            return
        try:
            params = form_to_elicitation(form)
            elicit_result = await provider.get_elicitation(params, tool_call_id=form.id)
            answer = elicitation_to_form_answer(form, elicit_result)
            if answer is None:
                await client.cancel_form(form.session_id, form.id)
            else:
                await client.reply_form(form.session_id, form.id, answer)
        except Exception:
            self.log.exception("Failed to reply to OpenCode V2 form", form_id=form.id)

    async def _stream_events(  # noqa: PLR0915
        self,
        prompts: list[UserContent],
        *,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        effective_parent_id: str | None,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: InputProvider | None = None,
        deps: TDeps | None = None,
        wait_for_connections: bool | None = None,
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[str]]:
        client = self._require_client()
        final_session_id = session_id or self.session_id
        if final_session_id is None:
            raise ValueError("session_id must be set")
        v2_session_id = await self._ensure_session(client)
        run_id = str(uuid4())
        reply_id = message_id or str(uuid4())
        yield RunStartedEvent(session_id=final_session_id, run_id=run_id, agent_name=self.name)

        reconstructor = MessageReconstructor(
            initial_prompts=prompts,
            model_name=self.model_name,
            provider_name="opencode_v2",
        )
        tracker = V2EventTracker(
            session_id=v2_session_id,
            agent_name=self.name,
            message_id=reply_id,
        )
        provider = input_provider or self._input_provider
        run_context = self.get_context(data=deps, input_provider=provider)
        prompt_body = user_content_to_prompt_body(prompts)

        queue: asyncio.Queue[Any] = asyncio.Queue()
        connected = asyncio.Event()
        sentinel = object()
        stream_error: list[BaseException] = []
        bg_tasks: set[asyncio.Task[Any]] = set()

        async def _read_sse() -> None:
            try:
                async for event in client.events():
                    if not connected.is_set():
                        connected.set()
                    if isinstance(event, V2EventServerConnected):
                        continue
                    await queue.put(event)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001
                stream_error.append(exc)
            finally:
                connected.set()
                await queue.put(sentinel)

        reader = asyncio.create_task(_read_sse())
        try:
            async with self._tool_bridge.set_run_context(run_context, prompt=prompts):
                with suppress(TimeoutError):
                    await asyncio.wait_for(connected.wait(), timeout=_SSE_CONNECT_GRACE_SECONDS)
                if stream_error:
                    raise stream_error[0]

                await client.send_prompt(v2_session_id, prompt_body)

                while not tracker.done:
                    item = await queue.get()
                    if item is sentinel:
                        if stream_error:
                            raise stream_error[0]
                        break
                    if isinstance(item, PermissionAsked) and item.data.session_id == v2_session_id:
                        task = asyncio.create_task(
                            self._handle_permission(client, item.data, provider)
                        )
                        bg_tasks.add(task)
                        task.add_done_callback(bg_tasks.discard)
                        continue
                    if isinstance(item, FormCreated) and item.data.form.session_id == v2_session_id:
                        task = asyncio.create_task(
                            self._handle_form(client, item.data.form, provider)
                        )
                        bg_tasks.add(task)
                        task.add_done_callback(bg_tasks.discard)
                        continue
                    for native_event in tracker.handle(item):
                        reconstructor.observe(native_event)
                        yield native_event
        finally:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
            for task in list(bg_tasks):
                task.cancel()
            if bg_tasks:
                await asyncio.gather(*bg_tasks, return_exceptions=True)

        for event in tracker.flush_open_tools():
            reconstructor.observe(event)
            yield event

        if tracker.last_model_name:
            self._active_model_name = tracker.last_model_name

        reconstructor.flush(finish_reason=tracker.finish_reason)
        cost_info = TokenCost(total_cost=tracker.total_cost) if tracker.total_cost else None
        complete_msg = ChatMessage[str](
            content=reconstructor.text_content,
            role="assistant",
            name=self.name,
            message_id=reply_id,
            session_id=final_session_id,
            parent_id=user_msg.message_id,
            usage=tracker.total_usage,
            cost_info=cost_info,
            model_name=self.model_name,
            messages=reconstructor.model_messages,
            finish_reason=tracker.finish_reason,
        )
        yield StreamCompleteEvent(message=complete_msg)

    async def _interrupt(self) -> None:
        if self._client is None or self._sdk_session_id is None:
            return
        try:
            await self._client.interrupt_session(self._sdk_session_id)
        except Exception:
            self.log.exception("Failed to interrupt OpenCode V2 session")

    async def start_new_session(self) -> None:
        """Start a fresh OpenCode V2 session on the next turn."""
        await super().start_new_session()
        self._sdk_session_id = None

    # --- Models, modes, auth, MCP & usage limits -------------------------------------

    @property
    def model_name(self) -> str | None:
        """Active model name as ``provider/model``."""
        return self._active_model_name or self._model

    async def set_model(self, model: str) -> None:
        """Switch the model on the active session."""
        await self._set_mode(model, "model")

    async def get_available_models(self) -> list[TokoModelInfo] | None:
        """List models reported by ``GET /api/model``."""
        if self._client is None:
            return None
        models = await self._fetch_models_raw()
        return [to_tokonomics_model_info(m) for m in models if m.enabled]

    @property
    def config_options(self) -> Sequence[ConfigOption[Self]]:
        """Permission mode, agent, model variant, and model options."""
        from agentpool.agents.opencode_v2_agent.config_options import CONFIG_OPTIONS

        return CONFIG_OPTIONS

    async def get_auth_status(self) -> AuthStatus:
        """Report provider credentials configured on the OpenCode V2 server."""
        from agentpool.agents.auth import AuthAccount, AuthStatus

        if self._client is None:
            return AuthStatus(state="unknown")
        try:
            credentials = await self._client.list_credentials()
        except Exception:
            self.log.debug("Failed to read OpenCode V2 credentials", exc_info=True)
            return AuthStatus(state="unknown")
        if not credentials:
            return AuthStatus(state="not_required")
        accounts = [
            AuthAccount(
                label=f"{cred.integration_id}: {cred.label}",
                description=cred.value.type,
                active=cred.active,
            )
            for cred in credentials
        ]
        return AuthStatus(state="authenticated", accounts=accounts)

    async def get_mcp_server_info(self) -> dict[str, MCPServerStatus]:
        """Return MCP server statuses reported by ``GET /api/mcp``."""
        if self._client is None:
            return await super().get_mcp_server_info()
        try:
            servers = await self._client.list_mcp_servers()
        except Exception:
            self.log.debug("Failed to read OpenCode V2 MCP servers", exc_info=True)
            return {}
        return {server.name: to_mcp_server_status(server) for server in servers}

    async def get_usage_limits(self) -> UsageLimits | None:
        """Return plan usage limits from ``GET /api/experimental/usage/limits``."""
        if self._client is None:
            return None
        try:
            info = await self._client.usage_limits()
        except Exception:
            self.log.debug("OpenCode V2 usage limits unavailable", exc_info=True)
            return None
        return from_usage_info(info)

    # --- Sessions ---------------------------------------------------------------------

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List sessions from the OpenCode V2 server."""
        if self._client is None:
            return []
        try:
            page = await self._client.list_sessions(
                limit=limit,
                directory=cwd,
                archived="false",
            )
        except Exception:
            self.log.exception("Failed to list OpenCode V2 sessions")
            return []
        return [to_session_data(info, agent_name=self.name) for info in page.data]

    async def load_session(self, session_id: str) -> SessionData | None:
        """Bind to an existing OpenCode V2 session and restore its conversation history."""
        if self._client is None:
            return None
        pool_session = await self.storage.load_session(session_id) if self.storage else None
        v2_id = (
            pool_session.sdk_session_id
            if pool_session is not None and pool_session.sdk_session_id
            else session_id
        )
        try:
            info = await self._client.get_session(v2_id)
            raw_messages = await self._client.list_all_messages(v2_id)
        except Exception:
            self.log.exception(
                "Failed to load OpenCode V2 session",
                session_id=session_id,
                v2_session_id=v2_id,
            )
            return None

        self._sdk_session_id = info.id
        if info.agent:
            self._opencode_agent = info.agent
        if info.model is not None:
            self._active_model_name = format_model_ref(info.model)
            self._variant = info.model.variant
        messages = v2_messages_to_chat_messages(
            raw_messages, session_id=session_id, agent_name=self.name
        )
        self.conversation.chat_messages.clear()
        self.conversation.chat_messages.extend(messages)
        if pool_session is not None:
            return pool_session
        return to_session_data(info, agent_name=self.name)
