"""Add cursor pagination indexes for ledger history.

Revision ID: 20260707_17
Revises: 20260707_16
Create Date: 2026-07-07 00:00:17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260707_17"
down_revision: str | None = "20260707_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Support newest-first cursor queries for ledger history panels."""
    op.create_index(
        "ix_ledger_adjustments_created_id",
        "ledger_adjustments",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_staff_settlements_created_id",
        "staff_settlements",
        ["created_at", "id"],
    )


def downgrade() -> None:
    """Remove cursor pagination indexes."""
    op.drop_index(
        "ix_staff_settlements_created_id",
        table_name="staff_settlements",
    )
    op.drop_index(
        "ix_ledger_adjustments_created_id",
        table_name="ledger_adjustments",
    )
