"""SQLAlchemy persistence for the ingestion pipeline.

Implements the ``IngestionSink`` protocol from ``app.services.ingestion``. The
service decides what happens; this module only decides how it is stored.

TRANSACTION SHAPE, AND WHY IT IS NOT ONE BIG TRANSACTION

Each sink method opens its own short session and commits. A single transaction
spanning the whole job would be the instinctive choice, but an ingestion run can
take minutes - rasterising and OCR-ing forty scanned pages is not fast. Holding a
transaction open that long keeps a connection and row locks occupied for the
duration, which is how one large upload stalls unrelated writes.

The non-atomicity is deliberate and matches the pipeline's recovery model:

  * raw extractions are written FIRST and are immutable, so a crash during
    segmentation loses nothing expensive - the retry re-reads stored text
  * drafts are inserted only if the job has none, so a retry cannot duplicate
    an editor's review queue

That is why the service documents "raw text is persisted before anything else"
as a recovery requirement rather than an implementation detail.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import IngestionStage
from app.models.ingestion import IngestionDraft, IngestionJob, RawExtraction
from app.services.ingestion import JobState, JobSummary, content_hash

logger = logging.getLogger(__name__)


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Accept either form. Job ids cross the RQ boundary as strings."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


class SqlIngestionSink:
    """Database-backed sink.

    Takes a session FACTORY rather than a session, because each method needs its
    own short transaction. Passing a live session in would tempt callers into
    holding one open across the run.
    """

    def __init__(self, session_factory: Any = None) -> None:
        if isinstance(session_factory, AsyncSession):
            # The parameter used to be annotated `Any`, so `SqlIngestionSink(session)`
            # was accepted and then failed deep inside a request with
            # "TypeError: 'AsyncSession' object is not callable" - a message that names
            # neither the caller nor the fix. Two routes in the content library did
            # exactly this, which is why starting a pipeline run was a 500.
            raise TypeError(
                "SqlIngestionSink takes a session FACTORY, not a live session. "
                "Construct it as SqlIngestionSink() and pass the caller's session to "
                "the method: create_job(..., session=session)."
            )
        self._session_factory = session_factory

    def _factory(self) -> Any:
        if self._session_factory is None:
            # Resolved lazily so importing this module does not build an engine,
            # which would fail in tests and in any process without DATABASE_URL.
            from app.core.dependencies import get_engine, get_session_factory

            get_engine()
            self._session_factory = get_session_factory()
        return self._session_factory

    # ------------------------------------------------------------- read side

    async def load_job(self, job_id: str) -> JobState | None:
        try:
            key = _as_uuid(job_id)
        except ValueError:
            # A malformed id is a client error, not a crash. Returning None lets
            # the pipeline report job_not_found.
            logger.warning("Malformed ingestion job id: %r", job_id)
            return None

        async with self._factory()() as session:
            job = await session.get(IngestionJob, key)
            if job is None:
                return None
            return JobState(
                job_id=str(job.id),
                bucket=job.bucket,
                storage_path=job.storage_path,
                stage=job.stage,
                drafts_created=job.drafts_created,
            )

    async def list_jobs(
        self,
        *,
        stage: str | None = None,
        limit: int = 20,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> tuple[list[JobSummary], int]:
        """The operations queue: newest first, optionally filtered by stage.

        WHY THIS RETURNED AN EMPTY PAGE UNTIL NOW

        The route existed, the envelope was asserted by a contract test, and the
        query behind it was never written - so the endpoint answered 200 with no
        rows, which is indistinguishable from "no jobs have been uploaded" for
        every client and every test. An editor could therefore see nothing at all
        in a queue that had work in it. This is the query.

        NEWEST FIRST, unlike the drafts worklist. The two lists answer different
        questions: "what did I just upload and is it still running" is asked of
        jobs, and the oldest-first rule belongs to a review queue where starvation
        is the risk. Ordering both the same way would get one of them wrong.

        ``total`` is counted separately rather than derived from ``len(rows)``,
        because a page is not a result set - deriving it is how ``hasMore`` starts
        lying on the last page.

        ``session`` is optional because this method has two callers with genuinely
        different lifetimes. A route should read inside the REQUEST's session: it
        already has one open, and opening a second means the listing can be served
        from a different database than the request is configured for - which is not
        a hypothetical, it is how this method first went wrong. The worker has no
        request and passes nothing, and gets a short transaction of its own.
        """
        filters = []
        if stage:
            filters.append(IngestionJob.stage == stage)

        async def _run(active: AsyncSession) -> tuple[list[JobSummary], int]:
            total = await active.scalar(
                select(func.count()).select_from(IngestionJob).where(*filters)
            )
            rows = (
                (
                    await active.execute(
                        select(IngestionJob)
                        .where(*filters)
                        .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return [_job_summary(row) for row in rows], int(total or 0)

        if session is not None:
            return await _run(session)
        async with self._factory()() as own:
            return await _run(own)

    # ------------------------------------------------------------ write side

    async def create_job(
        self,
        *,
        bucket: str,
        storage_path: str,
        uploaded_by: uuid.UUID | None = None,
        course_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Create the job row at QUEUED and return its id.

        WHY A ROW EXISTS BEFORE THE BYTES DO

        The upload flow mints a signed URL and then the browser PUTs the file
        straight to storage; the API never sees the bytes. Until this method
        existed, nothing created the row - so `POST /ingestion/jobs/{id}/start` had
        no id that could ever refer to a real job, and the whole pipeline was
        unreachable from a client. The documented flow (upload, PUT, start, poll)
        could not be executed at all.

        Writing the row FIRST also means a failed upload leaves evidence. A job
        that never progresses past QUEUED is visible in the operations queue and can
        be retried; an upload with no row is invisible, and presents later as "the
        paper never appeared".

        ``stage_history`` is seeded with the initial stage rather than left empty,
        because the history is what answers "how long has it been sitting here" -
        and a job with no history looks like a job whose clock never started.
        """
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        job = IngestionJob(
            bucket=bucket,
            storage_path=storage_path,
            uploaded_by=uploaded_by,
            course_id=course_id,
            subject_id=subject_id,
            stage=IngestionStage.QUEUED.value,
            stage_history=[{"stage": IngestionStage.QUEUED.value, "at": now.isoformat()}],
            drafts_created=0,
            needs_manual_review=False,
        )

        async def _run(active: AsyncSession) -> str:
            active.add(job)
            await active.flush()
            job_id = str(job.id)
            await active.commit()
            return job_id

        if session is not None:
            return await _run(session)
        async with self._factory()() as own:
            return await _run(own)

    async def open_job(
        self,
        *,
        bucket: str,
        storage_path: str,
        uploaded_by: uuid.UUID | None,
        course_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """The job for this OBJECT, re-opened if one already exists.

        ``uq_ingestion_job_object`` allows exactly one ingestion job per
        ``(bucket, storage_path)``, and that is the right model: the job describes a run
        over one stored object, and two rows for one object are two histories that can
        disagree.

        ``create_job`` did not honour it, and the consequence was a 500 on the most
        ordinary admin action there is. The pipeline reports a FAILED document; the
        operator presses Re-process; a second row is inserted; the retry hits the unique
        constraint and the request dies carrying a PostgreSQL message. Pressing that
        button a second time is precisely what the button is for, so this broke exactly
        when it was needed - and the live sweep only reproduces it against a document
        that already has a job, which is why a fresh database looked clean.

        Re-opening RESETS the run: stage back to QUEUED, the previous failure cleared,
        and a new entry in the append-only history. The history is never rewritten, so
        the admin can still see that this object failed once before - which is the one
        fact they need in order to decide whether to press the button at all.
        """
        from datetime import UTC, datetime

        async def _run(active: AsyncSession) -> str:
            existing = (
                await active.execute(
                    select(IngestionJob).where(
                        IngestionJob.bucket == bucket,
                        IngestionJob.storage_path == storage_path,
                    )
                )
            ).scalar_one_or_none()

            now = datetime.now(UTC)
            if existing is None:
                job = IngestionJob(
                    bucket=bucket,
                    storage_path=storage_path,
                    uploaded_by=uploaded_by,
                    course_id=course_id,
                    subject_id=subject_id,
                    stage=IngestionStage.QUEUED.value,
                    stage_history=[{"stage": IngestionStage.QUEUED.value, "at": now.isoformat()}],
                    drafts_created=0,
                    needs_manual_review=False,
                )
                active.add(job)
                await active.flush()
                job_id = str(job.id)
            else:
                job_id = str(existing.id)
                existing.stage = IngestionStage.QUEUED.value
                existing.error_reason = None
                existing.started_at = None
                existing.finished_at = None
                existing.needs_manual_review = False
                # Placement is refreshed only when the caller supplied one: re-processing
                # must not silently un-file a document that has since been categorised.
                if course_id is not None:
                    existing.course_id = course_id
                if subject_id is not None:
                    existing.subject_id = subject_id
                history = list(existing.stage_history or [])
                history.append({"stage": IngestionStage.QUEUED.value, "at": now.isoformat()})
                existing.stage_history = history
            await active.commit()
            return job_id

        if session is not None:
            return await _run(session)
        async with self._factory()() as own:
            return await _run(own)

    async def record_stage(
        self, job_id: str, stage: IngestionStage, *, detail: str | None = None
    ) -> None:
        """Append to the stage log and move the current stage forward.

        The history is append-only and read-modify-write on a JSONB column. That
        is safe here because exactly one worker processes a job at a time
        (enforced by the skip-if-terminal guard in the pipeline); a second
        concurrent writer would be a bug worth surfacing rather than papering
        over with locking.
        """
        entry = {
            "stage": stage.value,
            "at": datetime.now(UTC).isoformat(),
        }
        if detail:
            entry["detail"] = detail

        async with self._factory()() as session:
            job = await session.get(IngestionJob, _as_uuid(job_id))
            if job is None:
                return
            job.stage = stage.value
            # Reassign rather than mutate in place: SQLAlchemy does not detect
            # in-place mutation of a JSONB list, so appending would be silently
            # discarded on commit.
            job.stage_history = [*list(job.stage_history or []), entry]
            if stage is IngestionStage.DOWNLOADING and job.started_at is None:
                job.started_at = datetime.now(UTC)
            if stage in (IngestionStage.AWAITING_QA, IngestionStage.FAILED):
                job.finished_at = datetime.now(UTC)
            await session.commit()

    async def save_raw_pages(self, job_id: str, pages: list[Any]) -> dict[int, str]:
        """Persist immutable raw text. Returns {page_number: extraction_id}.

        Uses ON CONFLICT DO NOTHING so re-running a job after a later-stage
        failure is cheap and cannot overwrite the original extraction - which is
        the whole point of keeping raw text separate (§11.4).
        """
        key = _as_uuid(job_id)
        mapping: dict[int, str] = {}

        async with self._factory()() as session:
            for page in pages:
                stmt = (
                    pg_insert(RawExtraction)
                    .values(
                        id=uuid.uuid4(),
                        job_id=key,
                        page_number=page.page_number,
                        raw_text=page.text,
                        tier=page.tier.value,
                        confidence=page.confidence,
                        char_count=page.char_count,
                        content_hash=content_hash(page.text),
                    )
                    .on_conflict_do_nothing(constraint="uq_raw_extraction_page")
                )
                await session.execute(stmt)

            await session.commit()

            # Read back regardless of whether the insert happened, so a retry
            # still gets the ids of the rows written by the first attempt.
            result = await session.execute(
                select(RawExtraction.page_number, RawExtraction.id).where(
                    RawExtraction.job_id == key
                )
            )
            for page_number, extraction_id in result.all():
                mapping[int(page_number)] = str(extraction_id)

        return mapping

    async def save_drafts(self, job_id: str, drafts: list[dict[str, Any]]) -> int:
        """Insert candidate questions, once.

        Guarded on "this job has no drafts yet". A retry after a failure between
        draft insertion and job completion must not double an editor's queue with
        duplicates of questions already reviewed.
        """
        key = _as_uuid(job_id)

        async with self._factory()() as session:
            existing = await session.scalar(
                select(func.count()).select_from(IngestionDraft).where(IngestionDraft.job_id == key)
            )
            if existing:
                logger.info(
                    "Job %s already has %s drafts; not inserting duplicates",
                    job_id,
                    existing,
                )
                return int(existing)

            for draft in drafts:
                session.add(
                    IngestionDraft(
                        job_id=key,
                        raw_extraction_id=_optional_uuid(draft.get("raw_extraction_id")),
                        text=draft["text"],
                        source_page=draft.get("source_page"),
                        detected_year=draft.get("detected_year"),
                        detected_attempt=draft.get("detected_attempt"),
                        detected_marks=draft.get("detected_marks"),
                        detected_question_type=draft.get("detected_question_type"),
                        detection_confidence=draft.get("detection_confidence"),
                        review_status="PENDING",
                    )
                )
            await session.commit()
            return len(drafts)

    async def complete(
        self,
        job_id: str,
        *,
        tier: int,
        page_count: int,
        mean_confidence: float,
        drafts_created: int,
        needs_manual_review: bool,
    ) -> None:
        async with self._factory()() as session:
            job = await session.get(IngestionJob, _as_uuid(job_id))
            if job is None:
                return
            job.extraction_tier = tier
            job.page_count = page_count
            job.mean_confidence = mean_confidence
            job.drafts_created = drafts_created
            job.needs_manual_review = needs_manual_review
            job.finished_at = datetime.now(UTC)
            await session.commit()

    async def fail(self, job_id: str, reason: str) -> None:
        """Record a failure reason.

        This must never raise. If recording the failure fails too, the job still
        needs to be marked failed - swallowing the secondary error would leave
        the job stuck in a non-terminal stage and it would be retried forever.
        """
        try:
            async with self._factory()() as session:
                job = await session.get(IngestionJob, _as_uuid(job_id))
                if job is None:
                    return
                job.stage = IngestionStage.FAILED.value
                job.error_reason = reason[:4000]
                job.finished_at = datetime.now(UTC)
                job.stage_history = [
                    *list(job.stage_history or []),
                    {
                        "stage": IngestionStage.FAILED.value,
                        "at": datetime.now(UTC).isoformat(),
                        "detail": reason[:500],
                    },
                ]
                await session.commit()
        except Exception:
            logger.exception("Could not record failure for job %s", job_id)


def _job_summary(job: IngestionJob) -> JobSummary:
    """Map an ORM row to the read model.

    Explicit rather than automatic: ``JobSummary`` is a wire contract, and
    ``from_orm``-style mapping would start exposing any column added to the model
    later, including columns that should not leave the database.
    """
    return JobSummary(
        job_id=str(job.id),
        bucket=job.bucket,
        storage_path=job.storage_path,
        stage=job.stage,
        drafts_created=job.drafts_created,
        page_count=job.page_count,
        extraction_tier=job.extraction_tier,
        needs_manual_review=job.needs_manual_review,
        error_reason=job.error_reason,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _optional_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return _as_uuid(str(value))
    except ValueError:
        # The pipeline supplies ids it just received from save_raw_pages, so a
        # malformed value means an internal bug rather than bad input. Dropping
        # the link is preferable to failing the whole job.
        logger.warning("Ignoring malformed raw_extraction_id: %r", value)
        return None
