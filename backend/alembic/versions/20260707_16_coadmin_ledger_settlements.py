"""Add coadmin settlement ownership fields.

Revision ID: 20260707_16
Revises: 20260707_15
Create Date: 2026-07-07 00:00:16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260707_16"
down_revision: str | None = "20260707_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow one settlement record to represent a whole coadmin team."""
    op.add_column(
        "staff_settlements",
        sa.Column("coadmin_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "staff_settlements",
        sa.Column("scope", sa.String(length=16), server_default="staff", nullable=False),
    )
    op.create_foreign_key(
        "fk_staff_settlements_coadmin_id_users",
        "staff_settlements",
        "users",
        ["coadmin_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_staff_settlements_coadmin_completed",
        "staff_settlements",
        ["coadmin_id", "completed_at"],
    )


def downgrade() -> None:
    """Remove coadmin settlement ownership fields."""
    op.drop_index(
        "ix_staff_settlements_coadmin_completed",
        table_name="staff_settlements",
    )
    op.drop_constraint(
        "fk_staff_settlements_coadmin_id_users",
        "staff_settlements",
        type_="foreignkey",
    )
    op.drop_column("staff_settlements", "scope")
    op.drop_column("staff_settlements", "coadmin_id")
