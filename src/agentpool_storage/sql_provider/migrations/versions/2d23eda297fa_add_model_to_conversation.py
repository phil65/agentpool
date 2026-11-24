"""add_model_to_conversation.

Revision ID: 2d23eda297fa
Revises: f61790110e77
Create Date: 2026-01-26 03:52:08.341422

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "2d23eda297fa"
down_revision: str | Sequence[str] | None = "f61790110e77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add model column to conversation table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = {c["name"] for c in inspector.get_columns("conversation")}

    if "model" not in columns:
        op.add_column(
            "conversation",
            sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )
        op.create_index(op.f("ix_conversation_model"), "conversation", ["model"], unique=False)


def downgrade() -> None:
    """Remove model column from conversation table."""
    op.drop_index(op.f("ix_conversation_model"), table_name="conversation")
    op.drop_column("conversation", "model")
