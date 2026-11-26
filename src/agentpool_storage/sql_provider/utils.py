"""Utilities for database storage."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunUsage
from sqlalchemy import Column, and_
from sqlmodel import select

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, TokenCost
from agentpool.storage import deserialize_messages
from agentpool_storage.models import ConversationData
from agentpool_storage.sql_provider.models import Conversation


if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlmodel.sql.expression import SelectOfScalar

    from agentpool_config.session import SessionQuery
    from agentpool_storage.sql_provider.models import Message


# Track migrated databases to skip redundant migration attempts
_migrated_databases: set[str] = set()
logger = get_logger(__name__)


def aggregate_token_usage(messages: Sequence[ChatMessage[str]]) -> RunUsage:
    """Sum up tokens from a sequence of messages."""
    usage = RunUsage()
    for msg in messages:
        usage += msg.usage
    return usage


def to_chat_message(db_message: Message) -> ChatMessage[str]:
    """Convert database message to ChatMessage."""
    cost_info = None
    if db_message.total_tokens is not None:
        cost_info = TokenCost(total_cost=Decimal(db_message.cost or 0.0))
    return ChatMessage[str](
        message_id=db_message.id,
        session_id=db_message.session_id,
        parent_id=db_message.parent_id,
        content=db_message.content,
        role=db_message.role,  # type: ignore
        name=db_message.name,
        model_name=db_message.model,
        cost_info=cost_info,
        usage=RunUsage(
            input_tokens=db_message.input_tokens or 0,
            output_tokens=db_message.output_tokens or 0,
        ),
        response_time=db_message.response_time,
        timestamp=db_message.timestamp,
        provider_name=db_message.provider_name,
        provider_response_id=db_message.provider_response_id,
        messages=deserialize_messages(db_message.messages),
        finish_reason=db_message.finish_reason,  # type: ignore
        metadata=dict(db_message.metadata_json or {}),
    )


def run_alembic_migrations(connection: Any) -> None:
    """Run Alembic migrations using an existing database connection.

    This function is idempotent. It tracks which databases have been migrated
    in the current process to avoid redundant migration attempts.

    Args:
        connection: SQLAlchemy connection (sync)
    """
    from alembic.config import Config
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from sqlmodel import SQLModel

    # Get database URL for tracking
    db_url = str(connection.engine.url)
    # Check if already migrated in this process (fast path)
    if db_url in _migrated_databases:
        logger.debug("Skipping migrations, already completed for this database")
        return

    # Shipped inside the package (the wheel has no repository root)
    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        logger.warning("Migrations directory not found, skipping Alembic migrations")
        _migrated_databases.add(db_url)
        return

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    alembic_cfg.attributes["connection"] = connection
    script = ScriptDirectory.from_config(alembic_cfg)

    def upgrade_fn(rev: str, context: EnvironmentContext) -> list:  # type: ignore[type-arg]
        return script._upgrade_revs("head", rev)

    with EnvironmentContext(
        alembic_cfg,
        script,
        fn=upgrade_fn,
        as_sql=False,
        starting_rev=None,
        destination_rev="head",
    ) as env_context:
        env_context.configure(connection=connection, target_metadata=SQLModel.metadata)
        with env_context.begin_transaction():
            env_context.run_migrations()

    # Mark this database as migrated
    _migrated_databases.add(db_url)
    logger.debug("Migrations completed for database", db_url=db_url)


def parse_model_info(model: str | None) -> tuple[str | None, str | None]:
    """Parse model string into provider and name.

    Args:
        model: Full model string (e.g., "openai:gpt-5", "anthropic:claude-sonnet-4-0")

    Returns:
        Tuple of (provider, name)
    """
    if not model:
        return None, None

    # Try splitting by ':' or '/'
    parts = model.split(":") if ":" in model else model.split("/")

    if len(parts) == 2:  # noqa: PLR2004
        provider, name = parts
        return provider.lower(), name

    # No provider specified, try to infer
    name = parts[0]
    if name.startswith(("gpt-", "text-", "dall-e")):
        return "openai", name
    if name.startswith("claude"):
        return "anthropic", name
    if name.startswith(("llama", "mistral")):
        return "meta", name

    return None, name


def build_message_query(query: SessionQuery) -> SelectOfScalar[Any]:
    """Build SQLModel query from SessionQuery."""
    from agentpool_storage.sql_provider.models import Message

    stmt = select(Message).order_by(Message.timestamp)  # type: ignore

    conditions: list[Any] = []
    if query.name:
        conditions.append(Message.session_id == query.name)
    if query.agents:
        conditions.append(Column("name").in_(query.agents))
    if query.since and (cutoff := query.get_time_cutoff()):
        conditions.append(Message.timestamp >= cutoff)
    if query.until:
        conditions.append(Message.timestamp <= datetime.fromisoformat(query.until))
    if query.contains:
        conditions.append(Message.content.contains(query.contains))  # type: ignore
    if query.roles:
        conditions.append(Message.role.in_(query.roles))  # type: ignore

    if conditions:
        stmt = stmt.where(and_(*conditions))
    if query.limit:
        stmt = stmt.limit(query.limit)

    return stmt


def format_conversation(
    conv: Conversation | ConversationData,
    messages: Sequence[ChatMessage[str]],
    *,
    include_tokens: bool = False,
    compact: bool = False,
) -> ConversationData:
    """Format SQL conversation model to ConversationData.

    With ``compact``, only the first and last message are kept. Token usage is always
    computed over all messages.
    """
    all_msgs = list(messages)
    msgs = [all_msgs[0], all_msgs[-1]] if compact and len(all_msgs) > 1 else all_msgs
    usage = aggregate_token_usage(all_msgs) if include_tokens else None
    if isinstance(conv, Conversation):
        return ConversationData(
            id=conv.id,
            agent=conv.agent_name,
            title=conv.title,
            start_time=conv.start_time.isoformat(),
            messages=msgs,
            token_usage=usage,
        )
    result = ConversationData(**conv)
    result["messages"] = msgs
    if include_tokens and result["token_usage"] is None:
        result["token_usage"] = usage
    return result
