"""rename_conversation_id_to_session_id.

Revision ID: f61790110e77
Revises: 2f915b1f62bd
Create Date: 2026-01-22 11:39:54.081008

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "f61790110e77"
down_revision: str | Sequence[str] | None = "2f915b1f62bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename conversation_id to session_id in message table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if message table exists and has the column
    if "message" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("message")}
    indexes = {idx["name"] for idx in inspector.get_indexes("message")}

    # Already migrated
    if "session_id" in columns:
        return

    # Column doesn't exist (fresh DB will have session_id from model)
    if "conversation_id" not in columns:
        return

    # Drop old index if it exists
    if "ix_message_conversation_id" in indexes:
        op.drop_index("ix_message_conversation_id", table_name="message")

    # Rename column
    op.alter_column("message", "conversation_id", new_column_name="session_id")

    # Create new index if it doesn't exist
    if "ix_message_session_id" not in indexes:
        op.create_index("ix_message_session_id", "message", ["session_id"])


def downgrade() -> None:
    """Rename session_id back to conversation_id in message table."""
    op.drop_index("ix_message_session_id", table_name="message")
    op.alter_column("message", "session_id", new_column_name="conversation_id")
    op.create_index("ix_message_conversation_id", "message", ["conversation_id"])
