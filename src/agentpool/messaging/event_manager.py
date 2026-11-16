"""Event manager for handling multiple event sources."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
import inspect
from typing import TYPE_CHECKING, Any, Self

from anyenv.signals import Signal
from evented.event_data import EventData, FunctionResultEventData
from evented_config import EmailConfig, FileWatchConfig, TimeEventConfig, WebhookConfig
from pydantic import SecretStr

from agentpool.log import get_logger
from agentpool.utils.inspection import execute, get_fn_name
from agentpool.utils.tasks import TaskManager
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence
    from datetime import datetime, timedelta
    from types import TracebackType

    from evented.base import EventSource
    from evented.timed_watcher import TimeEventSource
    from evented_config import EventConfig


logger = get_logger(__name__)


type EventCallback = Callable[[EventData], Awaitable[None] | None]


class EventManager:
    """Manages multiple event sources and their lifecycles."""

    event_processed = Signal[EventData]()

    def __init__(
        self,
        configs: list[EventConfig] | None = None,
        event_callbacks: list[EventCallback] | None = None,
        enable_events: bool = True,
    ) -> None:
        """Initialize event manager.

        Args:
            configs: List of event configurations
            event_callbacks: List of event callbacks
            enable_events: Whether to enable event processing
        """
        self.task_manager = TaskManager()
        self.configs = configs or []
        self.enabled = enable_events
        self._sources: dict[str, EventSource] = {}
        self._callbacks = event_callbacks or []
        self._observers = defaultdict[str, list[EventObserver]](list)

    def add_callback(self, callback: EventCallback) -> None:
        """Register an event callback."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: EventCallback) -> None:
        """Remove a previously registered callback."""
        self._callbacks.remove(callback)

    async def emit_event(self, event: EventData) -> None:
        """Emit event to all callbacks and optionally handle via node."""
        if not self.enabled:
            return

        for callback in self._callbacks:
            try:
                result = callback(event)
                if isinstance(result, Awaitable):
                    await result
            except Exception:
                logger.exception("Error in event callback", name=get_fn_name(callback))

        await self.event_processed.emit(event)

    async def add_file_watch(
        self,
        paths: str | Sequence[str],
        *,
        name: str | None = None,
        extensions: list[str] | None = None,
        ignore_paths: list[str] | None = None,
        recursive: bool = True,
        debounce: int = 1600,
    ) -> EventSource:
        """Add file system watch event source.

        Args:
            paths: Paths or patterns to watch
            name: Optional source name (default: generated from paths)
            extensions: File extensions to monitor
            ignore_paths: Paths to ignore
            recursive: Whether to watch subdirectories
            debounce: Minimum time between events (ms)
        """
        path_list = [paths] if isinstance(paths, str) else list(paths)
        config = FileWatchConfig(
            name=name or f"file_watch_{len(self._sources)}",
            paths=path_list,
            extensions=extensions,
            ignore_paths=ignore_paths,
            recursive=recursive,
            debounce=debounce,
        )
        return await self.add_source(config)

    async def add_webhook(
        self,
        path: str,
        *,
        name: str | None = None,
        port: int = 8000,
        secret: str | None = None,
    ) -> EventSource:
        """Add webhook event source.

        Args:
            path: URL path to listen on
            name: Optional source name
            port: Port to listen on
            secret: Optional secret for request validation
        """
        name = name or f"webhook_{len(self._sources)}"
        sec = SecretStr(secret) if secret else None
        config = WebhookConfig(name=name, path=path, port=port, secret=sec)
        return await self.add_source(config)

    async def add_timed_event(
        self,
        schedule: str,
        prompt: str,
        *,
        name: str | None = None,
        timezone: str | None = None,
        skip_missed: bool = False,
    ) -> TimeEventSource:
        """Add time-based event source.

        Args:
            schedule: Cron expression (e.g. "0 9 * * 1-5" for weekdays at 9am)
            prompt: Prompt to send when triggered
            name: Optional source name
            timezone: Optional timezone (system default if None)
            skip_missed: Whether to skip missed executions
        """
        from evented.timed_watcher import TimeEventSource

        config = TimeEventConfig(
            name=name or f"timed_{len(self._sources)}",
            schedule=schedule,
            prompt=prompt,
            timezone=timezone,
            skip_missed=skip_missed,
        )
        source = await self.add_source(config)
        assert isinstance(source, TimeEventSource)
        return source

    async def add_email_watch(
        self,
        host: str,
        username: str,
        password: str,
        *,
        name: str | None = None,
        port: int = 993,
        folder: str = "INBOX",
        ssl: bool = True,
        check_interval: int = 60,
        mark_seen: bool = True,
        filters: dict[str, str] | None = None,
        max_size: int | None = None,
    ) -> EventSource:
        """Add email monitoring event source.

        Args:
            host: IMAP server hostname
            username: Email account username
            password: Account password or app password
            name: Optional source name
            port: Server port (default: 993 for IMAP SSL)
            folder: Mailbox to monitor
            ssl: Whether to use SSL/TLS
            check_interval: Seconds between checks
            mark_seen: Whether to mark processed emails as seen
            filters: Optional email filtering criteria
            max_size: Maximum email size in bytes
        """
        config = EmailConfig(
            name=name or f"email_{len(self._sources)}",
            host=host,
            username=username,
            password=SecretStr(password),
            port=port,
            folder=folder,
            ssl=ssl,
            check_interval=check_interval,
            mark_seen=mark_seen,
            filters=filters or {},
            max_size=max_size,
        )
        return await self.add_source(config)

    async def add_source(self, config: EventConfig) -> EventSource:
        """Add and start a new event source.

        Args:
            config: Event source configuration

        Raises:
            ValueError: If source already exists or is invalid
        """
        logger.debug("Setting up event source", name=config.name, type=config.type)
        from evented.base import EventSource

        if config.name in self._sources:
            raise ValueError(f"Event source already exists: {config.name}")

        try:
            source = EventSource.from_config(config)
            await source.__aenter__()
            self._sources[config.name] = source
            # Start processing events
            name = f"event_processor_{config.name}"
            self.task_manager.create_task(self._process_events(source), name=name)
            logger.debug("Added event source", name=config.name)
        except Exception as e:
            msg = "Failed to add event source"
            logger.exception(msg, name=config.name)
            raise RuntimeError(msg) from e
        else:
            return source

    async def remove_source(self, name: str) -> None:
        """Stop and remove an event source.

        Args:
            name: Name of source to remove
        """
        if source := self._sources.pop(name, None):
            await source.__aexit__(None, None, None)
            logger.debug("Removed event source", name=name)

    async def _process_events(self, source: EventSource) -> None:
        """Process events from a source.

        Args:
            source: Event source to process
        """
        try:
            # Get the async iterator from the coroutine
            async for event in source.events():
                if not self.enabled:
                    logger.debug("Event processing disabled, skipping event")
                    continue
                await self.emit_event(event)

        except asyncio.CancelledError:
            logger.debug("Event processing cancelled")
            raise

        except Exception:
            logger.exception("Error processing events")

    async def __aenter__(self) -> Self:
        """Allow using manager as async context manager."""
        if not self.enabled:
            return self
        for trigger in self.configs:
            await self.add_source(trigger)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up when exiting context."""
        self.enabled = False

        for name in list(self._sources):
            await self.remove_source(name)

    def track[T](
        self,
        event_name: str | None = None,
        **event_metadata: Any,
    ) -> (
        Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]
        | Callable[[Callable[..., T]], Callable[..., T]]
    ):
        """Track function calls as events.

        Args:
            event_name: Optional name for the event (defaults to function name)
            **event_metadata: Additional metadata to include with event

        Example:
            @event_manager.track("user_search")
            async def search_docs(query: str) -> list[Doc]:
                results = await search(query)
                return results  # This result becomes event data
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:

            name = event_name or get_fn_name(func)

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start_time = get_now()
                meta = {"args": args, "kwargs": kwargs, **event_metadata}
                try:
                    result = await func(*args, **kwargs)
                    if self.enabled:
                        meta |= {"status": "success", "duration": get_now() - start_time}
                        event = EventData.create(name, content=result, metadata=meta)
                        await self.emit_event(event)
                except Exception as e:
                    if self.enabled:
                        dur = get_now() - start_time
                        meta |= {"status": "error", "error": str(e), "duration": dur}
                        event = EventData.create(name, content=str(e), metadata=meta)
                        await self.emit_event(event)
                    raise
                else:
                    return result

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start_time = get_now()
                meta = {"args": args, "kwargs": kwargs, **event_metadata}
                try:
                    result = func(*args, **kwargs)
                    if self.enabled:
                        meta |= {"status": "success", "duration": get_now() - start_time}
                        event = EventData.create(name, content=result, metadata=meta)
                        self.task_manager.run_background(self.emit_event(event))
                except Exception as e:
                    if self.enabled:
                        dur = get_now() - start_time
                        meta |= {"status": "error", "error": str(e), "duration": dur}
                        event = EventData.create(name, content=str(e), metadata=meta)
                        self.task_manager.run_background(self.emit_event(event))
                    raise
                else:
                    return result

            return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

        return decorator

    def poll[T](
        self,
        event_type: str,
        interval: timedelta | None = None,
    ) -> (
        Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]
        | Callable[[Callable[..., T]], Callable[..., T]]
    ):
        """Decorator to register an event observer.

        Args:
            event_type: Type of event to observe
            interval: Optional polling interval for periodic checks

        Example:
            @event_manager.observe("file_changed")
            async def handle_file_change(event: FileEventData):
                await process_file(event.path)
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            observer = EventObserver(func, interval=interval)
            self._observers[event_type].append(observer)

            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                result = await execute(func, *args, **kwargs)
                # Convert result to event and emit
                if self.enabled:
                    typ = type(result).__name__
                    meta = {"type": "function_result", "output_type": typ}
                    event = FunctionResultEventData(result=result, source=event_type, metadata=meta)
                    await self.emit_event(event)
                return result

            return wrapper

        return decorator


@dataclass
class EventObserver:
    """Registered event observer."""

    callback: Callable[..., Any]
    interval: timedelta | None = None
    last_run: datetime | None = None

    async def __call__(self, event: EventData) -> None:
        """Handle an event."""
        try:
            await execute(self.callback, event)
        except Exception:
            logger.exception("Error in event observer")


if __name__ == "__main__":
    from evented.event_data import EventData

    async def dummy_callback(event: EventData) -> None:
        if prompt := event.to_prompt():
            print(f"Received: {prompt}")

    event_manager = EventManager(event_callbacks=[dummy_callback])
