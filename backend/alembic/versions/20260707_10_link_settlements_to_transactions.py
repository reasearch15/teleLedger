"""Link completed settlements to included transactions.

Revision ID: 20260707_10
Revises: 20260706_09
Create Date: 2026-07-07 00:00:10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260707_10"
down_revision: str | None = "20260706_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Associate payments and cashouts with their settlement."""
    op.add_column(
        "payment_events",
        sa.Column("settlement_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "cashout_requests",
        sa.Column("settlement_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_payment_events_settlement_id_staff_settlements",
        "payment_events",
        "staff_settlements",
        ["settlement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_cashout_requests_settlement_id_staff_settlements",
        "cashout_requests",
        "staff_settlements",
        ["settlement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_payment_events_staff_unsettled_completed",
        "payment_events",
        ["completed_by_staff_id", "settlement_id", "completed_at"],
    )
    op.create_index(
        "ix_cashout_requests_staff_unsettled_completed",
        "cashout_requests",
        ["created_by_staff_id", "settlement_id", "completed_at"],
    )


def downgrade() -> None:
    """Remove transaction settlement associations."""
    op.drop_index(
        "ix_cashout_requests_staff_unsettled_completed",
        table_name="cashout_requests",
    )
    op.drop_index(
        "ix_payment_events_staff_unsettled_completed",
        table_name="payment_events",
    )
    op.drop_constraint(
        "fk_cashout_requests_settlement_id_staff_settlements",
        "cashout_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_payment_events_settlement_id_staff_settlements",
        "payment_events",
        type_="foreignkey",
    )
    op.drop_column("cashout_requests", "settlement_id")
    op.drop_column("payment_events", "settlement_id")
