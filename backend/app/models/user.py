from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRole(StrEnum):
    """Authorization roles supported by Telegram Ledger."""

    ADMIN = "admin"
    STAFF = "staff"


class User(Base):
    """Local administrator or staff account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda roles: [role.value for role in roles],
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    staff_color: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
        default="#2563EB",
        server_default="#2563EB",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
