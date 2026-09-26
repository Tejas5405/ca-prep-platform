"""Doubt threads: create, list, reply, resolve.

VISIBILITY IS THE WHOLE SECURITY MODEL HERE, and it is enforced in SQL rather than
in the router: ``list_for`` and ``get_for`` take the caller's user id and staff
flag, and a student's query simply cannot return another student's thread. A
permission check that runs after the fetch is one refactor away from being
skipped; a WHERE clause is not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select, update

from app.models.doubt import Doubt, DoubtReply
from app.models.user import User

#: Statuses a staff member may set. CLOSED is the student's own "thanks, done".
STAFF_STATUSES = frozenset({"OPEN", "ANSWERED", "RESOLVED"})
STUDENT_STATUSES = frozenset({"OPEN", "CLOSED"})


def utcnow() -> datetime:
    return datetime.now(UTC)


class SqlDoubtRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        title: str,
        body: str,
        course_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
        chapter_id: uuid.UUID | None = None,
        question_id: uuid.UUID | None = None,
        priority: str = "NORMAL",
    ) -> Doubt:
        now = utcnow()
        doubt = Doubt(
            user_id=user_id,
            title=title.strip(),
            body=body.strip(),
            course_id=course_id,
            subject_id=subject_id,
            chapter_id=chapter_id,
            question_id=question_id,
            status="OPEN",
            priority=priority,
            reply_count=0,
            last_activity_at=now,
            created_at=now,
            updated_at=now,
        )
        self._session.add(doubt)
        await self._session.flush()
        return doubt

    async def add_reply(
        self, *, doubt_id: uuid.UUID, user_id: uuid.UUID, body: str, is_staff_answer: bool
    ) -> DoubtReply:
        now = utcnow()
        reply = DoubtReply(
            doubt_id=doubt_id,
            user_id=user_id,
            body=body.strip(),
            is_staff_answer=is_staff_answer,
            is_accepted=False,
            upvotes=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(reply)

        # The parent row is updated in the same transaction: a reply that does not
        # move ``last_activity_at`` is a doubt that looks abandoned in the queue,
        # and the reply count is what the list endpoint renders.
        values: dict[str, Any] = {
            "reply_count": Doubt.reply_count + 1,
            "last_activity_at": now,
            "updated_at": now,
        }
        # A staff answer moves an OPEN doubt to ANSWERED. It does NOT move it to
        # RESOLVED: the student decides whether the doubt is actually resolved.
        if is_staff_answer:
            values["status"] = "ANSWERED"
        await self._session.execute(update(Doubt).where(Doubt.id == doubt_id).values(**values))
        await self._session.flush()
        return reply

    async def get(self, doubt_id: uuid.UUID) -> Doubt | None:
        return (
            await self._session.execute(select(Doubt).where(Doubt.id == doubt_id))
        ).scalar_one_or_none()

    async def get_for(
        self, *, doubt_id: uuid.UUID, user_id: uuid.UUID, is_staff: bool
    ) -> Doubt | None:
        stmt = select(Doubt).where(Doubt.id == doubt_id)
        if not is_staff:
            stmt = stmt.where(Doubt.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for(
        self,
        *,
        user_id: uuid.UUID,
        is_staff: bool,
        status: str | None = None,
        subject_id: uuid.UUID | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[Doubt], int]:
        conditions: list[Any] = []
        if not is_staff:
            conditions.append(Doubt.user_id == user_id)
        if status is not None:
            conditions.append(Doubt.status == status)
        if subject_id is not None:
            conditions.append(Doubt.subject_id == subject_id)

        count_stmt: Select[Any] = select(func.count()).select_from(Doubt).where(*conditions)
        total = int((await self._session.execute(count_stmt)).scalar_one())

        stmt = (
            select(Doubt)
            .where(*conditions)
            # Most recent activity first: the moderator queue is worked from the
            # top, and the student's own list wants their live threads first.
            .order_by(Doubt.last_activity_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        return rows, total

    async def replies_for(
        self, doubt_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[DoubtReply, User]]]:
        """Replies with their author, for a page of doubts, in one query."""
        if not doubt_ids:
            return {}
        stmt = (
            select(DoubtReply, User)
            # Outer join: a reply whose author has been deleted still belongs in the
            # thread. An inner join would silently drop it and change the story.
            .outerjoin(User, User.id == DoubtReply.user_id)
            .where(DoubtReply.doubt_id.in_(doubt_ids))
            .order_by(DoubtReply.created_at)
        )
        grouped: dict[uuid.UUID, list[tuple[DoubtReply, User]]] = {}
        for reply, author in (await self._session.execute(stmt)).all():
            grouped.setdefault(reply.doubt_id, []).append((reply, author))
        return grouped

    async def set_status(
        self,
        *,
        doubt: Doubt,
        status: str,
        resolved_by: uuid.UUID | None = None,
        resolution_note: str | None = None,
    ) -> Doubt:
        now = utcnow()
        doubt.status = status
        doubt.updated_at = now
        doubt.last_activity_at = now
        if status == "RESOLVED":
            doubt.resolved_at = now
            doubt.resolved_by = resolved_by
            doubt.resolution_note = resolution_note
        if status == "CLOSED":
            # Closing without answering is a legitimate student action, and it must
            # not look like a resolution to whoever reports on resolution rates.
            doubt.resolved_at = None
        await self._session.flush()
        return doubt

    async def accept_reply(self, *, doubt: Doubt, reply: DoubtReply, user_id: uuid.UUID) -> Doubt:
        """Mark one reply as the accepted answer, unmarking any previous one."""
        await self._session.execute(
            update(DoubtReply)
            .where(DoubtReply.doubt_id == doubt.id)
            .values(is_accepted=False, updated_at=utcnow())
        )
        reply.is_accepted = True
        reply.updated_at = utcnow()
        doubt.accepted_reply_id = reply.id
        doubt.status = "RESOLVED"
        doubt.resolved_at = utcnow()
        doubt.resolved_by = user_id
        doubt.updated_at = utcnow()
        await self._session.flush()
        return doubt

    async def reply(self, reply_id: uuid.UUID) -> DoubtReply | None:
        return (
            await self._session.execute(select(DoubtReply).where(DoubtReply.id == reply_id))
        ).scalar_one_or_none()

    async def counts(self, *, user_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(Doubt.status, func.count(Doubt.id))
            .where(Doubt.user_id == user_id)
            .group_by(Doubt.status)
        )
        counts = {status: int(count) for status, count in (await self._session.execute(stmt)).all()}
        return {
            "open": counts.get("OPEN", 0),
            "answered": counts.get("ANSWERED", 0),
            "resolved": counts.get("RESOLVED", 0),
            "closed": counts.get("CLOSED", 0),
            "total": sum(counts.values()),
        }
