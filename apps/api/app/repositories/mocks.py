"""Mock paper reads.

Only PUBLISHED papers are visible here, for the same reason only PUBLISHED
questions are: a draft paper has no verified answer key, and a student who takes
one gets scored against questions nobody has checked.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.models.progress import MockAttempt, MockTest
from app.models.question import Question


class SqlMockRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def list_published(
        self,
        *,
        course_id: uuid.UUID | None = None,
        kind: str | None = None,
        level: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[MockTest], int]:
        conditions: list[Any] = [MockTest.status == "PUBLISHED"]
        if course_id is not None:
            conditions.append(MockTest.course_id == course_id)
        if kind is not None:
            conditions.append(MockTest.kind == kind)
        if level is not None:
            from app.models.curriculum import Course

            conditions.append(
                MockTest.course_id.in_(select(Course.id).where(Course.level == level))
            )

        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(MockTest).where(*conditions)
                )
            ).scalar_one()
        )

        stmt = (
            select(MockTest)
            .where(*conditions)
            # Full-length papers last: a student scrolling for something to do in an
            # hour wants the chapter practice first, and a student looking for the
            # real thing knows they are looking for it.
            .order_by(MockTest.is_premium, MockTest.kind.desc(), MockTest.title)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        return rows, total

    async def published(self, mock_id: uuid.UUID) -> MockTest | None:
        stmt = select(MockTest).where(MockTest.id == mock_id, MockTest.status == "PUBLISHED")
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def questions_for(self, mock: MockTest) -> list[Question]:
        """The paper's questions, in the order stored on the paper.

        ``question_ids`` is a JSONB array, so the join happens here rather than in
        SQL - and a question deleted since the paper was published disappears from
        the paper instead of appearing with no options.
        """
        ids = [uuid.UUID(str(value)) for value in (mock.question_ids or [])]
        if not ids:
            return []
        rows = await self._session.execute(
            select(Question).where(
                Question.id.in_(ids),
                Question.status == "PUBLISHED",
                Question.deleted_at.is_(None),
            )
        )
        by_id = {question.id: question for question in rows.scalars().all()}
        return [by_id[question_id] for question_id in ids if question_id in by_id]

    async def in_progress_attempt(
        self, *, user_id: uuid.UUID, mock_test_id: uuid.UUID
    ) -> MockAttempt | None:
        """The partial unique index allows at most one of these per user and paper."""
        stmt = select(MockAttempt).where(
            MockAttempt.user_id == user_id,
            MockAttempt.mock_test_id == mock_test_id,
            MockAttempt.status == "IN_PROGRESS",
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def start_attempt(
        self,
        *,
        user_id: uuid.UUID,
        mock: MockTest,
        started_at: Any,
        expires_at: Any,
    ) -> MockAttempt:
        """Open an attempt, or resume the one already open.

        RESUMING rather than inserting: the database has a partial unique index on
        (user_id, mock_test_id) WHERE status = 'IN_PROGRESS', so a second insert
        would raise. A student who refreshes the page must get their attempt back,
        not the opportunity to burn the timer twice.
        """
        existing = await self.in_progress_attempt(user_id=user_id, mock_test_id=mock.id)
        if existing is not None:
            return existing

        attempt = MockAttempt(
            user_id=user_id,
            mock_test_id=mock.id,
            status="IN_PROGRESS",
            started_at=started_at,
            expires_at=expires_at,
            auto_submitted=False,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def attempt_for_user(
        self, *, attempt_id: uuid.UUID, user_id: uuid.UUID
    ) -> MockAttempt | None:
        """Scoped by user in the WHERE clause: an attempt id is not a capability."""
        stmt = select(MockAttempt).where(
            MockAttempt.id == attempt_id, MockAttempt.user_id == user_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def history(
        self, *, user_id: uuid.UUID, limit: int = 20
    ) -> list[tuple[MockAttempt, MockTest]]:
        stmt = (
            select(MockAttempt, MockTest)
            .join(MockTest, MockTest.id == MockAttempt.mock_test_id)
            .where(MockAttempt.user_id == user_id)
            .order_by(MockAttempt.created_at.desc())
            .limit(limit)
        )
        return [(row[0], row[1]) for row in (await self._session.execute(stmt)).all()]
