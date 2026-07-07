"""Add indexes for paginated payment-list queries.

Revision ID: 20260706_04
Revises: 20260706_03
Create Date: 2026-07-06 00:00:03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260706_04"
down_revision: str | None = "20260706_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Index payment filters and Telegram source ordering fields."""
    op.create_index(
        "ix_payment_events_status",
        "payment_events",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_payment_events_payment_datetime",
        "payment_events",
        ["payment_datetime"],
        unique=False,
    )
    op.create_index(
        "ix_payment_events_created_at",
        "payment_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_messages_received_at_message_id",
        "telegram_messages",
        ["received_at", "telegram_message_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove payment-list query indexes."""
    op.drop_index(
        "ix_telegram_messages_received_at_message_id",
        table_name="telegram_messages",
    )
    op.drop_index(
        "ix_payment_events_created_at",
        table_name="payment_events",
    )
    op.drop_index(
        "ix_payment_events_payment_datetime",
        table_name="payment_events",
    )
    op.drop_index(
        "ix_payment_events_status",
        table_name="payment_events",
    )
