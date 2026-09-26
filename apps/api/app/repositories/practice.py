"""Question bank reads.

EVERY QUERY HERE RETURNS PUBLISHED QUESTIONS ONLY, and that predicate is written
once, in ``_published()``, rather than at each call site. A draft question carries
no verified answer, so serving one leaks an unverified claim into a student's
practice - and the bug would be invisible, because the page would look fine.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, false, func, or_, select

from app.models.curriculum import Chapter, SubjectComponent
from app.models.progress import PracticeAttempt, UserQuestionProgress
from app.models.question import ExamSession, Question, QuestionOption


def _published() -> Any:
    """The only statuses a student may ever see."""
    return (Question.status == "PUBLISHED", Question.deleted_at.is_(None))


async def _subject_filter(session: Any, subject_id: uuid.UUID) -> Any:
    """A student filter is either a real paper or one Intermediate split.

    A split returns questions assigned to it, plus questions whose chapter is
    mapped to it and which have not been assigned elsewhere. An unclassified
    question on a combined paper does not appear in either split, and asking
    for the combined paper itself returns nothing: that id is not a student
    filter. Foundation and Final papers are unchanged.
    """
    component = (
        await session.execute(
            select(SubjectComponent).where(
                SubjectComponent.id == subject_id,
                SubjectComponent.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if component is not None:
        chapter_ids = select(Chapter.id).where(
            Chapter.subject_component_id == component.id,
            Chapter.deleted_at.is_(None),
        )
        return or_(
            Question.subject_component_id == component.id,
            and_(
                Question.subject_component_id.is_(None),
                Question.chapter_id.in_(chapter_ids),
            ),
        )
    has_splits = (
        await session.execute(
            select(func.count(SubjectComponent.id)).where(
                SubjectComponent.parent_subject_id == subject_id,
                SubjectComponent.is_active.is_(True),
            )
        )
    ).scalar_one()
    if has_splits:
        return false()
    return Question.subject_id == subject_id


def normalise_answer(value: str | None) -> str:
    """Fold an answer into a comparable form.

    Whitespace and case only. Deliberately NOT a semantic comparison: "1000" and
    "1,000" stay different, because a question that needs thousands-separator
    tolerance needs a proper numeric type, not a cleverer string fold that quietly
    marks wrong answers correct.
    """
    return (value or "").strip().upper()


def grade(question: Question, chosen: str | None) -> bool | None:
    """Is this answer correct?

    ``None`` means UNGRADEABLE, not wrong: descriptive answers and case studies
    that a human has to read. Returning False there would mark a student wrong for
    an answer nobody has marked.

    A SKIPPED OBJECTIVE QUESTION IS WRONG, NOT UNGRADEABLE. The distinction matters
    beyond wording: an ungraded attempt is excluded from the accuracy denominator
    and counted in ``pendingReview`` on the dashboard. Leaving a blank MCQ out of
    both is wrong twice over - nobody is ever going to review it, and a student who
    skips ten questions sees an accuracy built from the ones they chose to answer.
    Unanswered is worth zero marks in the exam, so it is worth zero here.
    """
    if question.question_type in {"DESCRIPTIVE", "CASE_STUDY"}:
        return None
    if question.correct_answer is None:
        # No key recorded: nothing can be compared, so a human has to look.
        return None
    if chosen is None:
        return False
    if question.question_type == "MSQ":
        # "A,C" and "C,A" are the same answer; the ORDER is the client's business.
        chosen_set = {p for p in normalise_answer(chosen).replace(" ", "").split(",") if p}
        correct_set = {
            p for p in normalise_answer(question.correct_answer).replace(" ", "").split(",") if p
        }
        return bool(chosen_set) and chosen_set == correct_set
    return normalise_answer(chosen) == normalise_answer(question.correct_answer)


class SqlQuestionRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def questions(
        self,
        *,
        course_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
        chapter_id: uuid.UUID | None = None,
        topic_id: uuid.UUID | None = None,
        difficulty: str | None = None,
        question_type: str | None = None,
        exclude_ids: list[uuid.UUID] | None = None,
        limit: int = 20,
    ) -> list[Question]:
        """A random slice of the bank matching the filters.

        RANDOM ON PURPOSE. Ordering by ``id`` would hand every student the same
        questions in the same order, so "the first 10 questions of Income Tax" would
        become the whole of what most students practise.

        ``ORDER BY random()`` is a full scan of the matching set, which is fine at
        syllabus scale (thousands of rows) and would not be at millions - recorded
        here so the future bottleneck is a known trade-off rather than a surprise.
        """
        stmt: Select[Any] = select(Question).where(*_published())
        if course_id is not None:
            stmt = stmt.where(Question.course_id == course_id)
        if subject_id is not None:
            stmt = stmt.where(await _subject_filter(self._session, subject_id))
        if chapter_id is not None:
            stmt = stmt.where(Question.chapter_id == chapter_id)
        if topic_id is not None:
            stmt = stmt.where(Question.topic_id == topic_id)
        if difficulty is not None:
            stmt = stmt.where(Question.difficulty == difficulty)
        if question_type is not None:
            stmt = stmt.where(Question.question_type == question_type)
        if exclude_ids:
            stmt = stmt.where(Question.id.notin_(exclude_ids))
        stmt = stmt.order_by(func.random()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def question(self, question_id: uuid.UUID) -> Question | None:
        stmt = select(Question).where(Question.id == question_id, *_published())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def questions_by_ids(self, question_ids: list[uuid.UUID]) -> list[Question]:
        """Order-preserving load, used to rebuild a mock paper from its stored ids."""
        if not question_ids:
            return []
        stmt = select(Question).where(Question.id.in_(question_ids), *_published())
        rows = list((await self._session.execute(stmt)).scalars().all())
        by_id = {row.id: row for row in rows}
        # A question that has since been archived disappears from the paper rather
        # than appearing without an answer.
        return [by_id[qid] for qid in question_ids if qid in by_id]

    async def detail(self, question_id: uuid.UUID, user_id: uuid.UUID) -> QuestionDetail | None:
        """Everything one question screen needs, in a fixed number of queries.

        Four reads, none of them per-row: the row, its options, the caller's
        progress row (which carries the bookmark flag) and their most recent
        attempt. This is a detail endpoint, so the cost is paid once per page and
        an N+1 is not hidden by pagination the way it would be in a list.

        Returns ``None`` for anything a student may not see - unpublished, archived
        or soft-deleted - because those three all have the same correct answer, and
        a route that distinguishes them tells an unauthorised caller which ids
        exist.
        """
        question = await self.question(question_id)
        if question is None:
            return None

        options = (await self.options_for([question_id])).get(question_id, [])

        progress = (
            await self._session.execute(
                select(UserQuestionProgress).where(
                    UserQuestionProgress.user_id == user_id,
                    UserQuestionProgress.question_id == question_id,
                )
            )
        ).scalar_one_or_none()

        attempt = (
            await self._session.execute(
                select(PracticeAttempt)
                .where(
                    PracticeAttempt.user_id == user_id,
                    PracticeAttempt.question_id == question_id,
                )
                .order_by(PracticeAttempt.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        session_row = None
        if question.attempt_id is not None:
            session_row = await self._session.get(ExamSession, question.attempt_id)

        return QuestionDetail(
            question=question,
            options=options,
            bookmarked=bool(progress and progress.is_marked_for_review),
            attempt=attempt,
            exam_session=session_row,
        )

    async def options_for(
        self, question_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[QuestionOption]]:
        """Options for a whole page of questions in ONE query.

        The models deliberately carry no ORM relationships, so there is no lazy
        load to trip over here - but there is also no eager load, and the N+1 would
        be as invisible as it is in any other codebase.
        """
        if not question_ids:
            return {}
        stmt = (
            select(QuestionOption)
            .where(QuestionOption.question_id.in_(question_ids))
            .order_by(QuestionOption.sequence, QuestionOption.label)
        )
        grouped: dict[uuid.UUID, list[QuestionOption]] = {}
        for option in (await self._session.execute(stmt)).scalars().all():
            grouped.setdefault(option.question_id, []).append(option)
        return grouped


@dataclass(slots=True)
class QuestionDetail:
    """One question, its options, and what the caller has already done with it.

    A dataclass rather than a tuple: the route reads four of these fields and a
    five-field tuple is where argument-order bugs live.
    """

    question: Question
    options: list[QuestionOption] = field(default_factory=list)
    bookmarked: bool = False
    attempt: PracticeAttempt | None = None
    exam_session: ExamSession | None = None

    @property
    def answered(self) -> bool:
        """Has this student ever answered it?

        THE GATE ON REVEALING THE ANSWER. Same rule as ``POST /practice/answers``:
        the answer exists in the response only once an attempt does. A student who
        has answered it has already paid for the explanation, and one who has not
        must not be able to read it out of the network tab.
        """
        return self.attempt is not None
