"""Add Telegram backfill checkpoints.

Revision ID: 20260706_07
Revises: 20260706_06
Create Date: 2026-07-06 00:00:06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260706_07"
down_revision: str | None = "20260706_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Telegram startup backfill checkpoints."""
    op.create_table(
        "telegram_backfill_checkpoints",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("last_scanned_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "telegram_chat_id",
            name=op.f("pk_telegram_backfill_checkpoints"),
        ),
    )


def downgrade() -> None:
    """Remove Telegram startup backfill checkpoints."""
    op.drop_table("telegram_backfill_checkpoints")
