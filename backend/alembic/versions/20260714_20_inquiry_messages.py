"""Add inquiry chat messages for the cashout Telegram group.

Revision ID: 20260714_20
Revises: 20260712_19
Create Date: 2026-07-14 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260714_20"
down_revision: str | None = "20260712_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create inquiry message storage for the cashout-group chat panel."""
    op.execute(
        """
        CREATE TYPE inquiry_direction AS ENUM ('inbound', 'outbound')
        """
    )
    op.execute(
        """
        CREATE TYPE inquiry_message_source AS ENUM (
            'telegram_external', 'inquiry', 'cashout_panel'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE inquiry_media_type AS ENUM ('none', 'photo', 'document')
        """
    )
    op.execute(
        """
        CREATE TYPE inquiry_media_download_status AS ENUM (
            'not_applicable', 'pending', 'ready', 'failed'
        )
        """
    )

    direction = postgresql.ENUM(
        "inbound",
        "outbound",
        name="inquiry_direction",
        create_type=False,
    )
    source = postgresql.ENUM(
        "telegram_external",
        "inquiry",
        "cashout_panel",
        name="inquiry_message_source",
        create_type=False,
    )
    media_type = postgresql.ENUM(
        "none",
        "photo",
        "document",
        name="inquiry_media_type",
        create_type=False,
    )
    media_status = postgresql.ENUM(
        "not_applicable",
        "pending",
        "ready",
        "failed",
        name="inquiry_media_download_status",
        create_type=False,
    )

    op.create_table(
        "inquiry_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_sender_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_display_name", sa.String(length=255), nullable=True),
        sa.Column("sender_username", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("direction", direction, nullable=False),
        sa.Column("message_source", source, nullable=False),
        sa.Column(
            "media_type",
            media_type,
            nullable=False,
            server_default="none",
        ),
        sa.Column("media_mime_type", sa.String(length=128), nullable=True),
        sa.Column("media_storage_key", sa.String(length=512), nullable=True),
        sa.Column("media_filename", sa.String(length=255), nullable=True),
        sa.Column("media_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "media_download_status",
            media_status,
            nullable=False,
            server_default="not_applicable",
        ),
        sa.Column(
            "sent_by_teleledger_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
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
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_inquiry_messages_chat_message",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_inquiry_messages_idempotency_key",
        ),
    )
    op.create_index(
        "ix_inquiry_messages_chat_date",
        "inquiry_messages",
        ["telegram_chat_id", "message_date", "id"],
    )
    op.create_index(
        "ix_inquiry_messages_visible_list",
        "inquiry_messages",
        ["telegram_chat_id", "message_date"],
        postgresql_where=sa.text("message_source != 'cashout_panel'"),
    )


def downgrade() -> None:
    """Drop inquiry message storage."""
    op.drop_index("ix_inquiry_messages_visible_list", table_name="inquiry_messages")
    op.drop_index("ix_inquiry_messages_chat_date", table_name="inquiry_messages")
    op.drop_table("inquiry_messages")
    op.execute("DROP TYPE IF EXISTS inquiry_media_download_status")
    op.execute("DROP TYPE IF EXISTS inquiry_media_type")
    op.execute("DROP TYPE IF EXISTS inquiry_message_source")
    op.execute("DROP TYPE IF EXISTS inquiry_direction")
