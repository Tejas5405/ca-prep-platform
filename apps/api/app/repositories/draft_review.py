"""SQLAlchemy persistence for draft review decisions.

THE TRANSACTION SHAPE HERE IS THE OPPOSITE OF THE INGESTION PIPELINE'S

``SqlIngestionSink`` deliberately commits each step separately, because a job
runs for minutes and holding a transaction open that long blocks unrelated
writes. It can get away with that because raw extractions are immutable and
re-insertion is guarded.

Promotion cannot. Three writes must be atomic:

    1. insert the question
    2. insert its options
    3. mark the draft APPROVED with promoted_question_id

Split them and every failure mode is a data-integrity bug that a human has to
untangle: a question nobody decided to create, or a decided draft whose content
was lost, or an editor told the approval succeeded while only half of it landed.

THE ROW LOCK

``promote`` re-reads the draft ``FOR UPDATE``. The service's own check cannot be
authoritative - two editors can have the same draft open, both see PENDING, and
both proceed. The lock serialises them, so the second re-read sees APPROVED and
gets a clear conflict instead of creating a duplicate question.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import Chapter, Course, Subject, Topic
from app.models.ingestion import IngestionDraft, IngestionJob
from app.models.question import Question, QuestionOption
from app.services.draft_review import (
    DraftAlreadyDecided,
    DraftNotFound,
    DraftState,
    DraftSummary,
    ResolvedPlacement,
    preview_of,
)
from app.services.publishing import (
    PUBLISHABLE_STATUSES,
    AlreadyPublished,
    Incomplete,
    NotPublishable,
    PublishableQuestion,
    PublishedResult,
)

logger = logging.getLogger(__name__)


class SqlDraftReviewStore:
    """Implements DraftReviewStore against PostgreSQL.

    Takes a session factory rather than a session because ``promote`` needs its
    own transaction boundary - see the module docstring.
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory

    def _factory(self) -> Any:
        if self._session_factory is None:
            from app.core.dependencies import get_engine, get_session_factory

            get_engine()
            self._session_factory = get_session_factory()
        return self._session_factory

    # ------------------------------------------------------------- read side

    async def load_draft(self, draft_id: uuid.UUID) -> DraftState | None:
        """Load the draft and the provenance of the job that produced it.

        Joined rather than lazily followed: the caller always needs the storage
        path (it becomes ``questions.source_pdf_path``), so a second round trip
        would be pure overhead.
        """
        async with self._factory()() as session:
            row = (
                await session.execute(
                    select(IngestionDraft, IngestionJob)
                    .join(IngestionJob, IngestionDraft.job_id == IngestionJob.id)
                    .where(IngestionDraft.id == draft_id)
                )
            ).first()

            if row is None:
                return None

            draft, job = row
            return DraftState(
                draft_id=draft.id,
                job_id=draft.job_id,
                text=draft.text,
                review_status=draft.review_status,
                source_page=draft.source_page,
                detection_confidence=_as_float(draft.detection_confidence),
                detected_marks=draft.detected_marks,
                detected_question_type=draft.detected_question_type,
                detected_year=draft.detected_year,
                storage_path=job.storage_path,
                bucket=job.bucket,
            )

    async def list_drafts(
        self,
        *,
        review_status: str = "PENDING",
        job_id: uuid.UUID | None = None,
        limit: int = 20,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> tuple[list[DraftSummary], int]:
        """The QA worklist.

        OLDEST FIRST. This is a queue of decisions with a human at the end, and a
        queue ordered newest-first starves: an editor works through the top of the
        list while the draft at the bottom is never reached. ``created_at`` is not
        unique - a single ingestion run writes dozens of drafts within a
        millisecond - so ``id`` breaks ties, which is what makes offset pagination
        stable rather than sometimes repeating or skipping a row.

        Indexed by ``idx_ingestion_drafts_review_queue (review_status,
        created_at)``, which is exactly this filter and sort.

        ``session`` is optional so a route can read inside the request's own
        session (see the note on ``SqlIngestionSink.list_jobs``); the worker passes
        nothing and gets a short transaction of its own.
        """
        filters = [IngestionDraft.review_status == review_status]
        if job_id is not None:
            filters.append(IngestionDraft.job_id == job_id)

        async def _run(active: AsyncSession) -> tuple[list[IngestionDraft], int]:
            total = await active.scalar(
                select(func.count()).select_from(IngestionDraft).where(*filters)
            )
            rows = (
                (
                    await active.execute(
                        select(IngestionDraft)
                        .where(*filters)
                        .order_by(IngestionDraft.created_at.asc(), IngestionDraft.id.asc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows), int(total or 0)

        if session is not None:
            plain_rows, total_count = await _run(session)
        else:
            async with self._factory()() as own:
                plain_rows, total_count = await _run(own)
        rows = plain_rows

        return [
            DraftSummary(
                draft_id=row.id,
                job_id=row.job_id,
                review_status=row.review_status,
                preview=preview_of(row.text),
                source_page=row.source_page,
                detected_year=row.detected_year,
                detected_attempt=row.detected_attempt,
                detected_marks=row.detected_marks,
                detected_question_type=row.detected_question_type,
                detection_confidence=_as_float(row.detection_confidence),
                created_at=row.created_at,
            )
            for row in rows
        ], total_count

    async def resolve_placement(
        self,
        subject_id: uuid.UUID,
        chapter_id: uuid.UUID | None,
        topic_id: uuid.UUID | None,
    ) -> ResolvedPlacement | None:
        """Validate placement and derive the course and syllabus scheme.

        Chapter and topic are checked to BELONG to the chosen subject. Skipping
        that check is easy and produces the worst kind of bug: a question filed
        under one subject while sitting in another subject's chapter. The
        question then appears in the wrong filter, and a student revising Group II
        Direct Tax is shown a Group I Auditing question with nothing looking
        wrong anywhere.
        """
        async with self._factory()() as session:
            course_id, scheme = await self._course_for_subject(session, subject_id)
            if course_id is None:
                return None

            if chapter_id is not None:
                chapter_subject = await session.scalar(
                    select(Chapter.subject_id).where(Chapter.id == chapter_id)
                )
                if chapter_subject is None or chapter_subject != subject_id:
                    return None

            if topic_id is not None:
                # A topic's subject is reached through its chapter, so this
                # checks the whole chain rather than the topic's own id.
                topic_chain = (
                    await session.execute(
                        select(Chapter.subject_id)
                        .join(Topic, Topic.chapter_id == Chapter.id)
                        .where(Topic.id == topic_id)
                    )
                ).first()
                if topic_chain is None or topic_chain[0] != subject_id:
                    return None

                # A topic supplied without its chapter: derive it from the topic
                # rather than rejecting, because the editor's intent is clear.
                if chapter_id is None:
                    chapter_id = await session.scalar(
                        select(Topic.chapter_id).where(Topic.id == topic_id)
                    )

            return ResolvedPlacement(
                course_id=course_id,
                chapter_id=chapter_id,
                syllabus_scheme=scheme,
            )

    @staticmethod
    async def _course_for_subject(session: Any, subject_id: uuid.UUID) -> tuple[Any, str]:
        """Return (course_id, syllabus_scheme) for a subject, or (None, '')."""
        row = (
            await session.execute(
                select(Course.id, Course.syllabus_scheme)
                .join(Subject, Subject.course_id == Course.id)
                .where(Subject.id == subject_id)
            )
        ).first()
        if row is None:
            return None, ""
        return row[0], row[1]

    # ------------------------------------------------------------ write side

    async def promote(
        self,
        draft_id: uuid.UUID,
        *,
        question: dict[str, Any],
        options: list[dict[str, Any]],
        review: dict[str, Any],
    ) -> uuid.UUID:
        """Atomically create the question and close the draft.

        See the module docstring: all three writes share one transaction.
        """
        async with self._factory()() as session:
            # FOR UPDATE: serialises concurrent approvals of the same draft.
            draft = await session.scalar(
                select(IngestionDraft).where(IngestionDraft.id == draft_id).with_for_update()
            )
            if draft is None:
                raise DraftNotFound(f"No ingestion draft with id {draft_id}")

            if draft.review_status != "PENDING":
                # A concurrent approval or rejection won the race. Raising rolls
                # the transaction back, so the question is not created.
                raise DraftAlreadyDecided(f"Draft {draft_id} was already {draft.review_status}")

            new_question = Question(id=uuid.uuid4(), **question)
            session.add(new_question)

            for option in options:
                session.add(QuestionOption(question_id=new_question.id, **option))

            draft.review_status = review["status"]
            draft.reviewed_by = review.get("reviewed_by")
            draft.reviewed_at = datetime.now(UTC)
            draft.review_note = review.get("review_note")
            # The provenance link. This is what lets a published question be
            # traced back to the extraction that produced it.
            draft.promoted_question_id = new_question.id

            await session.commit()
            return new_question.id

    async def record_decision(
        self,
        draft_id: uuid.UUID,
        *,
        status: str,
        review: dict[str, Any],
    ) -> None:
        """Record a rejection or duplicate marking.

        Also locked, for the same reason as promote: a rejection racing an
        approval must not leave a decided draft pointing at a question the editor
        just rejected.
        """
        async with self._factory()() as session:
            draft = await session.scalar(
                select(IngestionDraft).where(IngestionDraft.id == draft_id).with_for_update()
            )
            if draft is None:
                raise DraftNotFound(f"No ingestion draft with id {draft_id}")
            if draft.review_status != "PENDING":
                raise DraftAlreadyDecided(f"Draft {draft_id} was already {draft.review_status}")

            draft.review_status = status
            draft.reviewed_by = review.get("reviewed_by")
            draft.reviewed_at = datetime.now(UTC)
            draft.review_note = review.get("review_note")

            await session.commit()


def _as_float(value: Any) -> float | None:
    """NUMERIC arrives as Decimal; question.extraction_confidence is a float column."""
    return float(value) if value is not None else None


# ================================================================= publishing
#
# WHY THIS LIVES IN THE SAME STORE
#
# Publishing reads the same question row that promotion writes, and the two share
# the rule that matters: a published question must carry a verifier. Keeping them
# together means the invariant is enforced against one model, and a reviewer reading
# the review path finds the publish path next to it - which is how the "approve
# creates a DRAFT, then someone publishes" handoff stays visible.


class SqlPublishingStore:
    """Implements PublishingStore against PostgreSQL."""

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory

    def _factory(self) -> Any:
        if self._session_factory is None:
            from app.core.dependencies import get_engine, get_session_factory

            get_engine()
            self._session_factory = get_session_factory()
        return self._session_factory

    async def load_publishable_question(
        self, question_id: uuid.UUID, *, session: AsyncSession | None = None
    ) -> PublishableQuestion | None:
        """Load the row plus the option counts the decision needs.

        The counts are read in SQL rather than by loading every option: a
        four-option question loads four rows to answer two integers, and a
        mis-keyed question with a stray duplicate could load hundreds.

        ``session`` is optional for the same reason as the listings: a route reads
        inside the request's session, and a store that opens its own would answer
        from a different database than the request is configured against. That is
        not a hypothetical - it is the bug this whole family of methods had.
        """
        from sqlalchemy import case, func

        from app.models.question import Question, QuestionOption

        async def _run(active: AsyncSession):
            row = await active.get(Question, question_id)
            if row is None:
                return None
            counts = (
                await active.execute(
                    select(
                        func.count(QuestionOption.id),
                        func.count(case((QuestionOption.is_correct.is_(True), 1))),
                    ).where(QuestionOption.question_id == question_id)
                )
            ).one()
            return row, int(counts[0] or 0), int(counts[1] or 0)

        if session is not None:
            loaded = await _run(session)
        else:
            async with self._factory()() as own:
                loaded = await _run(own)
        if loaded is None:
            return None
        row, option_count, correct_count = loaded
        return PublishableQuestion(
            question_id=row.id,
            status=row.status,
            question_type=row.question_type,
            verified_by=row.verified_by,
            option_count=option_count,
            correct_option_count=correct_count,
        )

    async def publish_question(
        self,
        question_id: uuid.UUID,
        *,
        verifier_id: uuid.UUID,
        session: AsyncSession | None = None,
    ) -> PublishedResult:
        """Mark a question PUBLISHED and stamp the verifier, in one commit.

        ``FOR UPDATE``: two reviewers can have the same question open, and both can
        pass the pre-check a moment apart. The lock makes the second one re-read the
        published row and answer with a conflict rather than overwriting the first
        verifier's name.

        The lock only helps if both callers go through the same row - which is why
        the route passes the request's session rather than letting this open a
        second one. Two sessions would each hold their own transaction and the
        second would wait on a lock it can never see released by code it is running.
        """
        from app.models.question import Question

        async def _run(active: AsyncSession) -> PublishedResult:
            row = (
                await active.execute(
                    select(Question).where(Question.id == question_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise DraftNotFound(f"No question with id {question_id}")

            previous = row.status
            if previous == "PUBLISHED":
                raise AlreadyPublished(
                    published_by=str(row.verified_by) if row.verified_by else None
                )
            if previous not in PUBLISHABLE_STATUSES:
                raise NotPublishable(status=previous)

            row.status = "PUBLISHED"
            row.verified_by = verifier_id
            from app.services.question_history import record_version

            await record_version(
                active, row, actor_id=verifier_id, reason="Published by a verifier"
            )
            await active.commit()
            return PublishedResult(
                content_id=row.id,
                status=row.status,
                previous_status=previous,
                verified_by=verifier_id,
            )

        if session is not None:
            return await _run(session)
        async with self._factory()() as own:
            return await _run(own)

    async def publish_mock(
        self,
        mock_id: uuid.UUID,
        *,
        verifier_id: uuid.UUID,
        session: AsyncSession | None = None,
    ) -> PublishedResult | None:
        """Publish a paper, refusing one whose questions are not all live.

        A paper is only as good as its questions: opening one that references
        unpublished rows gives a student a paper that scores zero for reasons they
        cannot see, and gives the report a denominator that does not exist. So the
        question statuses are checked in SQL - a JSONB list of ids cannot be joined
        by the ORM's relationship machinery here, and the check must happen against
        the database rather than against the cached list on the row.
        """
        from app.models.progress import MockTest
        from app.models.question import Question

        async def _run(active: AsyncSession) -> PublishedResult | None:
            mock = (
                await active.execute(
                    select(MockTest).where(MockTest.id == mock_id).with_for_update()
                )
            ).scalar_one_or_none()
            if mock is None:
                return None

            previous = mock.status
            if previous == "PUBLISHED":
                raise AlreadyPublished()

            ids = [uuid.UUID(str(value)) for value in (mock.question_ids or [])]
            if not ids:
                raise Incomplete("this paper has no questions")

            published = (
                await active.execute(
                    select(func.count())
                    .select_from(Question)
                    .where(Question.id.in_(ids), Question.status == "PUBLISHED")
                )
            ).scalar_one()
            if int(published) != len(ids):
                raise Incomplete(
                    f"{len(ids) - int(published)} of {len(ids)} questions are not "
                    "published. A paper is only as good as its questions."
                )

            mock.status = "PUBLISHED"
            await active.commit()

            return PublishedResult(
                content_id=mock.id,
                status=mock.status,
                previous_status=previous,
                verified_by=verifier_id,
                question_count=len(ids),
            )

        if session is not None:
            return await _run(session)
        async with self._factory()() as own:
            return await _run(own)
