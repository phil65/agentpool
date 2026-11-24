"""drop conversation_id from session table.

Revision ID: 0a066f5efb21
Revises: cd08c98e04c6
Create Date: 2026-01-21 20:27:04.403701

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "0a066f5efb21"
down_revision: str | Sequence[str] | None = "cd08c98e04c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Check if the session table exists and has the conversation_id column
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "session" not in inspector.get_table_names():
        # Table doesn't exist yet, nothing to migrate
        return

    columns = {col["name"] for col in inspector.get_columns("session")}
    if "conversation_id" not in columns:
        # Column doesn't exist, nothing to migrate
        return

    # Check if index exists before dropping
    indexes = {idx["name"] for idx in inspector.get_indexes("session")}
    if "ix_session_conversation_id" in indexes:
        op.drop_index("ix_session_conversation_id", table_name="session")

    op.drop_column("session", "conversation_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "session",
        sa.Column(
            "conversation_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_session_conversation_id",
        "session",
        ["conversation_id"],
        unique=False,
    )
