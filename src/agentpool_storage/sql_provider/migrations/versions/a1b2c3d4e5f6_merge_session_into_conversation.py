"""Merge session table into conversation table.

Session lifecycle data (pool_id, project_id, parent_id, version, cwd,
last_active, metadata_json) is now stored directly in the conversation table.
The separate session table is dropped.

Revision ID: a1b2c3d4e5f6
Revises: 0a066f5efb21
Create Date: 2026-02-16 00:00:00.000000

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "2d23eda297fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add session fields to conversation table and migrate data from session table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Step 1: Add new columns to conversation table (if they don't already exist)
    existing_columns = {col["name"] for col in inspector.get_columns("conversation")}

    new_columns = {
        "pool_id": sa.Column(
            "pool_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
        "project_id": sa.Column(
            "project_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
        "parent_id": sa.Column(
            "parent_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
        "version": sa.Column(
            "version",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="1",
        ),
        "cwd": sa.Column("cwd", sa.Text(), nullable=True),
        "last_active": sa.Column("last_active", sa.DateTime(), nullable=True),
        "metadata_json": sa.Column("metadata_json", sa.JSON(), nullable=True),
    }

    for col_name, col_def in new_columns.items():
        if col_name not in existing_columns:
            op.add_column("conversation", col_def)

    # Step 2: Create indexes for new columns
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("conversation")}

    index_defs = {
        "ix_conversation_pool_id": "pool_id",
        "ix_conversation_project_id": "project_id",
        "ix_conversation_parent_id": "parent_id",
        "ix_conversation_last_active": "last_active",
    }

    for idx_name, col_name in index_defs.items():
        if idx_name not in existing_indexes and col_name in {
            *existing_columns,
            *new_columns,
        }:
            op.create_index(idx_name, "conversation", [col_name], unique=False)

    # Step 3: Migrate data from session table to conversation table (if session exists)
    if "session" in inspector.get_table_names():
        # Migrate session data into matching conversation rows
        op.execute(
            sa.text("""
                UPDATE conversation
                SET pool_id = s.pool_id,
                    project_id = s.project_id,
                    parent_id = s.parent_id,
                    version = s.version,
                    cwd = s.cwd,
                    last_active = s.last_active,
                    metadata_json = s.metadata_json
                FROM session s
                WHERE conversation.id = s.session_id
            """)
        )

        # Insert sessions that don't have a corresponding conversation row
        op.execute(
            sa.text("""
                INSERT INTO conversation (id, agent_name, pool_id, project_id, parent_id,
                                         version, cwd, start_time, last_active, metadata_json,
                                         total_tokens, total_cost)
                SELECT s.session_id, s.agent_name, s.pool_id, s.project_id, s.parent_id,
                       s.version, s.cwd, s.created_at, s.last_active, s.metadata_json,
                       0, 0.0
                FROM session s
                WHERE s.session_id NOT IN (SELECT id FROM conversation)
            """)
        )

        # Step 4: Drop the session table
        op.drop_table("session")


def downgrade() -> None:
    """Recreate session table and move session data back."""
    # Recreate the session table
    op.create_table(
        "session",
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), primary_key=True),
        sa.Column("agent_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("pool_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("parent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "version", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="1"
        ),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("cwd", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_active", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_session_agent_name", "session", ["agent_name"], unique=False)
    op.create_index("ix_session_pool_id", "session", ["pool_id"], unique=False)
    op.create_index("ix_session_project_id", "session", ["project_id"], unique=False)
    op.create_index("ix_session_parent_id", "session", ["parent_id"], unique=False)
    op.create_index("ix_session_title", "session", ["title"], unique=False)
    op.create_index("ix_session_created_at", "session", ["created_at"], unique=False)
    op.create_index("ix_session_last_active", "session", ["last_active"], unique=False)

    # Copy data back from conversation to session
    op.execute(
        sa.text("""
            INSERT INTO session (session_id, agent_name, pool_id, project_id, parent_id,
                                 version, title, cwd, created_at, last_active, metadata_json)
            SELECT id, agent_name, pool_id, project_id, parent_id,
                   version, title, cwd, start_time, last_active, metadata_json
            FROM conversation
            WHERE pool_id IS NOT NULL OR project_id IS NOT NULL OR cwd IS NOT NULL
        """)
    )

    # Drop session columns from conversation
    op.drop_index("ix_conversation_last_active", table_name="conversation")
    op.drop_index("ix_conversation_parent_id", table_name="conversation")
    op.drop_index("ix_conversation_project_id", table_name="conversation")
    op.drop_index("ix_conversation_pool_id", table_name="conversation")
    op.drop_column("conversation", "metadata_json")
    op.drop_column("conversation", "last_active")
    op.drop_column("conversation", "cwd")
    op.drop_column("conversation", "version")
    op.drop_column("conversation", "parent_id")
    op.drop_column("conversation", "project_id")
    op.drop_column("conversation", "pool_id")
