"""Reading a T3 Code environment's threads over its MCP endpoint (``pyt3client``).

Shared by the T3 agent (its sessions are threads) and the T3 storage provider (threads as
conversation history): signing in, finding a project, listing threads and reading their
messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Literal

from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from pyt3client import T3Client
    from pyt3client.models.tools import Item, Thread, Thread1, ThreadReadInput, ThreadReadOutput


READ_PAGE_SIZE = 100
"""Items per ``t3_thread_read`` call (T3's maximum)."""

LIST_PAGE_SIZE = 100
"""Threads per ``t3_thread_list`` call."""

MAX_ITEM_CHARS = 20_000
"""Text read per item; T3 cuts each read at about 30,000 characters in all."""

MESSAGE_ITEM_TYPES = frozenset({"user_message", "assistant_message"})
"""Item types that are the conversation (the ``messages`` view's)."""


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


def connect(*, url: str | None, token: str | None, login: str | None, client_name: str) -> T3Client:
    """A client of the environment: ``url`` with ``token``, else the saved sign-in ``login``.

    Raises:
        RuntimeError: Without a usable saved sign-in (missing or expired)
    """
    from pyt3client import T3Client, credentials

    if url is not None and token is not None:
        return T3Client(url, token, client_name=client_name)
    try:
        _, saved = credentials.select(credentials.load(), login)
    except credentials.StoreError as exc:
        msg = f"T3 sign-in {login!r}: {exc} (sign in with `pyt3 login`)"
        raise RuntimeError(msg) from exc
    if credentials.is_expired(saved):
        msg = f"T3 sign-in {login!r} expired; sign in again with `pyt3 login`"
        raise RuntimeError(msg)
    return T3Client.from_credentials(saved, client_name=client_name)


async def resolve_project(client: T3Client, project: str) -> str:
    """The project id: ``project`` as given, or the project in that folder on the T3 host.

    Raises:
        RuntimeError: If no project has that folder
    """
    from pyt3client.models.tools import PROJECT_LIST, ProjectListInput

    if (folder := server_path(project)) is None:
        return project
    cursor: int | None = None
    while True:
        params = ProjectListInput(limit=100)
        if cursor is not None:
            params.cursor = cursor
        page = await client.call(PROJECT_LIST, params)
        for listed in page.projects:
            if server_path(listed.workspace_root) == folder:
                return listed.id
        if page.next_cursor is None:
            break
        cursor = page.next_cursor.root
    msg = f"No T3 project in {project!r} (add it in T3 Code first)"
    raise RuntimeError(msg)


async def project_folders(client: T3Client) -> dict[str, str]:
    """Each project's folder on the T3 host, by project id."""
    from pyt3client.models.tools import PROJECT_LIST, ProjectListInput

    folders: dict[str, str] = {}
    cursor: int | None = None
    while True:
        params = ProjectListInput(limit=100)
        if cursor is not None:
            params.cursor = cursor
        page = await client.call(PROJECT_LIST, params)
        folders.update({project.id: project.workspace_root for project in page.projects})
        if page.next_cursor is None:
            return folders
        cursor = page.next_cursor.root


async def read_thread(client: T3Client, thread_id: str) -> Thread1:
    """A thread's details (title, project, times), without its items."""
    from pyt3client.models.tools import THREAD_READ, ThreadReadInput

    params = ThreadReadInput(thread_id=thread_id, view="messages", after_position=-1, limit=1)
    return (await client.call(THREAD_READ, params)).thread


async def list_threads(
    client: T3Client, *, project_id: str | None, limit: int | None = None
) -> list[Thread]:
    """The threads of ``project_id`` (None: of every project), newest first, all pages."""
    from pyt3client.models.tools import THREAD_LIST, ThreadListInput

    threads: list[Thread] = []
    cursor: int | None = None
    while limit is None or len(threads) < limit:
        page_size = LIST_PAGE_SIZE if limit is None else min(LIST_PAGE_SIZE, limit - len(threads))
        params = ThreadListInput(project_id=project_id, limit=page_size)
        if project_id is None:
            params.all_projects = True
        if cursor is not None:
            params.cursor = cursor
        page = await client.call(THREAD_LIST, params)
        threads.extend(page.threads)
        if page.next_cursor is None or not page.threads:
            break
        cursor = page.next_cursor.root
    return threads


def read_input(
    thread_id: str, after: int, view: Literal["activity", "messages"]
) -> ThreadReadInput:
    """One page of ``thread_id``'s items after position ``after``."""
    from pyt3client.models.tools import ThreadReadInput

    return ThreadReadInput(
        thread_id=thread_id,
        view=view,
        after_position=after,
        limit=READ_PAGE_SIZE,
        max_chars_per_item=MAX_ITEM_CHARS,
    )


async def read_items(
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

    read = await client.call(THREAD_READ, read_input(thread_id, after, view))
    while read.has_more and read.next_position is not None:
        page = await client.call(THREAD_READ, read_input(thread_id, read.next_position.root, view))
        read = page.model_copy(update={"items": [*read.items, *page.items]})
    return read


async def read_messages(
    client: T3Client, thread_id: str, *, session_id: str, agent_name: str
) -> list[ChatMessage[str]]:
    """The conversation of a thread: its user and assistant messages, oldest first."""
    read = await read_items(client, thread_id, -1, view="messages")
    return [
        history_message(item, session_id, agent_name)
        for item in read.items
        if item.type in MESSAGE_ITEM_TYPES
    ]


def history_message(item: Item, session_id: str, agent_name: str) -> ChatMessage[str]:
    """A user or assistant message of a thread as a chat message.

    It carries its model message: servers replay those to clients that load the session.
    """
    from pydantic_ai import ModelRequest, ModelResponse, TextPart, UserPromptPart

    text = item.text or ""
    when = timestamp(item.updated_at)
    if item.type == "user_message":
        request = ModelRequest(parts=[UserPromptPart(content=text, timestamp=when)])
        return ChatMessage[str](
            content=text, role="user", session_id=session_id, messages=[request], timestamp=when
        )
    response = ModelResponse(parts=[TextPart(content=text)], timestamp=when)
    return ChatMessage[str](
        content=text,
        role="assistant",
        name=agent_name,
        session_id=session_id,
        messages=[response],
        timestamp=when,
    )


def timestamp(value: str) -> datetime:
    """An ISO timestamp of T3 (UTC when it names no zone)."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
