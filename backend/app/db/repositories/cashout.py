from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.db.repositories.base import BaseRepository
from app.models.cashout import (
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.user import User
from app.telegram.peer_ids import normalize_telegram_chat_id


@dataclass(frozen=True, slots=True)
class CashoutStaffIdentity:
    """Small staff identity embedded in cashout responses."""

    id: int
    username: str
    color: str


@dataclass(frozen=True, slots=True)
class CashoutListItem:
    """Cashout request plus requesting/completing staff identities."""

    cashout: CashoutRequest
    requested_by: CashoutStaffIdentity
    completed_by: CashoutStaffIdentity | None


@dataclass(frozen=True, slots=True)
class CashoutListPage:
    """Bounded newest-first cashout page."""

    items: list[CashoutListItem]
    has_more: bool


@dataclass(frozen=True, slots=True)
class CashoutAuditRecord:
    """Audit row with its actor display name."""

    audit: CashoutRequestAudit
    actor_username: str | None


@dataclass(frozen=True, slots=True)
class CashoutReactionCandidate:
    """Open cashout row eligible for reaction reconciliation."""

    cashout_id: int
    telegram_message_id: int
    telegram_chat_id: int | None


class CashoutRepository(BaseRepository[CashoutRequest]):
    """Persistence for cashout workflow and its delivery outbox."""

    async def add(self, cashout: CashoutRequest) -> CashoutRequest:
        self._session.add(cashout)
        await self._session.flush()
        return cashout

    async def add_audit(self, audit: CashoutRequestAudit) -> CashoutRequestAudit:
        self._session.add(audit)
        await self._session.flush()
        return audit

    async def get_by_idempotency_key(
        self,
        staff_id: int,
        idempotency_key: str,
    ) -> CashoutRequest | None:
        statement = select(CashoutRequest).where(
            CashoutRequest.created_by_staff_id == staff_id,
            CashoutRequest.idempotency_key == idempotency_key,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_id_for_update(
        self,
        cashout_id: int,
    ) -> CashoutRequest | None:
        statement = select(CashoutRequest).where(CashoutRequest.id == cashout_id).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_telegram_message_id_for_update(
        self,
        telegram_message_id: int,
    ) -> CashoutRequest | None:
        """Legacy message-id-only lookup retained for callers/tests."""
        statement = (
            select(CashoutRequest)
            .where(CashoutRequest.telegram_message_id == telegram_message_id)
            .with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_telegram_message_for_update(
        self,
        *,
        telegram_message_id: int,
        telegram_chat_id: int,
        fallback_chat_id: int | None = None,
    ) -> CashoutRequest | None:
        """Match a cashout by Telegram chat + message IDs.

        Prefers an exact ``(telegram_chat_id, telegram_message_id)`` match.
        Falls back to message-id-only rows whose chat ID is null (historical)
        or equals the configured cashout group, so pre-migration rows still work.
        """
        normalized_chat = normalize_telegram_chat_id(telegram_chat_id)
        normalized_fallback = normalize_telegram_chat_id(fallback_chat_id)
        allowed_chats = {
            chat
            for chat in (normalized_chat, normalized_fallback)
            if chat is not None
        }

        exact = (
            select(CashoutRequest)
            .where(
                CashoutRequest.telegram_message_id == telegram_message_id,
                CashoutRequest.telegram_chat_id == normalized_chat,
            )
            .with_for_update()
        )
        matched = (await self._session.execute(exact)).scalar_one_or_none()
        if matched is not None:
            return matched

        fallback = (
            select(CashoutRequest)
            .where(
                CashoutRequest.telegram_message_id == telegram_message_id,
                or_(
                    CashoutRequest.telegram_chat_id.is_(None),
                    CashoutRequest.telegram_chat_id.in_(allowed_chats),
                ),
            )
            .order_by(CashoutRequest.id.desc())
            .with_for_update()
            .limit(1)
        )
        return (await self._session.execute(fallback)).scalar_one_or_none()

    async def list_reaction_candidate_message_ids(
        self,
        *,
        limit: int,
    ) -> list[int]:
        candidates = await self.list_reaction_candidates(limit=limit)
        return [candidate.telegram_message_id for candidate in candidates]

    async def list_reaction_candidates(
        self,
        *,
        limit: int,
    ) -> list[CashoutReactionCandidate]:
        statement = (
            select(
                CashoutRequest.id,
                CashoutRequest.telegram_message_id,
                CashoutRequest.telegram_chat_id,
            )
            .where(
                CashoutRequest.telegram_message_id.is_not(None),
                CashoutRequest.telegram_status == CashoutTelegramStatus.SENT,
                CashoutRequest.status.not_in(
                    [CashoutStatus.COMPLETED, CashoutStatus.CANCELLED]
                ),
            )
            .order_by(
                CashoutRequest.telegram_sent_at.desc(),
                CashoutRequest.id.desc(),
            )
            .limit(limit)
        )
        return [
            CashoutReactionCandidate(
                cashout_id=int(row[0]),
                telegram_message_id=int(row[1]),
                telegram_chat_id=int(row[2]) if row[2] is not None else None,
            )
            for row in (await self._session.execute(statement)).all()
            if row[1] is not None
        ]

    async def list_requests(
        self,
        *,
        staff_id: int | None,
        status: CashoutStatus | None,
        telegram_status: CashoutTelegramStatus | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> CashoutListPage:
        conditions = self._list_conditions(
            staff_id=staff_id,
            status=status,
            telegram_status=telegram_status,
            search=search,
        )
        requester = aliased(User, name="cashout_requester")
        completer = aliased(User, name="cashout_completer")
        statement = (
            select(
                CashoutRequest,
                requester.id,
                requester.username,
                requester.staff_color,
                completer.id,
                completer.username,
                completer.staff_color,
            )
            .join(requester, CashoutRequest.created_by_staff_id == requester.id)
            .outerjoin(
                completer,
                CashoutRequest.completed_by_staff_id == completer.id,
            )
            .where(*conditions)
            .order_by(CashoutRequest.created_at.desc(), CashoutRequest.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )
        rows = (await self._session.execute(statement)).all()
        return CashoutListPage(
            items=[
                CashoutListItem(
                    cashout=row[0],
                    requested_by=CashoutStaffIdentity(
                        id=row[1],
                        username=row[2],
                        color=row[3],
                    ),
                    completed_by=(
                        CashoutStaffIdentity(
                            id=row[4],
                            username=row[5],
                            color=row[6],
                        )
                        if row[4] is not None
                        else None
                    ),
                )
                for row in rows[:limit]
            ],
            has_more=len(rows) > limit,
        )

    async def claim_next_delivery(
        self,
        now: datetime,
    ) -> CashoutRequest | None:
        statement = (
            select(CashoutRequest)
            .where(
                CashoutRequest.telegram_status != CashoutTelegramStatus.SENT,
                CashoutRequest.status != CashoutStatus.CANCELLED,
                or_(
                    CashoutRequest.telegram_next_attempt_at.is_(None),
                    CashoutRequest.telegram_next_attempt_at <= now,
                ),
            )
            .order_by(CashoutRequest.created_at.asc(), CashoutRequest.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_audit(self, cashout_id: int) -> list[CashoutAuditRecord]:
        actor = aliased(User, name="cashout_audit_actor")
        statement = (
            select(CashoutRequestAudit, actor.username)
            .outerjoin(actor, CashoutRequestAudit.actor_user_id == actor.id)
            .where(CashoutRequestAudit.cashout_request_id == cashout_id)
            .order_by(
                CashoutRequestAudit.created_at.asc(),
                CashoutRequestAudit.id.asc(),
            )
        )
        rows = (await self._session.execute(statement)).all()
        return [CashoutAuditRecord(audit=row[0], actor_username=row[1]) for row in rows]

    @staticmethod
    def _list_conditions(
        *,
        staff_id: int | None,
        status: CashoutStatus | None,
        telegram_status: CashoutTelegramStatus | None,
        search: str | None,
    ) -> tuple[ColumnElement[bool], ...]:
        conditions: list[ColumnElement[bool]] = []
        if staff_id is not None:
            conditions.append(CashoutRequest.created_by_staff_id == staff_id)
            conditions.append(CashoutRequest.status != CashoutStatus.CANCELLED)
        if status is not None:
            conditions.append(CashoutRequest.status == status)
        if telegram_status is not None:
            conditions.append(CashoutRequest.telegram_status == telegram_status)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            conditions.append(
                or_(
                    CashoutRequest.player_tag.ilike(pattern, escape="\\"),
                    CashoutRequest.request_number.ilike(pattern, escape="\\"),
                    CashoutRequest.notes.ilike(pattern, escape="\\"),
                )
            )
        return tuple(conditions)
