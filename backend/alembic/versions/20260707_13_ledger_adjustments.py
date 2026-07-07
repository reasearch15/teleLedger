"""Add manual ledger adjustment records.

Revision ID: 20260707_13
Revises: 20260707_12
Create Date: 2026-07-07 00:00:11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260707_13"
down_revision: str | None = "20260707_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create append-only ledger adjustment table."""
    op.execute("CREATE TYPE ledger_adjustment_type AS ENUM ('total_in_adjustment')")
    adjustment_type = postgresql.ENUM(
        "total_in_adjustment",
        name="ledger_adjustment_type",
        create_type=False,
    )
    op.create_table(
        "ledger_adjustments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=True),
        sa.Column("type", adjustment_type, nullable=False),
        sa.Column("amount_delta", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("previous_total_in", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("new_total_in", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("settlement_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["users.id"],
            name="fk_ledger_adjustments_created_by_admin_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["staff_settlements.id"],
            name="fk_ledger_adjustments_settlement_id_staff_settlements",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["users.id"],
            name="fk_ledger_adjustments_staff_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_adjustments"),
    )
    op.create_index(
        "ix_ledger_adjustments_staff_id",
        "ledger_adjustments",
        ["staff_id"],
    )
    op.create_index(
        "ix_ledger_adjustments_settlement_id",
        "ledger_adjustments",
        ["settlement_id"],
    )
    op.create_index(
        "ix_ledger_adjustments_created_at",
        "ledger_adjustments",
        ["created_at"],
    )


def downgrade() -> None:
    """Remove manual ledger adjustment table."""
    op.drop_index(
        "ix_ledger_adjustments_created_at",
        table_name="ledger_adjustments",
    )
    op.drop_index(
        "ix_ledger_adjustments_settlement_id",
        table_name="ledger_adjustments",
    )
    op.drop_index(
        "ix_ledger_adjustments_staff_id",
        table_name="ledger_adjustments",
    )
    op.drop_table("ledger_adjustments")
    op.execute("DROP TYPE ledger_adjustment_type")
