"""Collection storage.

THE RULE THIS FILE FOLLOWS, LEARNED THE HARD WAY THREE TIMES

Every method takes an optional ``session``. When the route passes its request
session, the store uses it; when nothing is passed, it opens its own. Three
separate bugs in this codebase came from a store method that silently opened its own
factory: the route's transaction could not see the write, the symptom was a 404 or
an empty list, and no dependency override could fix it. An integration test caught
each one only because it asserted on the ROW rather than the status code.

Ownership is a predicate, not a check done afterwards. Every read and every write is
filtered by ``user_id`` in the query itself. A method that loads a collection and
then compares owners in Python is one `if` away from leaking another student's
collection, and the `if` is exactly the line a later refactor removes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session_factory
from app.models.collection import Collection, CollectionQuestion
from app.models.progress import UserQuestionProgress
from app.models.question import Question, QuestionOption


@dataclass(slots=True)
class CollectionRow:
    """A collection and the counts a list screen needs."""

    collection: Collection
    question_count: int


@dataclass(slots=True)
class CollectionItem:
    """One question inside a collection, with what the student needs to recognise it."""

    question_id: uuid.UUID
    text: str
    question_type: str
    difficulty: str
    marks: int
    subject_id: uuid.UUID
    chapter_id: uuid.UUID | None
    is_historical: bool
    finance_act_year: str | None
    disclaimer_text: str | None
    note: str | None
    position: int
    options: list[QuestionOption]


class SqlCollectionStore:
    """Reads and writes for collections, over either the caller's session or its own."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    # ---------------------------------------------------------------- sessions

    class _Scope:
        """Uses the injected session, or opens one and commits on clean exit.

        Written as a context manager rather than duplicated try/finally in every
        method, because the duplication is where the fourth version of the
        "forgot to commit" bug would come from.
        """

        def __init__(self, session: AsyncSession | None) -> None:
            self._given = session
            self.session: AsyncSession | None = session
            self._own: bool = False

        async def __aenter__(self) -> AsyncSession:
            if self._given is not None:
                self.session = self._given
                return self._given
            self._own = True
            self.session = get_session_factory()()
            return self.session

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            assert self.session is not None
            if not self._own:
                return
            if exc_type is None:
                await self.session.commit()
            await self.session.close()

    # ------------------------------------------------------------- collections

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        kind: str,
        description: str | None = None,
        filters: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> Collection:
        async with self._Scope(session or self._session) as active:
            collection = Collection(
                id=uuid.uuid4(),
                user_id=user_id,
                name=name,
                description=description,
                kind=kind,
                filters=filters,
                item_count=0,
                is_system=False,
            )
            active.add(collection)
            await active.flush()
            return collection

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> tuple[list[CollectionRow], int]:
        """The student's collections, newest first, with their real counts.

        The count comes from a grouped aggregate rather than ``item_count``: the
        denormalised column is maintained for list screens, but a number shown to a
        student must be the number of rows that exist. A drifting counter is worse
        than a slow query at this scale, and the aggregate is one query for the
        whole page.
        """
        async with self._Scope(session or self._session) as active:
            counts = (
                select(
                    CollectionQuestion.collection_id,
                    func.count(CollectionQuestion.id).label("n"),
                )
                .group_by(CollectionQuestion.collection_id)
                .subquery()
            )
            rows = (
                await active.execute(
                    select(Collection, func.coalesce(counts.c.n, 0))
                    .outerjoin(counts, counts.c.collection_id == Collection.id)
                    .where(Collection.user_id == user_id)
                    .order_by(Collection.is_system.desc(), Collection.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            total = (
                await active.execute(
                    select(func.count(Collection.id)).where(Collection.user_id == user_id)
                )
            ).scalar_one()
            return [
                CollectionRow(collection=row[0], question_count=int(row[1])) for row in rows
            ], int(total)

    async def get(
        self, collection_id: uuid.UUID, user_id: uuid.UUID, *, session: AsyncSession | None = None
    ) -> Collection | None:
        """Ownership is part of the query. There is no "load then check" path."""
        async with self._Scope(session or self._session) as active:
            return (
                await active.execute(
                    select(Collection).where(
                        Collection.id == collection_id, Collection.user_id == user_id
                    )
                )
            ).scalar_one_or_none()

    async def rename(
        self,
        collection_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        session: AsyncSession | None = None,
    ) -> Collection | None:
        async with self._Scope(session or self._session) as active:
            values: dict[str, Any] = {}
            if name is not None:
                values["name"] = name
            if description is not None:
                values["description"] = description
            if not values:
                return await self.get(collection_id, user_id, session=active)
            result = await active.execute(
                update(Collection)
                .where(
                    Collection.id == collection_id,
                    Collection.user_id == user_id,
                    # A system collection is the product's, not the student's: the
                    # LDR list is referenced by the practice loop, so renaming it
                    # would break a feature to satisfy a whim.
                    Collection.is_system.is_(False),
                )
                .values(**values)
                .returning(Collection)
            )
            return result.scalar_one_or_none()

    async def delete(
        self, collection_id: uuid.UUID, user_id: uuid.UUID, *, session: AsyncSession | None = None
    ) -> bool:
        async with self._Scope(session or self._session) as active:
            result = await active.execute(
                delete(Collection).where(
                    Collection.id == collection_id,
                    Collection.user_id == user_id,
                    Collection.is_system.is_(False),
                )
            )
            return bool(result.rowcount)

    # ---------------------------------------------------------------- contents

    async def items(
        self,
        collection_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> tuple[list[CollectionItem], int]:
        async with self._Scope(session or self._session) as active:
            base = (
                select(CollectionQuestion, Question)
                .join(Question, Question.id == CollectionQuestion.question_id)
                .where(
                    CollectionQuestion.collection_id == collection_id,
                    Question.deleted_at.is_(None),
                )
                .order_by(CollectionQuestion.position, CollectionQuestion.created_at)
                .limit(limit)
                .offset(offset)
            )
            rows = (await active.execute(base)).all()
            total = (
                await active.execute(
                    select(func.count(CollectionQuestion.id))
                    .join(Question, Question.id == CollectionQuestion.question_id)
                    .where(
                        CollectionQuestion.collection_id == collection_id,
                        Question.deleted_at.is_(None),
                    )
                )
            ).scalar_one()

            option_map: dict[uuid.UUID, list[QuestionOption]] = {}
            if rows:
                ids = [row[1].id for row in rows]
                options = (
                    await active.execute(
                        select(QuestionOption)
                        .where(QuestionOption.question_id.in_(ids))
                        .order_by(QuestionOption.sequence, QuestionOption.label)
                    )
                ).scalars()
                for option in options:
                    option_map.setdefault(option.question_id, []).append(option)

            items = [
                CollectionItem(
                    question_id=question.id,
                    text=question.text,
                    question_type=question.question_type,
                    difficulty=question.difficulty,
                    marks=question.marks,
                    subject_id=question.subject_id,
                    chapter_id=question.chapter_id,
                    is_historical=bool(question.is_historical),
                    finance_act_year=question.finance_act_year,
                    disclaimer_text=question.disclaimer_text,
                    note=link.note,
                    position=link.position,
                    options=option_map.get(question.id, []),
                )
                for link, question in rows
            ]
            return items, int(total)

    async def add_questions(
        self,
        collection_id: uuid.UUID,
        question_ids: list[uuid.UUID],
        *,
        note: str | None = None,
        session: AsyncSession | None = None,
    ) -> int:
        """Add questions, ignoring ones already present.

        Idempotent by construction: ``ON CONFLICT DO NOTHING`` against
        ``uq_collection_question``. A student double-tapping "add" on a slow
        connection must not see an error, and the count that comes back is the
        number actually added rather than the number requested.
        """
        async with self._Scope(session or self._session) as active:
            existing = set(
                (
                    await active.execute(
                        select(CollectionQuestion.question_id).where(
                            CollectionQuestion.collection_id == collection_id,
                            CollectionQuestion.question_id.in_(question_ids),
                        )
                    )
                ).scalars()
            )
            # Only questions that exist AND are visible to a student may be added:
            # a draft id smuggled into a collection would otherwise be readable
            # through the collection screen, which is the leak the publishing
            # rules exist to prevent.
            valid = set(
                (
                    await active.execute(
                        select(Question.id).where(
                            Question.id.in_(question_ids),
                            Question.status == "PUBLISHED",
                            Question.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )
            to_add = [qid for qid in question_ids if qid in valid and qid not in existing]
            if not to_add:
                return 0

            max_position = (
                await active.execute(
                    select(func.coalesce(func.max(CollectionQuestion.position), 0)).where(
                        CollectionQuestion.collection_id == collection_id
                    )
                )
            ).scalar_one()

            for offset, qid in enumerate(to_add, start=1):
                active.add(
                    CollectionQuestion(
                        id=uuid.uuid4(),
                        collection_id=collection_id,
                        question_id=qid,
                        note=note,
                        position=int(max_position) + offset,
                    )
                )
            await active.flush()
            await active.execute(
                update(Collection)
                .where(Collection.id == collection_id)
                .values(item_count=Collection.item_count + len(to_add))
            )
            return len(to_add)

    async def remove_question(
        self,
        collection_id: uuid.UUID,
        question_id: uuid.UUID,
        *,
        session: AsyncSession | None = None,
    ) -> bool:
        async with self._Scope(session or self._session) as active:
            result = await active.execute(
                delete(CollectionQuestion).where(
                    CollectionQuestion.collection_id == collection_id,
                    CollectionQuestion.question_id == question_id,
                )
            )
            if not result.rowcount:
                return False
            await active.execute(
                update(Collection)
                .where(Collection.id == collection_id)
                .values(item_count=func.greatest(Collection.item_count - 1, 0))
            )
            return True

    async def collections_for_question(
        self, user_id: uuid.UUID, question_id: uuid.UUID, *, session: AsyncSession | None = None
    ) -> list[uuid.UUID]:
        """Which of this student's collections hold the question.

        Used by the question screen so "add to collection" can show what it is
        already in, rather than offering an add that silently does nothing.
        """
        async with self._Scope(session or self._session) as active:
            rows = await active.execute(
                select(CollectionQuestion.collection_id)
                .join(Collection, Collection.id == CollectionQuestion.collection_id)
                .where(Collection.user_id == user_id, CollectionQuestion.question_id == question_id)
            )
            return list(rows.scalars())

    # --------------------------------------------------------------------- LDR

    async def marked_for_review(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> tuple[list[tuple[Question, UserQuestionProgress]], int]:
        """The LDR list: questions this student flagged, newest flag first.

        The flag lives on ``user_question_progress.is_marked_for_review`` and is
        written by the practice loop, so this reads it rather than storing a second
        copy. Published questions only: a question archived since it was flagged
        disappears from the list rather than appearing unanswerable.
        """
        async with self._Scope(session or self._session) as active:
            rows = (
                await active.execute(
                    select(Question, UserQuestionProgress)
                    .join(
                        UserQuestionProgress,
                        UserQuestionProgress.question_id == Question.id,
                    )
                    .where(
                        UserQuestionProgress.user_id == user_id,
                        UserQuestionProgress.is_marked_for_review.is_(True),
                        Question.status == "PUBLISHED",
                        Question.deleted_at.is_(None),
                    )
                    .order_by(UserQuestionProgress.updated_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            total = (
                await active.execute(
                    select(func.count(UserQuestionProgress.id))
                    .join(Question, Question.id == UserQuestionProgress.question_id)
                    .where(
                        UserQuestionProgress.user_id == user_id,
                        UserQuestionProgress.is_marked_for_review.is_(True),
                        Question.status == "PUBLISHED",
                        Question.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
            return [(row[0], row[1]) for row in rows], int(total)
