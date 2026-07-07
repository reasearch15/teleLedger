"""Create Telegram messages and payment events.

Revision ID: 20260706_01
Revises:
Create Date: 2026-07-06 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260706_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

payment_status = postgresql.ENUM(
    "pending",
    "in_progress",
    "done",
    "ignored",
    "manual_review",
    name="payment_status",
    create_type=False,
)


def upgrade() -> None:
    """Create the initial payment ingestion tables."""
    payment_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "telegram_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_messages")),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_telegram_messages_chat_message",
        ),
    )

    op.create_table(
        "payment_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_tag", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("payment_sender_name", sa.String(length=255), nullable=False),
        sa.Column("payment_datetime", sa.DateTime(timezone=False), nullable=True),
        sa.Column("total_in", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("total_out", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            payment_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("claimed_by", sa.BigInteger(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by", sa.BigInteger(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parser_confidence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "parser_confidence BETWEEN 0 AND 100",
            name=op.f("ck_payment_events_parser_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["telegram_message_id"],
            ["telegram_messages.id"],
            name=op.f("fk_payment_events_telegram_message_id_telegram_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_events")),
    )
    op.create_index(
        op.f("ix_payment_events_telegram_message_id"),
        "payment_events",
        ["telegram_message_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the payment ingestion tables and status type."""
    op.drop_index(
        op.f("ix_payment_events_telegram_message_id"),
        table_name="payment_events",
    )
    op.drop_table("payment_events")
    op.drop_table("telegram_messages")
    payment_status.drop(op.get_bind(), checkfirst=True)

