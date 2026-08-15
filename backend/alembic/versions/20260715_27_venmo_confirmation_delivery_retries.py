"""Add Venmo confirmation delivery retry metadata.

Revision ID: 20260715_27
Revises: 20260715_26
Create Date: 2026-07-15 00:00:27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_27"
down_revision: str | None = "20260715_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("venmo_confirmation_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivery_attempts",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("delivery_lease_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_venmo_confirmation_attempts_delivery_due",
            ["status", "next_retry_at", "delivery_lease_until"],
        )


def downgrade() -> None:
    with op.batch_alter_table("venmo_confirmation_attempts") as batch_op:
        batch_op.drop_index("ix_venmo_confirmation_attempts_delivery_due")
        batch_op.drop_column("delivery_lease_until")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("delivery_attempts")
