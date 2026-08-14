"""Add cashout bot runtime partial-pending state and audit action.

Revision ID: 20260715_24
Revises: 20260715_23
Create Date: 2026-07-15 00:00:24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_24"
down_revision: str | None = "20260715_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE cashout_audit_action ADD VALUE IF NOT EXISTS "
            "'telegram_bot_completed'"
        )
    else:
        with op.batch_alter_table("cashout_request_audit") as batch_op:
            batch_op.alter_column(
                "action",
                existing_type=sa.Enum(
                    "created",
                    "telegram_sent",
                    "telegram_retry",
                    "telegram_reaction_completed",
                    "completed",
                    "cancelled",
                    "edited_notes",
                    "telegram_bot_completed",
                    name="cashout_audit_action",
                ),
                type_=sa.Enum(
                    "created",
                    "telegram_sent",
                    "telegram_retry",
                    "telegram_reaction_completed",
                    "completed",
                    "cancelled",
                    "edited_notes",
                    "telegram_bot_completed",
                    name="cashout_audit_action",
                ),
            )

    op.create_table(
        "cashout_partial_pending_inputs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "cashout_id",
            sa.BigInteger(),
            sa.ForeignKey("cashout_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "coadmin_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_message_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("cashout_id", name="uq_cashout_partial_pending_cashout"),
    )
    op.create_index(
        "ix_cashout_partial_pending_expires",
        "cashout_partial_pending_inputs",
        ["expires_at"],
    )
    op.create_index(
        "ix_cashout_partial_pending_chat_user",
        "cashout_partial_pending_inputs",
        ["telegram_chat_id", "telegram_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cashout_partial_pending_chat_user",
        table_name="cashout_partial_pending_inputs",
    )
    op.drop_index(
        "ix_cashout_partial_pending_expires",
        table_name="cashout_partial_pending_inputs",
    )
    op.drop_table("cashout_partial_pending_inputs")
