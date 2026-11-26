"""Add metadata_json to message (an answer's turn: outcome and parent).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-24
"""

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the metadata_json column to the message table (if missing)."""
    inspector = sa.inspect(op.get_bind())
    if "message" not in inspector.get_table_names():
        return
    if "metadata_json" in {c["name"] for c in inspector.get_columns("message")}:
        return
    with op.batch_alter_table("message") as batch_op:
        batch_op.add_column(sa.Column("metadata_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove the metadata_json column from the message table (if present)."""
    inspector = sa.inspect(op.get_bind())
    if "message" not in inspector.get_table_names():
        return
    if "metadata_json" not in {c["name"] for c in inspector.get_columns("message")}:
        return
    with op.batch_alter_table("message") as batch_op:
        batch_op.drop_column("metadata_json")
