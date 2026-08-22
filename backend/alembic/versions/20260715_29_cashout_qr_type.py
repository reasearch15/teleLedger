"""Add QR cash-out type and optional QR media reference.

Revision ID: 20260715_29
Revises: 20260715_28
Create Date: 2026-07-15 00:00:29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260715_29"
down_revision: str | None = "20260715_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cashout_type discriminator and QR media asset link."""
    op.execute("CREATE TYPE cashout_type AS ENUM ('standard', 'qr')")
    cashout_type = postgresql.ENUM(
        "standard",
        "qr",
        name="cashout_type",
        create_type=False,
    )
    op.add_column(
        "cashout_requests",
        sa.Column(
            "cashout_type",
            cashout_type,
            nullable=False,
            server_default="standard",
        ),
    )
    op.add_column(
        "cashout_requests",
        sa.Column("qr_media_asset_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_cashout_requests_qr_media_asset_id",
        "cashout_requests",
        "media_assets",
        ["qr_media_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "cashout_requests_qr_media_required",
        "cashout_requests",
        "(cashout_type != 'qr' OR qr_media_asset_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "cashout_requests_standard_no_qr_media",
        "cashout_requests",
        "(cashout_type = 'qr' OR qr_media_asset_id IS NULL)",
    )


def downgrade() -> None:
    """Remove QR cash-out columns and enum."""
    op.drop_constraint(
        "cashout_requests_standard_no_qr_media",
        "cashout_requests",
        type_="check",
    )
    op.drop_constraint(
        "cashout_requests_qr_media_required",
        "cashout_requests",
        type_="check",
    )
    op.drop_constraint(
        "fk_cashout_requests_qr_media_asset_id",
        "cashout_requests",
        type_="foreignkey",
    )
    op.drop_column("cashout_requests", "qr_media_asset_id")
    op.drop_column("cashout_requests", "cashout_type")
    op.execute("DROP TYPE cashout_type")
