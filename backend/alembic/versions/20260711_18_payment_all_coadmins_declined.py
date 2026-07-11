"""Track payments declined by all coadmins and admin review state.

Revision ID: 20260711_18
Revises: 20260707_17
Create Date: 2026-07-11 00:00:18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_18"
down_revision: str | None = "20260707_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add all-coadmin decline and admin review dismissal timestamps."""
    op.add_column(
        "payment_events",
        sa.Column(
            "all_coadmins_declined_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "payment_events",
        sa.Column(
            "declined_review_dismissed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_payment_events_all_coadmins_declined_at",
        "payment_events",
        ["all_coadmins_declined_at"],
    )
    op.create_index(
        "ix_payment_events_declined_review_dismissed_at",
        "payment_events",
        ["declined_review_dismissed_at"],
    )


def downgrade() -> None:
    """Remove all-coadmin decline and admin review dismissal timestamps."""
    op.drop_index(
        "ix_payment_events_declined_review_dismissed_at",
        table_name="payment_events",
    )
    op.drop_index(
        "ix_payment_events_all_coadmins_declined_at",
        table_name="payment_events",
    )
    op.drop_column("payment_events", "declined_review_dismissed_at")
    op.drop_column("payment_events", "all_coadmins_declined_at")
