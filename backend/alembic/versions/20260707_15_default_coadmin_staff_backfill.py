"""Backfill staff coadmin ownership and require coadmin for staff rows.

Revision ID: 20260707_15
Revises: 20260707_14
Create Date: 2026-07-07 00:00:15
"""

from __future__ import annotations

from collections.abc import Sequence

from app.db.coadmin_backfill import (  # noqa: F401
    DEFAULT_COADMIN_PASSWORD,
    DEFAULT_COADMIN_USERNAME,
    STAFF_REQUIRES_COADMIN_CONSTRAINT,
    add_staff_coadmin_required_constraint,
    assign_orphan_staff,
    ensure_coadmin_target,
)

revision: str = "20260707_15"
down_revision: str | None = "20260707_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Idempotent backfill for databases that reached 20260707_14 before inlining."""
    from app.db.coadmin_backfill import run_coadmin_backfill

    run_coadmin_backfill()


def downgrade() -> None:
    """Drop the staff ownership requirement without undoing backfilled assignments."""
    from app.db.coadmin_backfill import drop_staff_coadmin_required_constraint

    drop_staff_coadmin_required_constraint()
