"""remove_tool_calls_table.

Revision ID: cd08c98e04c6
Revises: 5ffc5f0266a1
Create Date: 2025-10-28 13:26:51.017129

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "cd08c98e04c6"
down_revision: str | Sequence[str] | None = "5ffc5f0266a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Check if table exists before trying to drop indexes and table
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "toolcall" in inspector.get_table_names():
        # Get existing indexes for the table
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("toolcall")}

        if "ix_toolcall_message_id" in existing_indexes:
            op.drop_index(op.f("ix_toolcall_message_id"), table_name="toolcall")
        if "ix_toolcall_conversation_id" in existing_indexes:
            op.drop_index(op.f("ix_toolcall_conversation_id"), table_name="toolcall")

        op.drop_table("toolcall")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "toolcall",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("tool_call_id", sa.String(), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("result", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_toolcall_conversation_id"), "toolcall", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_toolcall_message_id"), "toolcall", ["message_id"], unique=False)
