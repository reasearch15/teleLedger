"""Make payment events unique per Telegram message.

Revision ID: 20260706_03
Revises: 20260706_02
Create Date: 2026-07-06 00:00:02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260706_03"
down_revision: str | None = "20260706_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the lookup index with a uniqueness constraint."""
    op.drop_index(
        op.f("ix_payment_events_telegram_message_id"),
        table_name="payment_events",
    )
    op.create_unique_constraint(
        "uq_payment_events_telegram_message_id",
        "payment_events",
        ["telegram_message_id"],
    )


def downgrade() -> None:
    """Restore the non-unique Telegram message lookup index."""
    op.drop_constraint(
        "uq_payment_events_telegram_message_id",
        "payment_events",
        type_="unique",
    )
    op.create_index(
        op.f("ix_payment_events_telegram_message_id"),
        "payment_events",
        ["telegram_message_id"],
        unique=False,
    )

