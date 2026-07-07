"""Add durable cashout requests, Telegram outbox state, and audit history.

Revision ID: 20260706_06
Revises: 20260706_05
Create Date: 2026-07-06 00:00:05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260706_06"
down_revision: str | None = "20260706_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the cashout workflow and durable Telegram delivery outbox."""
    op.execute(
        """
        CREATE TYPE cashout_status AS ENUM (
            'pending', 'sent', 'completed', 'cancelled', 'failed_to_send'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE cashout_telegram_status AS ENUM (
            'pending', 'sent', 'failed_to_send'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE cashout_audit_action AS ENUM (
            'created', 'telegram_sent', 'telegram_retry',
            'completed', 'cancelled', 'edited_notes'
        )
        """
    )
    cashout_status = postgresql.ENUM(
        "pending",
        "sent",
        "completed",
        "cancelled",
        "failed_to_send",
        name="cashout_status",
        create_type=False,
    )
    telegram_status = postgresql.ENUM(
        "pending",
        "sent",
        "failed_to_send",
        name="cashout_telegram_status",
        create_type=False,
    )
    audit_action = postgresql.ENUM(
        "created",
        "telegram_sent",
        "telegram_retry",
        "completed",
        "cancelled",
        "edited_notes",
        name="cashout_audit_action",
        create_type=False,
    )

    op.create_table(
        "cashout_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("request_number", sa.String(length=24), nullable=True),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("player_tag", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            cashout_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "telegram_status",
            telegram_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_random_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "telegram_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "telegram_next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("telegram_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_last_error", sa.Text(), nullable=True),
        sa.Column("created_by_staff_id", sa.BigInteger(), nullable=False),
        sa.Column("completed_by_staff_id", sa.BigInteger(), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_cashout_requests_amount_positive"),
        sa.CheckConstraint(
            "char_length(btrim(player_tag)) > 0",
            name="ck_cashout_requests_player_tag_required",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_staff_id"],
            ["users.id"],
            name="fk_cashout_requests_completed_by_staff_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_staff_id"],
            ["users.id"],
            name="fk_cashout_requests_created_by_staff_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cashout_requests"),
        sa.UniqueConstraint(
            "created_by_staff_id",
            "idempotency_key",
            name="uq_cashout_requests_staff_idempotency",
        ),
        sa.UniqueConstraint(
            "request_number",
            name="uq_cashout_requests_request_number",
        ),
        sa.UniqueConstraint(
            "telegram_random_id",
            name="uq_cashout_requests_telegram_random_id",
        ),
    )
    op.create_index(
        "ix_cashout_requests_created",
        "cashout_requests",
        ["created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_cashout_requests_delivery",
        "cashout_requests",
        ["telegram_status", "telegram_next_attempt_at"],
        unique=False,
    )

    op.create_table(
        "cashout_request_audit",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("cashout_request_id", sa.BigInteger(), nullable=False),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_cashout_request_audit_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cashout_request_id"],
            ["cashout_requests.id"],
            name="fk_cashout_request_audit_cashout_request_id_cashout_requests",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cashout_request_audit"),
    )
    op.create_index(
        "ix_cashout_request_audit_request_created",
        "cashout_request_audit",
        ["cashout_request_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove cashout workflow tables and enum types."""
    op.drop_index(
        "ix_cashout_request_audit_request_created",
        table_name="cashout_request_audit",
    )
    op.drop_table("cashout_request_audit")
    op.drop_index("ix_cashout_requests_delivery", table_name="cashout_requests")
    op.drop_index("ix_cashout_requests_created", table_name="cashout_requests")
    op.drop_table("cashout_requests")
    op.execute("DROP TYPE cashout_audit_action")
    op.execute("DROP TYPE cashout_telegram_status")
    op.execute("DROP TYPE cashout_status")
