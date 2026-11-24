"""add_title_to_conversation.

Revision ID: 2f915b1f62bd
Revises: 0a066f5efb21
Create Date: 2026-01-21 21:26:35.561319

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from agentpool_storage.sql_provider.models import UTCDateTime


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "2f915b1f62bd"
down_revision: str | Sequence[str] | None = "0a066f5efb21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Get existing tables to make this migration idempotent
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # Create project table if not exists
    if "project" not in existing_tables:
        op.create_table(
            "project",
            sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("worktree", sa.Text(), nullable=True),
            sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("vcs", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("config_path", sa.Text(), nullable=True),
            sa.Column("created_at", UTCDateTime(), nullable=True),
            sa.Column("last_active", UTCDateTime(), nullable=True),
            sa.Column("settings_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("project_id"),
        )
        op.create_index(op.f("ix_project_created_at"), "project", ["created_at"], unique=False)
        op.create_index(op.f("ix_project_last_active"), "project", ["last_active"], unique=False)
        op.create_index(op.f("ix_project_name"), "project", ["name"], unique=False)
        op.create_index(op.f("ix_project_worktree"), "project", ["worktree"], unique=True)

    # Create session table if not exists
    if "session" not in existing_tables:
        op.create_table(
            "session",
            sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("agent_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("pool_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("parent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("cwd", sa.Text(), nullable=True),
            sa.Column("created_at", UTCDateTime(), nullable=True),
            sa.Column("last_active", UTCDateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("session_id"),
        )
        op.create_index(op.f("ix_session_agent_name"), "session", ["agent_name"], unique=False)
        op.create_index(op.f("ix_session_created_at"), "session", ["created_at"], unique=False)
        op.create_index(op.f("ix_session_last_active"), "session", ["last_active"], unique=False)
        op.create_index(op.f("ix_session_parent_id"), "session", ["parent_id"], unique=False)
        op.create_index(op.f("ix_session_pool_id"), "session", ["pool_id"], unique=False)
        op.create_index(op.f("ix_session_project_id"), "session", ["project_id"], unique=False)
        op.create_index(op.f("ix_session_title"), "session", ["title"], unique=False)

    # Add columns to conversation table
    conversation_columns = {c["name"] for c in inspector.get_columns("conversation")}
    if "title" not in conversation_columns:
        op.add_column(
            "conversation", sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        op.create_index(op.f("ix_conversation_title"), "conversation", ["title"], unique=False)

    # Add columns to message table
    message_columns = {c["name"] for c in inspector.get_columns("message")}
    if "parent_id" not in message_columns:
        op.add_column(
            "message", sa.Column("parent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        op.create_index(op.f("ix_message_parent_id"), "message", ["parent_id"], unique=False)
    if "provider_name" not in message_columns:
        op.add_column(
            "message", sa.Column("provider_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        op.create_index(
            op.f("ix_message_provider_name"), "message", ["provider_name"], unique=False
        )
    if "provider_response_id" not in message_columns:
        op.add_column(
            "message",
            sa.Column("provider_response_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )
    if "messages" not in message_columns:
        op.add_column(
            "message", sa.Column("messages", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
    if "finish_reason" not in message_columns:
        op.add_column(
            "message", sa.Column("finish_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_message_provider_name"), table_name="message")
    op.drop_index(op.f("ix_message_parent_id"), table_name="message")
    op.drop_column("message", "finish_reason")
    op.drop_column("message", "messages")
    op.drop_column("message", "provider_response_id")
    op.drop_column("message", "provider_name")
    op.drop_column("message", "parent_id")
    op.drop_index(op.f("ix_conversation_title"), table_name="conversation")
    op.drop_column("conversation", "title")
    op.drop_index(op.f("ix_session_title"), table_name="session")
    op.drop_index(op.f("ix_session_project_id"), table_name="session")
    op.drop_index(op.f("ix_session_pool_id"), table_name="session")
    op.drop_index(op.f("ix_session_parent_id"), table_name="session")
    op.drop_index(op.f("ix_session_last_active"), table_name="session")
    op.drop_index(op.f("ix_session_created_at"), table_name="session")
    op.drop_index(op.f("ix_session_agent_name"), table_name="session")
    op.drop_table("session")
    op.drop_index(op.f("ix_project_worktree"), table_name="project")
    op.drop_index(op.f("ix_project_name"), table_name="project")
    op.drop_index(op.f("ix_project_last_active"), table_name="project")
    op.drop_index(op.f("ix_project_created_at"), table_name="project")
    op.drop_table("project")
