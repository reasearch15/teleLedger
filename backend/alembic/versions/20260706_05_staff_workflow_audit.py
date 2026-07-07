"""Add staff workflow ownership, colors, and payment audit history.

Revision ID: 20260706_05
Revises: 20260706_04
Create Date: 2026-07-06 00:00:04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260706_05"
down_revision: str | None = "20260706_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install the strict three-state staff workflow and audit table."""
    op.add_column(
        "users",
        sa.Column(
            "staff_color",
            sa.String(length=7),
            server_default="#2563EB",
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE users
        SET staff_color = CASE MOD(id, 8)
            WHEN 0 THEN '#2563EB'
            WHEN 1 THEN '#7C3AED'
            WHEN 2 THEN '#EA580C'
            WHEN 3 THEN '#0D9488'
            WHEN 4 THEN '#DB2777'
            WHEN 5 THEN '#4F46E5'
            WHEN 6 THEN '#059669'
            ELSE '#B45309'
        END
        """
    )

    op.alter_column(
        "payment_events",
        "claimed_by",
        new_column_name="claimed_by_staff_id",
    )
    op.alter_column(
        "payment_events",
        "done_by",
        new_column_name="completed_by_staff_id",
    )
    op.alter_column(
        "payment_events",
        "done_at",
        new_column_name="completed_at",
    )

    op.execute("ALTER TABLE payment_events ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE payment_status RENAME TO payment_status_old")
    op.execute(
        "CREATE TYPE payment_status AS ENUM ('pending', 'in_progress', 'done')"
    )
    op.execute(
        """
        ALTER TABLE payment_events
        ALTER COLUMN status TYPE payment_status
        USING (
            CASE
                WHEN status::text IN ('ignored', 'manual_review') THEN 'pending'
                ELSE status::text
            END
        )::payment_status
        """
    )
    op.execute("DROP TYPE payment_status_old")
    op.execute(
        "ALTER TABLE payment_events ALTER COLUMN status SET DEFAULT 'pending'"
    )

    op.create_foreign_key(
        "fk_payment_events_claimed_by_staff_id_users",
        "payment_events",
        "users",
        ["claimed_by_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_payment_events_completed_by_staff_id_users",
        "payment_events",
        "users",
        ["completed_by_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        """
        CREATE TYPE payment_audit_action AS ENUM (
            'created', 'claimed', 'unclaimed', 'done', 'reopened', 'reassigned'
        )
        """
    )
    audit_action = postgresql.ENUM(
        "created",
        "claimed",
        "unclaimed",
        "done",
        "reopened",
        "reassigned",
        name="payment_audit_action",
        create_type=False,
    )
    payment_status = postgresql.ENUM(
        "pending",
        "in_progress",
        "done",
        name="payment_status",
        create_type=False,
    )
    op.create_table(
        "payment_audit_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("payment_event_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_staff_id", sa.BigInteger(), nullable=True),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("from_status", payment_status, nullable=True),
        sa.Column("to_status", payment_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_payment_audit_logs_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["payment_event_id"],
            ["payment_events.id"],
            name="fk_payment_audit_logs_payment_event_id_payment_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_staff_id"],
            ["users.id"],
            name="fk_payment_audit_logs_subject_staff_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_audit_logs"),
    )
    op.create_index(
        "ix_payment_audit_logs_payment_created",
        "payment_audit_logs",
        ["payment_event_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO payment_audit_logs (
            payment_event_id,
            actor_user_id,
            subject_staff_id,
            action,
            from_status,
            to_status,
            created_at
        )
        SELECT
            id,
            NULL,
            NULL,
            'created',
            NULL,
            'pending',
            created_at
        FROM payment_events
        """
    )


def downgrade() -> None:
    """Restore the pre-audit workflow columns and enum."""
    op.drop_index(
        "ix_payment_audit_logs_payment_created",
        table_name="payment_audit_logs",
    )
    op.drop_table("payment_audit_logs")
    op.execute("DROP TYPE payment_audit_action")

    op.drop_constraint(
        "fk_payment_events_completed_by_staff_id_users",
        "payment_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_payment_events_claimed_by_staff_id_users",
        "payment_events",
        type_="foreignkey",
    )

    op.execute("ALTER TABLE payment_events ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE payment_status RENAME TO payment_status_new")
    op.execute(
        """
        CREATE TYPE payment_status AS ENUM (
            'pending', 'in_progress', 'done', 'ignored', 'manual_review'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE payment_events
        ALTER COLUMN status TYPE payment_status
        USING status::text::payment_status
        """
    )
    op.execute("DROP TYPE payment_status_new")
    op.execute(
        "ALTER TABLE payment_events ALTER COLUMN status SET DEFAULT 'pending'"
    )

    op.alter_column(
        "payment_events",
        "completed_at",
        new_column_name="done_at",
    )
    op.alter_column(
        "payment_events",
        "completed_by_staff_id",
        new_column_name="done_by",
    )
    op.alter_column(
        "payment_events",
        "claimed_by_staff_id",
        new_column_name="claimed_by",
    )
    op.drop_column("users", "staff_color")
