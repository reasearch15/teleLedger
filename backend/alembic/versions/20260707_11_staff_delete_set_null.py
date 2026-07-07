"""Allow staff hard-delete by nulling historical user references.

Revision ID: 20260707_11
Revises: 20260707_10
Create Date: 2026-07-07 00:00:11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260707_11"
down_revision: str | None = "20260707_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Relax staff-linked foreign keys so user rows can be hard-deleted."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE cashout_requests "
                "ALTER COLUMN created_by_staff_id DROP NOT NULL"
            )
        )
    else:
        op.alter_column(
            "cashout_requests",
            "created_by_staff_id",
            existing_type=sa.BigInteger(),
            nullable=True,
        )
    op.drop_constraint(
        "fk_cashout_requests_created_by_staff_id_users",
        "cashout_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_cashout_requests_created_by_staff_id_users",
        "cashout_requests",
        "users",
        ["created_by_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )

    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text("ALTER TABLE staff_settlements ALTER COLUMN staff_id DROP NOT NULL")
        )
    else:
        op.alter_column(
            "staff_settlements",
            "staff_id",
            existing_type=sa.BigInteger(),
            nullable=True,
        )
    op.drop_constraint(
        "fk_staff_settlements_staff_id_users",
        "staff_settlements",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_staff_settlements_staff_id_users",
        "staff_settlements",
        "users",
        ["staff_id"],
        ["id"],
        ondelete="SET NULL",
    )

    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE staff_settlement_audit_logs "
                "ALTER COLUMN actor_user_id DROP NOT NULL"
            )
        )
    else:
        op.alter_column(
            "staff_settlement_audit_logs",
            "actor_user_id",
            existing_type=sa.BigInteger(),
            nullable=True,
        )
    op.drop_constraint(
        "fk_staff_settlement_audit_logs_actor_user_id_users",
        "staff_settlement_audit_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_staff_settlement_audit_logs_actor_user_id_users",
        "staff_settlement_audit_logs",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Restore restrictive staff-linked foreign keys."""
    op.drop_constraint(
        "fk_staff_settlement_audit_logs_actor_user_id_users",
        "staff_settlement_audit_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_staff_settlement_audit_logs_actor_user_id_users",
        "staff_settlement_audit_logs",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "staff_settlement_audit_logs",
        "actor_user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.drop_constraint(
        "fk_staff_settlements_staff_id_users",
        "staff_settlements",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_staff_settlements_staff_id_users",
        "staff_settlements",
        "users",
        ["staff_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "staff_settlements",
        "staff_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.drop_constraint(
        "fk_cashout_requests_created_by_staff_id_users",
        "cashout_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_cashout_requests_created_by_staff_id_users",
        "cashout_requests",
        "users",
        ["created_by_staff_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "cashout_requests",
        "created_by_staff_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
