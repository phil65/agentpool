"""Add agent_type and sdk_session_id to conversation.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add agent_type and sdk_session_id columns to conversation table."""
    with op.batch_alter_table("conversation") as batch_op:
        batch_op.add_column(sa.Column("agent_type", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("sdk_session_id", sa.String(), nullable=True))
        batch_op.create_index("ix_conversation_agent_type", ["agent_type"])
        batch_op.create_index("ix_conversation_sdk_session_id", ["sdk_session_id"])


def downgrade() -> None:
    """Remove agent_type and sdk_session_id columns from conversation table."""
    with op.batch_alter_table("conversation") as batch_op:
        batch_op.drop_index("ix_conversation_sdk_session_id")
        batch_op.drop_index("ix_conversation_agent_type")
        batch_op.drop_column("sdk_session_id")
        batch_op.drop_column("agent_type")
