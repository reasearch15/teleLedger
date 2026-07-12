"""Store cashout Telegram chat IDs and unique reaction lookup index.

Revision ID: 20260712_19
Revises: 20260711_18
Create Date: 2026-07-12 00:00:19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_19"
down_revision: str | None = "20260711_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add telegram_chat_id and a unique (chat, message) lookup index."""
    op.add_column(
        "cashout_requests",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_cashout_requests_telegram_chat_message",
        "cashout_requests",
        ["telegram_chat_id", "telegram_message_id"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_cashout_requests_telegram_chat_message
        ON cashout_requests (telegram_chat_id, telegram_message_id)
        WHERE telegram_message_id IS NOT NULL
        """
    )
    # Backfill chat IDs for historical sent cashouts from configured group when set.
    # Safe no-op when the setting is absent; runtime fallback still uses the
    # configured cashout group ID for null chat rows.


def downgrade() -> None:
    """Remove telegram_chat_id and reaction lookup indexes."""
    op.execute("DROP INDEX IF EXISTS uq_cashout_requests_telegram_chat_message")
    op.drop_index(
        "ix_cashout_requests_telegram_chat_message",
        table_name="cashout_requests",
    )
    op.drop_column("cashout_requests", "telegram_chat_id")
