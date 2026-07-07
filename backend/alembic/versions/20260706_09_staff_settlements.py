"""Add staff settlements ledger tables.

Revision ID: 20260706_09
Revises: 20260706_08
Create Date: 2026-07-06 00:00:08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260706_09"
down_revision: str | None = "20260706_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create staff settlement withdrawal records and audit logs."""
    op.execute(
        """
        CREATE TYPE staff_settlement_status AS ENUM (
            'pending', 'claimed', 'done', 'cancelled'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE staff_settlement_audit_action AS ENUM (
            'created', 'claimed', 'done', 'cancelled'
        )
        """
    )
    settlement_status = postgresql.ENUM(
        "pending",
        "claimed",
        "done",
        "cancelled",
        name="staff_settlement_status",
        create_type=False,
    )
    audit_action = postgresql.ENUM(
        "created",
        "claimed",
        "done",
        "cancelled",
        name="staff_settlement_audit_action",
        create_type=False,
    )

    op.create_table(
        "staff_settlements",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "status",
            settlement_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("claimed_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_admin_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["claimed_by_admin_id"],
            ["users.id"],
            name="fk_staff_settlements_claimed_by_admin_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_admin_id"],
            ["users.id"],
            name="fk_staff_settlements_completed_by_admin_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["users.id"],
            name="fk_staff_settlements_created_by_admin_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["users.id"],
            name="fk_staff_settlements_staff_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_staff_settlements"),
    )
    op.create_index(
        "ix_staff_settlements_staff_completed",
        "staff_settlements",
        ["staff_id", "completed_at"],
    )
    op.create_index(
        "ix_staff_settlements_status_created",
        "staff_settlements",
        ["status", "created_at"],
    )

    op.create_table(
        "staff_settlement_audit_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("previous_status", settlement_status, nullable=True),
        sa.Column("new_status", settlement_status, nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_staff_settlement_audit_logs_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["staff_settlements.id"],
            name="fk_staff_settlement_audit_logs_settlement_id_staff_settlements",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_staff_settlement_audit_logs"),
    )
    op.create_index(
        "ix_staff_settlement_audit_settlement_created",
        "staff_settlement_audit_logs",
        ["settlement_id", "created_at"],
    )


def downgrade() -> None:
    """Remove staff settlement ledger tables and enum types."""
    op.drop_index(
        "ix_staff_settlement_audit_settlement_created",
        table_name="staff_settlement_audit_logs",
    )
    op.drop_table("staff_settlement_audit_logs")
    op.drop_index(
        "ix_staff_settlements_status_created",
        table_name="staff_settlements",
    )
    op.drop_index(
        "ix_staff_settlements_staff_completed",
        table_name="staff_settlements",
    )
    op.drop_table("staff_settlements")
    op.execute("DROP TYPE staff_settlement_audit_action")
    op.execute("DROP TYPE staff_settlement_status")
