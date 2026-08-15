"""Add Venmo confirmation request cursor index.

Revision ID: 20260715_26
Revises: 20260715_25
Create Date: 2026-07-15 00:00:26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_26"
down_revision: str | None = "20260715_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_venmo_confirmation_requests_coadmin_created_id",
        "venmo_confirmation_requests",
        ["coadmin_id", "created_at", "id"],
    )
    op.create_index(
        "ix_venmo_confirmation_requests_created_id",
        "venmo_confirmation_requests",
        ["created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_venmo_confirmation_requests_created_id",
        table_name="venmo_confirmation_requests",
    )
    op.drop_index(
        "ix_venmo_confirmation_requests_coadmin_created_id",
        table_name="venmo_confirmation_requests",
    )
