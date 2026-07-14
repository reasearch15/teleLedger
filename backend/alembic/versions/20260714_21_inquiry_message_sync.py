"""Add inquiry message sync metadata columns.

Revision ID: 20260714_21
Revises: 20260714_20
Create Date: 2026-07-14 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_21"
down_revision: str | None = "20260714_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add grouped media, reply, forward, delete, and media hash tracking."""
    op.add_column(
        "inquiry_messages",
        sa.Column("telegram_grouped_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "inquiry_messages",
        sa.Column("reply_to_telegram_message_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "inquiry_messages",
        sa.Column("forward_from_display_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "inquiry_messages",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "inquiry_messages",
        sa.Column("media_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "inquiry_messages",
        sa.Column("media_error", sa.String(length=512), nullable=True),
    )
    op.create_index(
        "ix_inquiry_messages_grouped_id",
        "inquiry_messages",
        ["telegram_chat_id", "telegram_grouped_id"],
    )


def downgrade() -> None:
    """Remove inquiry sync metadata columns."""
    op.drop_index("ix_inquiry_messages_grouped_id", table_name="inquiry_messages")
    op.drop_column("inquiry_messages", "media_error")
    op.drop_column("inquiry_messages", "media_hash")
    op.drop_column("inquiry_messages", "is_deleted")
    op.drop_column("inquiry_messages", "forward_from_display_name")
    op.drop_column("inquiry_messages", "reply_to_telegram_message_id")
    op.drop_column("inquiry_messages", "telegram_grouped_id")
