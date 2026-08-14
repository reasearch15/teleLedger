"""Add Venmo payment screenshot upload event type.

Revision ID: 20260715_25
Revises: 20260715_24
Create Date: 2026-07-15 00:00:25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_25"
down_revision: str | None = "20260715_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE venmo_confirmation_event_type ADD VALUE IF NOT EXISTS "
            "'payment_screenshot_uploaded'"
        )
    else:
        with op.batch_alter_table("venmo_confirmation_events") as batch_op:
            batch_op.alter_column(
                "event_type",
                existing_type=sa.Enum(
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
                ),
                type_=sa.Enum(
                    "request_created",
                    "attempt_created",
                    "attempt_posted",
                    "confirmed",
                    "not_received",
                    "inquiry_created",
                    "inquiry_dismissed",
                    "resend_requested",
                    "resend_posted",
                    "payment_screenshot_uploaded",
                    "failure",
                    name="venmo_confirmation_event_type",
                ),
            )


def downgrade() -> None:
    pass
