"""Add cashout paid amounts, coadmin settings, Venmo foundation.

Revision ID: 20260715_23
Revises: 20260715_22
Create Date: 2026-07-15 00:00:23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260715_23"
down_revision: str | None = "20260715_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Phase 1 data foundation without retiring existing workflows."""
    cashout_completion_type = postgresql.ENUM(
        "full",
        "partial",
        name="cashout_completion_type",
        create_type=False,
    )
    notification_type = postgresql.ENUM(
        "venmo_confirmation_confirmed",
        name="persistent_notification_type",
        create_type=False,
    )
    venmo_status = postgresql.ENUM(
        "pending",
        "confirmed",
        "not_received",
        "cancelled",
        name="venmo_confirmation_status",
        create_type=False,
    )
    venmo_attempt_status = postgresql.ENUM(
        "pending",
        "posted",
        "confirmed",
        "not_received",
        "failed_to_send",
        name="venmo_confirmation_attempt_status",
        create_type=False,
    )
    venmo_inquiry_status = postgresql.ENUM(
        "open",
        "dismissed",
        "resent",
        name="venmo_confirmation_inquiry_status",
        create_type=False,
    )
    venmo_event_type = postgresql.ENUM(
        "request_created",
        "attempt_created",
        "attempt_posted",
        "confirmed",
        "not_received",
        "inquiry_created",
        "inquiry_dismissed",
        "resend_requested",
        "resend_posted",
        "failure",
        name="venmo_confirmation_event_type",
        create_type=False,
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        cashout_completion_type.create(bind, checkfirst=True)
        notification_type.create(bind, checkfirst=True)
        venmo_status.create(bind, checkfirst=True)
        venmo_attempt_status.create(bind, checkfirst=True)
        venmo_inquiry_status.create(bind, checkfirst=True)
        venmo_event_type.create(bind, checkfirst=True)

    op.add_column(
        "cashout_requests",
        sa.Column("actual_paid_amount", sa.Numeric(precision=18, scale=2), nullable=True),
    )
    op.add_column(
        "cashout_requests",
        sa.Column("completion_type", cashout_completion_type, nullable=True),
    )
    op.add_column(
        "cashout_requests",
        sa.Column("coadmin_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_cashout_requests_coadmin_id_users",
        "cashout_requests",
        "users",
        ["coadmin_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_cashout_requests_coadmin_status",
        "cashout_requests",
        ["coadmin_id", "status"],
    )
    op.execute(
        """
        UPDATE cashout_requests AS cashout
        SET coadmin_id = staff.coadmin_id
        FROM users AS staff
        WHERE cashout.created_by_staff_id = staff.id
          AND staff.coadmin_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE cashout_requests
        SET actual_paid_amount = amount,
            completion_type = 'full'
        WHERE status = 'completed'
          AND actual_paid_amount IS NULL
          AND completion_type IS NULL
        """
    )
    op.create_check_constraint(
        "ck_cashout_requests_actual_paid_positive",
        "cashout_requests",
        "actual_paid_amount IS NULL OR actual_paid_amount > 0",
    )
    op.create_check_constraint(
        "ck_cashout_requests_completed_has_payment",
        "cashout_requests",
        "status != 'completed' OR (completion_type IS NOT NULL AND actual_paid_amount IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_cashout_requests_completion_only_when_completed",
        "cashout_requests",
        "completion_type IS NULL OR (status = 'completed' AND actual_paid_amount IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_cashout_requests_full_paid_matches_amount",
        "cashout_requests",
        "completion_type != 'full' OR actual_paid_amount = amount",
    )
    op.create_check_constraint(
        "ck_cashout_requests_partial_paid_less_than_amount",
        "cashout_requests",
        "completion_type != 'partial' OR (actual_paid_amount > 0 AND actual_paid_amount < amount)",
    )

    op.create_table(
        "coadmin_telegram_workflow_settings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("coadmin_id", sa.BigInteger(), nullable=False),
        sa.Column("cashout_group_id", sa.BigInteger(), nullable=True),
        sa.Column("venmo_confirmation_group_id", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(["coadmin_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("coadmin_id", name="uq_coadmin_telegram_workflow_settings_coadmin"),
    )
    op.create_index(
        "ix_coadmin_telegram_workflow_settings_cashout_group",
        "coadmin_telegram_workflow_settings",
        ["cashout_group_id"],
    )
    op.create_index(
        "ix_coadmin_telegram_workflow_settings_venmo_group",
        "coadmin_telegram_workflow_settings",
        ["venmo_confirmation_group_id"],
    )

    op.create_table(
        "media_assets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("coadmin_id", sa.BigInteger(), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["coadmin_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_media_assets_coadmin_created", "media_assets", ["coadmin_id", "created_at", "id"]
    )
    op.create_index("ix_media_assets_storage_key", "media_assets", ["storage_key"], unique=True)

    op.create_table(
        "persistent_notifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False),
        sa.Column("coadmin_id", sa.BigInteger(), nullable=True),
        sa.Column("type", notification_type, nullable=False),
        sa.Column("related_entity_type", sa.String(length=64), nullable=False),
        sa.Column("related_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coadmin_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_persistent_notifications_recipient_unread",
        "persistent_notifications",
        ["recipient_user_id", "read_at", "created_at"],
    )
    op.create_index(
        "ix_persistent_notifications_related",
        "persistent_notifications",
        ["related_entity_type", "related_entity_id"],
    )

    op.create_table(
        "venmo_confirmation_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("coadmin_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_by_staff_id", sa.BigInteger(), nullable=True),
        sa.Column("screenshot_media_asset_id", sa.BigInteger(), nullable=False),
        sa.Column("status", venmo_status, server_default="pending", nullable=False),
        sa.Column("payment_note", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_by_telegram_username", sa.String(length=255), nullable=True),
        sa.Column("confirmed_by_display_name", sa.String(length=255), nullable=True),
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
        sa.ForeignKeyConstraint(["coadmin_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_staff_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["screenshot_media_asset_id"], ["media_assets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_venmo_confirmation_requests_coadmin_status",
        "venmo_confirmation_requests",
        ["coadmin_id", "status"],
    )
    op.create_index(
        "ix_venmo_confirmation_requests_staff_created",
        "venmo_confirmation_requests",
        ["requested_by_staff_id", "created_at"],
    )

    op.create_table(
        "venmo_confirmation_attempts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", venmo_attempt_status, server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"], ["venmo_confirmation_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", "attempt_number", name="uq_venmo_confirmation_attempts_request_number"
        ),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_venmo_confirmation_attempts_chat_message",
        ),
    )
    op.create_index(
        "ix_venmo_confirmation_attempts_request_status",
        "venmo_confirmation_attempts",
        ["request_id", "status"],
    )

    op.create_table(
        "venmo_confirmation_inquiries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("source_attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("resulting_attempt_id", sa.BigInteger(), nullable=True),
        sa.Column("status", venmo_inquiry_status, server_default="open", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_staff_id", sa.BigInteger(), nullable=True),
        sa.Column("resent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resent_by_staff_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"], ["venmo_confirmation_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_attempt_id"], ["venmo_confirmation_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resulting_attempt_id"], ["venmo_confirmation_attempts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["dismissed_by_staff_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resent_by_staff_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_attempt_id", name="uq_venmo_confirmation_inquiries_source_attempt"
        ),
    )
    op.create_index(
        "ix_venmo_confirmation_inquiries_request_status",
        "venmo_confirmation_inquiries",
        ["request_id", "status"],
    )

    op.create_table(
        "venmo_confirmation_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_id", sa.BigInteger(), nullable=True),
        sa.Column("inquiry_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", venmo_event_type, nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_source", sa.String(length=64), nullable=True),
        sa.Column("actor_identifier", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["venmo_confirmation_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["venmo_confirmation_attempts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["inquiry_id"], ["venmo_confirmation_inquiries.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_venmo_confirmation_events_request_created",
        "venmo_confirmation_events",
        ["request_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Remove Phase 1 foundation tables and columns."""
    op.drop_index(
        "ix_venmo_confirmation_events_request_created", table_name="venmo_confirmation_events"
    )
    op.drop_table("venmo_confirmation_events")
    op.drop_index(
        "ix_venmo_confirmation_inquiries_request_status", table_name="venmo_confirmation_inquiries"
    )
    op.drop_table("venmo_confirmation_inquiries")
    op.drop_index(
        "ix_venmo_confirmation_attempts_request_status", table_name="venmo_confirmation_attempts"
    )
    op.drop_table("venmo_confirmation_attempts")
    op.drop_index(
        "ix_venmo_confirmation_requests_staff_created", table_name="venmo_confirmation_requests"
    )
    op.drop_index(
        "ix_venmo_confirmation_requests_coadmin_status", table_name="venmo_confirmation_requests"
    )
    op.drop_table("venmo_confirmation_requests")
    op.drop_index("ix_persistent_notifications_related", table_name="persistent_notifications")
    op.drop_index(
        "ix_persistent_notifications_recipient_unread", table_name="persistent_notifications"
    )
    op.drop_table("persistent_notifications")
    op.drop_index("ix_media_assets_storage_key", table_name="media_assets")
    op.drop_index("ix_media_assets_coadmin_created", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index(
        "ix_coadmin_telegram_workflow_settings_venmo_group",
        table_name="coadmin_telegram_workflow_settings",
    )
    op.drop_index(
        "ix_coadmin_telegram_workflow_settings_cashout_group",
        table_name="coadmin_telegram_workflow_settings",
    )
    op.drop_table("coadmin_telegram_workflow_settings")

    for name in (
        "ck_cashout_requests_partial_paid_less_than_amount",
        "ck_cashout_requests_full_paid_matches_amount",
        "ck_cashout_requests_completion_only_when_completed",
        "ck_cashout_requests_completed_has_payment",
        "ck_cashout_requests_actual_paid_positive",
    ):
        op.drop_constraint(name, "cashout_requests", type_="check")
    op.drop_index("ix_cashout_requests_coadmin_status", table_name="cashout_requests")
    op.drop_constraint(
        "fk_cashout_requests_coadmin_id_users", "cashout_requests", type_="foreignkey"
    )
    op.drop_column("cashout_requests", "coadmin_id")
    op.drop_column("cashout_requests", "completion_type")
    op.drop_column("cashout_requests", "actual_paid_amount")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum_name in (
            "venmo_confirmation_event_type",
            "venmo_confirmation_inquiry_status",
            "venmo_confirmation_attempt_status",
            "venmo_confirmation_status",
            "persistent_notification_type",
            "cashout_completion_type",
        ):
            op.execute(f"DROP TYPE IF EXISTS {enum_name}")
