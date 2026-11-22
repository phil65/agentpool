"""Agent that runs its turns as a thread in a T3 Code environment.

One agentpool session is one T3 thread: the first prompt launches the thread
(``t3_thread_launch``), later ones send to it (``t3_thread_send``). While the run
is in progress, the agent reads the run's items (``t3_thread_read``, ``activity``)
every ``poll_interval`` seconds and reports what changed (``ItemTracker``).

Talks to the environment's MCP endpoint through ``pyt3client`` (see its CLAUDE.md
for the upstream map). T3's MCP endpoint answers an agent's questions but not its
permission requests, so a run that waits for an approval waits until someone
approves it in T3 Code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePath, PurePosixPath, PureWindowsPath
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self
from uuid import uuid4

import anyio

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import NoticeEvent, RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.agents.events.reconstructor import MessageReconstructor
from agentpool.agents.exceptions import AgentNotInitializedError
from agentpool.agents.t3_agent.items import FINAL_STATUSES, ItemTracker
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from pydantic_ai import UserContent
    from pyt3client import T3Client
    from pyt3client.models.tools import (
        Item,
        ModelSelection,
        OrchestratorCapabilitiesOutput,
        ThreadReadInput,
        ThreadReadOutput,
    )
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo

    from agentpool.agents.auth import AuthStatus
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ConfigOption
    from agentpool.common_types import AnyEventHandlerType, StrPath
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import MessageHistory
    from agentpool.models.t3_agents import T3AgentConfig, T3InteractionMode, T3RuntimeMode
    from agentpool.sessions import SessionData
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

RUN_FINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted", "rolled_back"})
"""Run statuses that end a turn (``t3_thread_wait``'s terminal statuses)."""

READ_PAGE_SIZE = 100
"""Items per ``t3_thread_read`` call (T3's maximum)."""

CANCEL_GRACE_SECONDS = 15.0
"""How long a cancelled turn follows its run until the interrupt ends it."""

MAX_ITEM_CHARS = 20_000
"""Text read per item; T3 cuts each read at about 30,000 characters in all."""


def prompt_text(prompts: Sequence[UserContent]) -> str:
    """The prompt as text: T3 threads take text messages.

    Raises:
        ValueError: The prompt holds content other than text.
    """
    texts: list[str] = []
    for prompt in prompts:
        if not isinstance(prompt, str):
            msg = f"T3 agents take text prompts, not {type(prompt).__name__}"
            raise ValueError(msg)  # noqa: TRY004
        texts.append(prompt)
    return "\n\n".join(texts)


def server_path(text: str) -> PurePath | None:
    r"""``text`` as an absolute path on the T3 host, or None if it is no absolute path.

    Paths keep the flavor they are written in, whatever this machine runs: ``/srv/app``
    is POSIX, ``C:\src\app`` a Windows path (compared case-insensitively). ``~`` is not
    expanded: the T3 host's home folder is unknown here.
    """
    if text.startswith("/"):
        return PurePosixPath(text)
    windows = PureWindowsPath(text)
    return windows if windows.is_absolute() else None


class T3Agent[TDeps = None](BaseAgent[TDeps, str]):
    """Runs its turns as a thread in a T3 Code environment."""

    AGENT_TYPE: ClassVar = "t3"
    TITLES_OWN_SESSIONS: ClassVar = True  # T3 titles its threads

    def __init__(
        self,
        *,
        project: str,
        login: str | None = None,
        url: str | None = None,
        token: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        options: dict[str, str | bool] | None = None,
        runtime_mode: T3RuntimeMode = "auto",
        interaction_mode: T3InteractionMode = "default",
        worktree: str | None = None,
        poll_interval: float = 0.5,
        client: T3Client | None = None,
        deps_type: type[TDeps] | None = None,
        name: str = "t3",
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
        """Create a T3 agent; it connects on context entry.

        Args:
            project: Project id, or the project's folder on the T3 host
            login: Alias of a sign-in saved by ``pyt3 login``
            url: Environment origin (with ``token``)
            token: MCP bearer token for ``url``
            provider: Provider instance; the project's default when omitted
            model: Model of the provider
            options: Model options (e.g. ``{"effort": "high"}``)
            runtime_mode: Permission mode of new threads
            interaction_mode: Interaction mode of new threads
            worktree: Base ref for a new worktree per new thread
            poll_interval: Seconds between reads of a running turn
            client: Client to use instead of connecting; its owner enters and closes it
            deps_type: Type of dependencies
            name: Agent name
            description: Agent description
            display_name: Human-readable name
            mcp_servers: MCP servers (unused: tools come from the T3 agent)
            env: Execution environment
            agent_pool: Pool of the agent
            enable_logging: Log to storage
            event_configs: Event trigger configurations
            input_provider: Provider for user input
            event_handlers: Event handlers
            commands: Slash commands
            hooks: Agent hooks
        """
        super().__init__(
            name=name,
            description=description,
            display_name=display_name,
            mcp_servers=mcp_servers,
            deps_type=deps_type,
            env=env,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            event_handlers=event_handlers,
            input_provider=input_provider,
            commands=commands,
            hooks=hooks,
        )
        self._project = project
        self._login = login
        self._url = url
        self._token = token
        self._provider = provider
        self._model = model
        self._options = dict(options or {})
        self._runtime_mode: T3RuntimeMode = runtime_mode
        self._interaction_mode: T3InteractionMode = interaction_mode
        self._worktree = worktree
        self._poll_interval = poll_interval
        self._client = client
        self._owns_client = client is None
        self._project_id: str | None = None
        self._sdk_session_id: str | None = None
        """The T3 thread of the current session."""
        self._run_id: str | None = None
        """The run of the turn in progress."""
        self._last_position = -1
        """Highest item position of the thread read so far (reads start after it)."""

    @classmethod
    def from_config(
        cls,
        config: T3AgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create a T3 agent from its configuration."""
        handlers: list[AnyEventHandlerType] = [
            *config.get_event_handlers(),
            *(event_handlers or []),
        ]
        return cls(
            project=config.project,
            login=config.login,
            url=config.url,
            token=config.get_token(),
            provider=config.provider,
            model=config.model,
            options=config.options,
            runtime_mode=config.runtime_mode,
            interaction_mode=config.interaction_mode,
            worktree=config.worktree,
            poll_interval=config.poll_interval,
            name=config.name or "t3",
            deps_type=deps_type,
            description=config.description,
            display_name=config.display_name,
            env=config.get_execution_environment(),
            input_provider=input_provider,
            event_handlers=handlers or None,
            agent_pool=agent_pool,
            hooks=config.hooks.get_agent_hooks(),
        )

    # --- Connection -------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        if self._owns_client:
            self._client = self._connect()
            await self._client.__aenter__()
        if self._client is None:
            raise AgentNotInitializedError
        self._project_id = await self._resolve_project(self._client)
        self.log.info("Connected to T3 Code", project_id=self._project_id)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
        await super().__aexit__(exc_type, exc_val, exc_tb)

    def _connect(self) -> T3Client:
        from pyt3client import T3Client, credentials

        if self._url is not None and self._token is not None:
            return T3Client(self._url, self._token, client_name=f"agentpool:{self.name}")
        try:
            _, saved = credentials.select(credentials.load(), self._login)
        except credentials.StoreError as exc:
            msg = f"T3 sign-in {self._login!r}: {exc} (sign in with `pyt3 login`)"
            raise RuntimeError(msg) from exc
        if credentials.is_expired(saved):
            msg = f"T3 sign-in {self._login!r} expired; sign in again with `pyt3 login`"
            raise RuntimeError(msg)
        return T3Client.from_credentials(saved, client_name=f"agentpool:{self.name}")

    async def _resolve_project(self, client: T3Client) -> str:
        """The project id: ``project`` as given, or the project in that folder on the T3 host."""
        from pyt3client.models.tools import PROJECT_LIST, ProjectListInput

        if (folder := server_path(self._project)) is None:
            return self._project
        cursor: int | None = None
        while True:
            params = ProjectListInput(limit=100)
            if cursor is not None:
                params.cursor = cursor
            page = await client.call(PROJECT_LIST, params)
            for project in page.projects:
                if server_path(project.workspace_root) == folder:
                    return project.id
            if page.next_cursor is None:
                break
            cursor = page.next_cursor.root
        msg = f"No T3 project in {self._project!r} (add it in T3 Code first)"
        raise RuntimeError(msg)

    async def get_auth_status(self) -> AuthStatus:
        """Signed in when connected; otherwise whether the saved sign-in is usable."""
        from agentpool.agents.auth import AuthAccount, AuthStatus

        if self._client is not None and self._project_id is not None:
            label = self._login or self._url or "T3 Code"
            return AuthStatus(
                state="authenticated",
                accounts=[AuthAccount(label=label, description="T3 Code environment", active=True)],
            )
        if self._url is not None and self._token is not None:
            return AuthStatus(state="unknown")
        from pyt3client import credentials

        try:
            _, saved = credentials.select(credentials.load(), self._login)
        except credentials.StoreError:
            return AuthStatus(state="unauthenticated")
        if credentials.is_expired(saved):
            return AuthStatus(state="unauthenticated")
        return AuthStatus(
            state="authenticated",
            accounts=[AuthAccount(label=self._login or saved.base_url, description=saved.base_url)],
        )

    def _require_client(self) -> T3Client:
        if self._client is None or self._project_id is None:
            raise AgentNotInitializedError
        return self._client

    # --- Turns ------------------------------------------------------------------------

    def _model_selection(self) -> ModelSelection | None:
        from pyt3client.models.tools import ModelSelection, ProviderOptionSelection

        if self._provider is None or self._model is None:
            return None  # The project's default
        selection = ModelSelection(instance_id=self._provider, model=self._model)
        if self._options:  # Left out otherwise: arguments are sent as set
            selection.options = [
                ProviderOptionSelection(id=key, value=value) for key, value in self._options.items()
            ]
        return selection

    async def _start_run(self, client: T3Client, text: str) -> str:
        """Launch the session's thread, or send to it; returns the run id."""
        from pyt3client.models.tools import (
            THREAD_LAUNCH,
            THREAD_SEND,
            ThreadLaunchInput,
            ThreadSendInput,
            WorkspaceStrategy,
        )

        if self._sdk_session_id is not None:
            sent = await client.call(
                THREAD_SEND, ThreadSendInput(thread_id=self._sdk_session_id, message=text)
            )
            return sent.run_id
        launch = ThreadLaunchInput(
            project_id=self._project_id,
            title=text.strip().split("\n", 1)[0][:80] or "agentpool",
            message=text,
            runtime_mode=self._runtime_mode,
            interaction_mode=self._interaction_mode,
        )
        if (selection := self._model_selection()) is not None:
            launch.model_selection = selection
        if self._worktree is not None:
            launch.workspace_strategy = WorkspaceStrategy(type="worktree", base_ref=self._worktree)
        launched = await client.call(THREAD_LAUNCH, launch)
        self._sdk_session_id = launched.thread_id
        self._last_position = -1
        if self.storage is not None and self.session_id is not None:
            # Loading this session later resumes the thread (see load_session)
            await self.storage.update_sdk_session_id(self.session_id, launched.thread_id)
        if launched.run_id is None:
            msg = "T3 launched the thread without a run"
            raise RuntimeError(msg)
        return launched.run_id

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
        assert self.session_id is not None  # Initialized by BaseAgent.run_stream()
        reply_id = message_id or str(uuid4())
        reconstructor = MessageReconstructor(initial_prompts=prompts, model_name=self.model_name)
        read_after = self._last_position
        run_id = await self._start_run(client, prompt_text(prompts))
        thread_id = self._sdk_session_id
        assert thread_id is not None
        self._run_id = run_id
        yield RunStartedEvent(session_id=self.session_id, run_id=run_id, agent_name=self.name)
        tracker = ItemTracker(agent_name=self.name, message_id=reply_id)
        provider = input_provider or self._input_provider
        answered: set[str] = set()
        status = "queued"
        told_approval = False
        cancelled_at: float | None = None
        try:
            while True:
                read = await self._read_items(client, thread_id, read_after)
                items = [item for item in read.items if item.run_id == run_id]
                for event in tracker.update(items):
                    reconstructor.observe(event)
                    yield event
                if read.items:
                    newest = max(item.position for item in read.items)
                    self._last_position = max(self._last_position, newest)
                    # Later reads start before the first item that may still change
                    open_items = [i.position for i in items if i.status not in FINAL_STATUSES]
                    read_after = min(open_items) - 1 if open_items else self._last_position
                run = next((r for r in read.recent_runs if r.run_id == run_id), None)
                status = run.status if run is not None else status
                if status in RUN_FINAL_STATUSES and not tracker.pending:
                    break
                if read.thread.pending_request_count > 0:
                    asked = await self._answer_questions(client, thread_id, provider, answered)
                    if not asked and not told_approval:
                        told_approval = True
                        yield NoticeEvent(
                            title="The T3 thread waits for approval in T3 Code",
                            severity="warning",
                            description="Approvals cannot be given through this agent;"
                            " use a runtime mode that needs none to avoid this.",
                        )
                else:
                    told_approval = False
                if self._cancelled:
                    cancelled_at = cancelled_at or time.monotonic()
                    if time.monotonic() - cancelled_at > CANCEL_GRACE_SECONDS:
                        break  # The interrupt did not end the run: stop following it
                await anyio.sleep(self._poll_interval)
        finally:
            self._run_id = None
        failed = status == "failed"
        if failed:
            yield RunErrorEvent(message="The T3 run failed", run_id=run_id, agent_name=self.name)
        reconstructor.flush(finish_reason="error" if failed else "stop")
        message = ChatMessage[str](
            content=tracker.reply,
            role="assistant",
            name=self.name,
            message_id=reply_id,
            session_id=self.session_id,
            parent_id=user_msg.message_id,
            model_name=self.model_name,
            messages=reconstructor.model_messages,
            finish_reason="error" if failed else "stop",
        )
        yield StreamCompleteEvent(message=message)

    async def _read_items(
        self,
        client: T3Client,
        thread_id: str,
        after: int,
        view: Literal["activity", "messages"] = "activity",
    ) -> ThreadReadOutput:
        """The thread's items after position ``after``, oldest first, all pages in one result.

        ``activity`` has every item, ``messages`` only the user's and the agent's messages.
        ``nextPosition`` is the position of a page's last item: the next page starts after it.
        """
        from pyt3client.models.tools import THREAD_READ

        read = await client.call(THREAD_READ, self._read_input(thread_id, after, view))
        while read.has_more and read.next_position is not None:
            page = await client.call(
                THREAD_READ, self._read_input(thread_id, read.next_position.root, view)
            )
            read = page.model_copy(update={"items": [*read.items, *page.items]})
        return read

    @staticmethod
    def _read_input(
        thread_id: str, after: int, view: Literal["activity", "messages"]
    ) -> ThreadReadInput:
        from pyt3client.models.tools import ThreadReadInput

        return ThreadReadInput(
            thread_id=thread_id,
            view=view,
            after_position=after,
            limit=READ_PAGE_SIZE,
            max_chars_per_item=MAX_ITEM_CHARS,
        )

    async def _answer_questions(
        self,
        client: T3Client,
        thread_id: str,
        provider: InputProvider | None,
        answered: set[str],
    ) -> bool:
        """Ask the thread's open questions through ``provider`` and send the answers.

        Returns:
            Whether the thread has open questions (if not, it waits for an approval).
        """
        from pyt3client.models.tools import PENDING_REQUEST, PendingRequestInput

        from agentpool.agents.t3_agent.questions import (
            PendingQuestions,
            PendingRequestIds,
            to_answer,
            to_elicitation,
        )

        listed = await client.call(
            PENDING_REQUEST, PendingRequestInput(action="list", thread_id=thread_id)
        )
        request_ids = PendingRequestIds.model_validate(listed.root).request_ids
        for request_id in request_ids:
            if request_id in answered:
                continue  # Answered; T3 has not closed it yet
            answered.add(request_id)
            if provider is None:
                self.log.warning("T3 asks a question, but there is no input provider")
                continue
            read = await client.call(
                PENDING_REQUEST,
                PendingRequestInput(action="read", thread_id=thread_id, request_id=request_id),
            )
            pending = PendingQuestions.model_validate(read.root)
            answers: dict[str, Any] = {}
            for question in pending.questions:
                result = await provider.get_elicitation(
                    to_elicitation(question), tool_call_id=request_id
                )
                if (answer := to_answer(question, result)) is not None:
                    answers[question.id] = answer
            await client.call(
                PENDING_REQUEST,
                PendingRequestInput(
                    action="respond", thread_id=thread_id, request_id=request_id, answers=answers
                ),
            )
        return bool(request_ids)

    async def _interrupt(self) -> None:
        from pyt3client.models.tools import THREAD_INTERRUPT, ThreadInterruptInput

        if self._client is None or self._sdk_session_id is None or self._run_id is None:
            return
        try:
            await self._client.call(
                THREAD_INTERRUPT,
                ThreadInterruptInput(thread_id=self._sdk_session_id, run_id=self._run_id),
            )
        except Exception:
            self.log.exception("Failed to interrupt the T3 run")

    async def start_new_session(self) -> None:
        """The next prompt launches a new thread."""
        await super().start_new_session()
        self._sdk_session_id = None
        self._last_position = -1

    # --- Models and modes -------------------------------------------------------------

    @property
    def model_name(self) -> str | None:
        """``provider/model``, or None for the project's default."""
        if self._provider is None or self._model is None:
            return None
        return f"{self._provider}/{self._model}"

    async def set_model(self, model: str) -> None:
        """Use ``provider/model`` for new threads."""
        await self._set_mode(model, "model")

    async def _capabilities(self) -> OrchestratorCapabilitiesOutput | None:
        from pyt3client.models.tools import ORCHESTRATOR_CAPABILITIES

        if self._client is None:
            return None
        try:
            return await self._client.call(ORCHESTRATOR_CAPABILITIES)
        except Exception:
            self.log.exception("Failed to read T3's providers")
            return None

    async def get_available_models(self) -> list[ModelInfo] | None:
        """The models of T3's providers that can run work, as ``provider/model``."""
        from tokonomics.model_discovery.model_info import ModelInfo

        if (capabilities := await self._capabilities()) is None:
            return None
        return [
            ModelInfo(
                id=f"{provider.provider_instance_id}/{model.id}",
                name=f"{provider.display_name or provider.provider_instance_id}: "
                f"{model.label or model.id}",
                provider=provider.provider_instance_id,
                input_modalities={"text"},
                output_modalities={"text"},
            )
            for provider in capabilities.providers
            if provider.can_run_child_task
            for model in provider.models
        ]

    @property
    def config_options(self) -> Sequence[ConfigOption[Self]]:
        """Runtime mode, interaction mode and model of new threads."""
        from agentpool.agents.t3_agent.config_options import CONFIG_OPTIONS

        return CONFIG_OPTIONS

    # --- Sessions ---------------------------------------------------------------------

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """The project's threads, newest first."""
        from pyt3client.models.tools import THREAD_LIST, ThreadListInput

        from agentpool.sessions import SessionData

        if self._client is None or self._project_id is None:
            return []
        listed = await self._client.call(
            THREAD_LIST, ThreadListInput(project_id=self._project_id, limit=limit or 50)
        )
        return [
            SessionData(
                session_id=thread.thread_id,
                agent_name=self.name,
                cwd=cwd,
                sdk_session_id=thread.thread_id,
                created_at=_timestamp(thread.created_at),
                last_active=_timestamp(thread.updated_at),
                metadata={"title": thread.title},
            )
            for thread in listed.threads
        ]

    async def load_session(self, session_id: str) -> SessionData | None:
        """Continue a session's thread.

        ``session_id`` is a pool session (its thread is its ``sdk_session_id``) or a
        thread listed by ``list_sessions``. The conversation is read from T3.
        """
        from agentpool.sessions import SessionData

        if self._client is None:
            return None
        pool_session = await self.storage.load_session(session_id) if self.storage else None
        thread_id = (
            pool_session.sdk_session_id
            if pool_session is not None and pool_session.sdk_session_id
            else session_id
        )
        read = await self._read_items(self._client, thread_id, -1, view="messages")
        self._sdk_session_id = thread_id
        self._last_position = max((item.position for item in read.items), default=-1)
        messages = [
            _history_message(item, session_id, self.name)
            for item in read.items
            if item.type in ("user_message", "assistant_message")
        ]
        self.conversation.chat_messages.clear()
        self.conversation.chat_messages.extend(messages)
        if pool_session is not None:
            return pool_session
        return SessionData(
            session_id=session_id,
            agent_name=self.name,
            sdk_session_id=thread_id,
            metadata={"title": read.thread.title},
        )


def _history_message(item: Item, session_id: str, agent_name: str) -> ChatMessage[str]:
    """A user or assistant message of a thread as a chat message.

    It carries its model message: servers replay those to clients that load the session.
    """
    from pydantic_ai import ModelRequest, ModelResponse, TextPart, UserPromptPart

    text = item.text or ""
    if item.type == "user_message":
        request = ModelRequest(parts=[UserPromptPart(content=text)])
        return ChatMessage[str](
            content=text, role="user", session_id=session_id, messages=[request]
        )
    response = ModelResponse(parts=[TextPart(content=text)])
    return ChatMessage[str](
        content=text,
        role="assistant",
        name=agent_name,
        session_id=session_id,
        messages=[response],
    )


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
