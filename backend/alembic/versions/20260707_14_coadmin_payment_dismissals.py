"""Add coadmin hierarchy and scoped payment dismissals.

Revision ID: 20260707_14
Revises: 20260707_13
Create Date: 2026-07-07 00:00:14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260707_14"
down_revision: str | None = "20260707_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add coadmin role, staff ownership, and scoped dismissals."""
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'coadmin'")
    op.add_column("users", sa.Column("coadmin_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_users_coadmin_id_users",
        "users",
        "users",
        ["coadmin_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_coadmin_id", "users", ["coadmin_id"])

    op.create_table(
        "payment_event_coadmin_dismissals",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("payment_event_id", sa.BigInteger(), nullable=False),
        sa.Column("coadmin_id", sa.BigInteger(), nullable=False),
        sa.Column("dismissed_by_staff_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "action",
            sa.String(length=32),
            server_default="not_ours",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["coadmin_id"],
            ["users.id"],
            name="fk_payment_dismissals_coadmin_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dismissed_by_staff_id"],
            ["users.id"],
            name="fk_payment_dismissals_staff_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["payment_event_id"],
            ["payment_events.id"],
            name="fk_payment_dismissals_payment_event_id_payment_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_payment_event_coadmin_dismissals",
        ),
        sa.UniqueConstraint(
            "payment_event_id",
            "coadmin_id",
            name="uq_payment_event_coadmin_dismissals_payment_coadmin",
        ),
    )
    op.create_index(
        "ix_payment_event_coadmin_dismissals_payment_event_id",
        "payment_event_coadmin_dismissals",
        ["payment_event_id"],
    )
    op.create_index(
        "ix_payment_event_coadmin_dismissals_coadmin_id",
        "payment_event_coadmin_dismissals",
        ["coadmin_id"],
    )


def downgrade() -> None:
    """Remove scoped dismissals and staff coadmin ownership."""
    op.drop_index(
        "ix_payment_event_coadmin_dismissals_coadmin_id",
        table_name="payment_event_coadmin_dismissals",
    )
    op.drop_index(
        "ix_payment_event_coadmin_dismissals_payment_event_id",
        table_name="payment_event_coadmin_dismissals",
    )
    op.drop_table("payment_event_coadmin_dismissals")
    op.drop_index("ix_users_coadmin_id", table_name="users")
    op.drop_constraint("fk_users_coadmin_id_users", "users", type_="foreignkey")
    op.drop_column("users", "coadmin_id")
