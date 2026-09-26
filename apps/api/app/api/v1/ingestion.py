"""Ingestion and QA endpoints - blueprint v3 §11.1.

The flow, and why it is split into two requests:

    1. POST /ingestion/uploads
         validate metadata -> mint a SHORT-LIVED signed upload URL
         -> create the job row in QUEUED (nothing queued yet)

    2. client PUTs the bytes directly to Supabase

    3. POST /ingestion/jobs/{id}/start
         confirm the object exists -> enqueue the RQ job

The split exists because the upload happens BETWEEN the two calls. The API never
sees the file, which is the point: a 50 MB scanned paper would otherwise hit
Render's request-size limit and occupy a worker for the whole transfer.

WHY /start VERIFIES THE OBJECT

Without a check, a client could call /start without ever uploading and the worker
would fail minutes later on a download error that looks like a storage outage.
Confirming existence first turns a confusing async failure into an immediate 409.

WHY ROLE CHECKS ARE ON EVERY ROUTE

The backend holds the Supabase service-role key and Supabase Storage RLS cannot
evaluate Supabase tokens, so these checks are the only authorization
boundary. Uploading source papers is editorial work, not student work.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import Permission, has_permission, require_permission
from app.integrations.supabase_storage import (
    BUCKETS,
    PREFIXES,
    StorageError,
    SupabaseStorage,
    build_object_path,
    validate_upload,
)
from app.models.enums import IngestionStage
from app.models.user import User
from app.repositories.draft_review import SqlPublishingStore
from app.repositories.ingestion import SqlIngestionSink
from app.schemas.base import StrictRequest, UuidRef
from app.services.draft_review import (
    ALL_REVIEW_STATUSES as DRAFT_STATUSES,
)
from app.services.draft_review import (
    DraftAlreadyDecided,
    DraftNotFound,
    OptionInput,
    PlacementNotFound,
    PromotionInput,
    PromotionValidationError,
    promote_draft,
    reject_draft,
)
from app.services.publishing import (
    AlreadyPublished,
    Incomplete,
    NotPublishable,
    check_publishable,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ingestion"])


# ================================================================ schemas


class CreateUploadIn(StrictRequest):
    """Request a signed upload URL.

    ``content_type`` and ``size_bytes`` are declared by the client and validated
    before a URL is minted. A declared size is not a guarantee - Supabase enforces
    the real limit at upload time - but it stops the obviously-wrong cases without
    a round trip.
    """

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(ge=1, le=200 * 1024 * 1024)
    course_id: UuidRef | None = None
    subject_id: UuidRef | None = None

    @property
    def target_bucket(self) -> str:
        return BUCKETS["questions"]


class OptionIn(StrictRequest):
    label: str = Field(min_length=1, max_length=4)
    text: str = Field(min_length=1, max_length=2000)
    is_correct: bool = False


class ReviewDraftIn(StrictRequest):
    """Approve or reject a draft question.

    Type and marks are REQUIRED on approval, not taken from the extraction's
    ``detected_*`` values. Those are regex reads of OCR text, and ``marks`` drives
    scoring: a wrong value marks every student who attempts the question wrong,
    silently and permanently. The UI prefills both from the detection, so
    confirming them is one click - and what gets stored is what a human confirmed.
    """

    decision: str = Field(pattern="^(APPROVE|REJECT|DUPLICATE)$")

    # ---- required to approve -------------------------------------------
    subject_id: UuidRef | None = None
    question_type: str | None = None
    marks: int | None = Field(default=None, ge=1, le=100)
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None
    year: int | None = Field(default=None, ge=1990, le=2100)
    attempt_id: UuidRef | None = None

    # ---- content -------------------------------------------------------
    difficulty: str = "MEDIUM"
    negative_marks: float = Field(default=0.0, ge=0)
    correct_answer: str | None = Field(default=None, max_length=500)
    model_answer: str | None = Field(default=None, max_length=10_000)
    explanation: str | None = Field(default=None, max_length=10_000)
    options: list[OptionIn] = Field(default_factory=list)

    # ---- taxation currency ---------------------------------------------
    is_historical: bool = False
    finance_act_year: str | None = Field(default=None, max_length=20)
    disclaimer_text: str | None = Field(default=None, max_length=2000)

    note: str | None = Field(default=None, max_length=1000)

    def to_promotion_input(self) -> PromotionInput:
        """Convert to the service's value type.

        Required-for-approval fields are asserted rather than coerced. The route
        checks them first and returns a 422 listing what is missing, so reaching
        this method with a None means the route's guard was bypassed - a bug worth
        an exception, not a silently-defaulted question.
        """
        assert self.subject_id is not None
        assert self.question_type is not None
        assert self.marks is not None
        return PromotionInput(
            subject_id=self.subject_id,
            question_type=self.question_type,
            marks=self.marks,
            chapter_id=self.chapter_id,
            topic_id=self.topic_id,
            difficulty=self.difficulty,
            negative_marks=self.negative_marks,
            correct_answer=self.correct_answer,
            model_answer=self.model_answer,
            explanation=self.explanation,
            options=tuple(
                OptionInput(label=o.label, text=o.text, is_correct=o.is_correct)
                for o in self.options
            ),
            year=self.year,
            attempt_id=self.attempt_id,
            is_historical=self.is_historical,
            finance_act_year=self.finance_act_year,
            disclaimer_text=self.disclaimer_text,
            note=self.note,
        )


# ============================================================== upload flow


@router.post(
    "/ingestion/uploads",
    status_code=status.HTTP_201_CREATED,
    summary="Request a signed upload URL for a source PDF",
)
async def create_upload(
    payload: CreateUploadIn,
    session: AsyncSession = Depends(get_db),
    caller: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
    settings: Settings = Depends(get_settings),
):
    try:
        validate_upload(
            filename=payload.filename,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
        )
    except StorageError as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unsupported upload",
            detail=str(exc),
            type_slug="upload",
        )

    # The path is SERVER-GENERATED. Accepting a client-supplied path would let
    # an editor overwrite another user's object, because a signed URL is scoped
    # to a path and nothing else.
    path = build_object_path(
        owner_id=str(caller.auth_user_id),
        filename=payload.filename,
        prefix=PREFIXES["originals"],
    )

    try:
        storage = SupabaseStorage(settings)
        signed = await storage.create_signed_upload_url(BUCKETS["questions"], path)
    except StorageError as exc:
        logger.error("Could not mint upload URL: %s", exc)
        return problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Storage unavailable",
            detail="Could not create an upload URL. Try again shortly.",
            type_slug="storage",
        )

    # THE JOB ROW IS CREATED HERE, NOT AFTER THE UPLOAD.
    #
    # Blueprint v3 §7.3 lists `POST /ingestion/jobs` as a separate call, and it was
    # never implemented - which meant no client could obtain a job id, so
    # `POST /ingestion/jobs/{id}/start` could only ever answer 404 and the pipeline
    # was unreachable end to end. Creating the row with the signed URL is one round
    # trip fewer and strictly better behaved: an upload that fails leaves a QUEUED
    # job that is visible and retryable, where a separate call leaves nothing at all
    # to notice.
    #
    # The response is returned even if the insert fails, because a 500 here would
    # throw away a URL the caller can still use - they would just have no job to
    # start. That is reported as `jobCreated: false` instead.
    job_id: str | None = None
    try:
        # ``open_job`` rather than ``create_job``: one job per stored object
        # (``uq_ingestion_job_object``). If the same object path is submitted twice -
        # a retry after a failed upload, which is the normal case for a flaky
        # connection - the second call re-opens the run instead of raising
        # UniqueViolation and silently reporting ``jobCreated: false``.
        job_id = await SqlIngestionSink().open_job(
            bucket=BUCKETS["questions"],
            storage_path=path,
            uploaded_by=caller.id,
            course_id=payload.course_id,
            subject_id=payload.subject_id,
            session=session,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Upload URL minted but job row failed for %s: %s", path, exc)

    return success(
        {
            "bucket": BUCKETS["questions"],
            "path": path,
            "uploadUrl": signed["uploadUrl"],
            "token": signed.get("token"),
            "expiresInSeconds": 300,
            "courseId": str(payload.course_id) if payload.course_id else None,
            "subjectId": str(payload.subject_id) if payload.subject_id else None,
            # The id the caller passes to `POST /ingestion/jobs/{jobId}/start`.
            "jobId": job_id,
            "jobCreated": job_id is not None,
        },
        request_id=get_request_id(),
    )


@router.post(
    "/ingestion/jobs/{job_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirm the upload landed and queue extraction",
)
async def start_job(
    job_id: str,
    _caller: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
    settings: Settings = Depends(get_settings),
):
    """Confirm and enqueue.

    Returns 202 rather than 200: the work has been accepted, not completed. The
    caller polls the job status endpoint.
    """
    job = await SqlIngestionSink().load_job(job_id)
    if job is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Ingestion job not found",
            detail=f"No ingestion job with id {job_id}",
            type_slug="not-found",
        )

    try:
        storage = SupabaseStorage(settings)
        present = await storage.exists(job.bucket, job.storage_path)
    except StorageError as exc:
        logger.error("Storage check failed for job %s: %s", job_id, exc)
        return problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Storage unavailable",
            detail="Could not confirm the upload. Try again shortly.",
            type_slug="storage",
        )

    if not present:
        # Fail here rather than letting the worker discover it minutes later,
        # where it presents as a storage outage instead of a missing upload.
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Upload not found",
            detail="The file has not been uploaded to the expected path yet.",
            type_slug="conflict",
        )

    try:
        from app.workers.rq_worker import enqueue_ingestion

        enqueue_ingestion(job_id, job.storage_path, job.bucket)
    except Exception as exc:  # noqa: BLE001
        # Redis being unavailable must not silently drop the job. The row stays
        # in QUEUED and can be re-queued, which is recoverable; a 200 here would
        # leave an upload that is never processed and never reported.
        logger.error("Could not enqueue job %s: %s", job_id, exc)
        return problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Queue unavailable",
            detail="The job was saved but could not be queued. Retry shortly.",
            type_slug="queue",
        )

    return success(
        {"jobId": job_id, "stage": "QUEUED", "status": "accepted"},
        request_id=get_request_id(),
    )


# =========================================================== job monitoring


#: Stages accepted by the queue filter. Validated against the enum rather than
#: passed through, so a typo returns 422 with the allowed list instead of a page
#: that is empty for a reason the caller cannot see.
JOB_STAGES = {stage.value for stage in IngestionStage}


@router.get("/ingestion/jobs", summary="List ingestion jobs")
async def list_jobs(
    stage: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _caller: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
):
    """The operations queue, newest first.

    Answers the two questions an editor has after uploading a paper: did the
    upload land, and is it still running. ``isTerminal`` is per job rather than
    per page so a polling client can stop on the job it cares about.
    """
    if stage is not None and stage.upper() not in JOB_STAGES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown stage",
            detail=f"stage must be one of {sorted(JOB_STAGES)}.",
            type_slug="validation",
            errors=[{"field": "stage", "message": "unknown ingestion stage"}],
        )

    from app.repositories.ingestion import SqlIngestionSink

    # The REQUEST's session, so the listing cannot be served from a different
    # database than the one this request is configured against.
    rows, total = await SqlIngestionSink().list_jobs(
        stage=stage.upper() if stage else None,
        limit=limit,
        offset=(page - 1) * limit,
        session=session,
    )

    return paginated(
        [
            {
                "jobId": row.job_id,
                "stage": row.stage,
                "bucket": row.bucket,
                "storagePath": row.storage_path,
                "draftsCreated": row.drafts_created,
                "pageCount": row.page_count,
                "extractionTier": row.extraction_tier,
                "needsManualReview": row.needs_manual_review,
                # The reason a job failed, surfaced in the list rather than only
                # in the detail view: an operator scanning forty jobs needs to see
                # WHICH ones failed, not open each one to find out.
                "errorReason": row.error_reason,
                "isTerminal": row.is_settled,
                "createdAt": row.created_at,
                "startedAt": row.started_at,
                "finishedAt": row.finished_at,
            }
            for row in rows
        ],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/ingestion/jobs/{job_id}", summary="Ingestion job status")
async def get_job(
    job_id: str,
    _caller: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
):
    """Job status, including the stage history.

    The history is exposed because it answers the question an editor actually
    has when something is slow: which stage is it sitting in, and how long has it
    been there.
    """
    from app.repositories.ingestion import SqlIngestionSink

    sink = SqlIngestionSink()
    try:
        key = uuid.UUID(job_id)
    except ValueError:
        return problem(
            status=status.HTTP_400_BAD_REQUEST,
            title="Invalid job id",
            detail="Job ids are UUIDs.",
            type_slug="validation",
        )

    job = await sink.load_job(str(key))
    if job is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Ingestion job not found",
            detail=f"No ingestion job with id {job_id}",
            type_slug="not-found",
        )

    return success(
        {
            "jobId": job.job_id,
            "stage": job.stage,
            "bucket": job.bucket,
            "storagePath": job.storage_path,
            "draftsCreated": job.drafts_created,
            # Poll-able: a client should back off once the job is terminal.
            "isTerminal": job.stage in {"AWAITING_QA", "PUBLISHED", "REJECTED", "FAILED"},
        },
        request_id=get_request_id(),
    )


# ============================================================== QA worklist


@router.get("/ingestion/drafts", summary="Draft questions awaiting review")
async def list_drafts(
    review_status: str = Query(default="PENDING"),
    job_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _caller: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
):
    """QA worklist, oldest first.

    Only editors and above can read drafts. Nothing student-facing reads the
    drafts table at all, which is what makes an unreviewed OCR artifact
    structurally unable to reach a student.

    Each row carries a truncated preview rather than the full draft text: this is
    a queue of decisions, and shipping every candidate question in full makes the
    payload grow with the backlog. The editor opens one draft to read it.
    """
    if review_status.upper() not in DRAFT_STATUSES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown review status",
            detail=f"reviewStatus must be one of {sorted(DRAFT_STATUSES)}.",
            type_slug="validation",
            errors=[{"field": "reviewStatus", "message": "unknown review status"}],
        )

    from app.repositories.draft_review import SqlDraftReviewStore

    rows, total = await SqlDraftReviewStore().list_drafts(
        review_status=review_status.upper(),
        job_id=job_id,
        limit=limit,
        offset=(page - 1) * limit,
        session=session,
    )

    return paginated(
        [
            {
                "draftId": str(row.draft_id),
                "jobId": str(row.job_id),
                "reviewStatus": row.review_status,
                "preview": row.preview,
                "sourcePage": row.source_page,
                "detectedYear": row.detected_year,
                "detectedAttempt": row.detected_attempt,
                "detectedMarks": row.detected_marks,
                "detectedQuestionType": row.detected_question_type,
                # Advisory, and labelled as such in the UI: it is what the
                # extractor guessed, which an editor confirms or corrects.
                "detectionConfidence": row.detection_confidence,
                "createdAt": row.created_at,
            }
            for row in rows
        ],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.post("/ingestion/drafts/{draft_id}/review", summary="Approve or reject a draft")
async def review_draft(
    draft_id: uuid.UUID,
    payload: ReviewDraftIn,
    session: AsyncSession = Depends(get_db),
    caller: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
):
    """Record a QA decision, and on approval create a real question.

    APPROVE requires the publish permission (Content Manager or above): it creates a
    question row. REJECT and DUPLICATE only remove work from the queue, so an Editor
    may do them - blueprint §8.2, "Edit draft: Yes, Publish: No".

    The created question is DRAFT and has no verifier. Publishing is a separate
    act, gated by ``ck_questions_published_requires_verifier`` - clearing an OCR
    backlog is triage, not content sign-off.
    """
    from app.repositories.draft_review import SqlDraftReviewStore

    actor_id = _actor_id(caller)
    store = SqlDraftReviewStore()

    if payload.decision != "APPROVE":
        try:
            await reject_draft(
                store,
                draft_id,
                actor_id=actor_id,
                status="DUPLICATE" if payload.decision == "DUPLICATE" else "REJECTED",
                note=payload.note,
            )
        except DraftNotFound as exc:
            return _not_found(exc)
        except DraftAlreadyDecided as exc:
            return _conflict(exc)
        return success(
            {"draftId": str(draft_id), "reviewStatus": payload.decision},
            request_id=get_request_id(),
        )

    # ---- approval -------------------------------------------------------
    #
    # Checked HERE rather than as a route dependency because the same route is the
    # editor's reject button, and the two decisions carry different authority in the
    # blueprint's matrix. The role is read from the DATABASE (``caller.role``), not from
    # the token claim the way this used to be: a demotion must take effect on the next
    # request, and a claim minted an hour ago would still say Content Manager.
    if not has_permission(caller.role, Permission.PUBLISH_CONTENT):
        return problem(
            status=status.HTTP_403_FORBIDDEN,
            title="Insufficient permission",
            detail=(
                "Approving a draft creates a question; that is the publish sign-off and "
                "needs PUBLISH_CONTENT (Content Manager or above)."
            ),
            type_slug="forbidden",
        )

    missing = [
        field
        for field, value in (
            ("subject_id", payload.subject_id),
            ("question_type", payload.question_type),
            ("marks", payload.marks),
        )
        if value is None
    ]
    if missing:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Incomplete approval",
            detail=(
                "Approving creates a question, and a question cannot be stored "
                "without placement and marks."
            ),
            type_slug="validation",
            errors=[{"field": name, "message": "required when approving"} for name in missing],
        )

    try:
        result = await promote_draft(
            store, draft_id, payload.to_promotion_input(), actor_id=actor_id
        )
    except DraftNotFound as exc:
        return _not_found(exc)
    except DraftAlreadyDecided as exc:
        # Includes losing a race to a concurrent approval: the store re-checks
        # under a row lock, so this is a real conflict rather than a stale read.
        return _conflict(exc)
    except PlacementNotFound as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown placement",
            detail=str(exc),
            type_slug="validation",
            errors=[{"field": "subject_id", "message": str(exc)}],
        )
    except PromotionValidationError as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid question",
            detail="The draft could not be promoted as submitted.",
            type_slug="validation",
            errors=[{"field": e.field, "message": e.message} for e in exc.errors],
        )

    # 201 via an explicit JSONResponse rather than the decorator: this one route
    # returns 200 for a rejection and 201 for an approval, and the decorator can
    # only carry a single success code. The envelope is built by the same helper
    # either way, so the body shape does not depend on which branch ran.
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=success(
            {
                "draftId": str(result.draft_id),
                "questionId": str(result.question_id),
                "reviewStatus": "APPROVED",
                # Spelled out so a client cannot mistake approval for publication.
                "questionStatus": "DRAFT",
                "published": False,
            },
            request_id=get_request_id(),
        ),
    )


# =================================================================== helpers


def _actor_id(caller: User) -> uuid.UUID:
    """The acting user's database id, from the row the permission check already loaded.

    This used to take the token principal and look the row up a second time, with a
    ``None`` return for "could not resolve" - which is how an approval could be recorded
    with no author. The permission dependency has already loaded the user row (that is
    what makes a role change take effect immediately), so the id is in hand and
    ``questions.created_by`` is never left null on a path that required a real person.
    """
    return caller.id


def _not_found(exc: Exception):
    return problem(
        status=status.HTTP_404_NOT_FOUND,
        title="Draft not found",
        detail=str(exc),
        type_slug="not-found",
    )


def _conflict(exc: Exception):
    """409 rather than 200: the caller's intent was not carried out.

    Returning success would tell an editor their approval landed when a
    concurrent reviewer had already decided the draft differently.
    """
    return problem(
        status=status.HTTP_409_CONFLICT,
        title="Draft already reviewed",
        detail=str(exc),
        type_slug="conflict",
    )


# ================================================================= publishing
#
# THE LAST STEP, AND UNTIL NOW THE MISSING ONE.
#
# Extraction never publishes and approval creates a DRAFT with no verifier, so the
# whole chain is "review before students see it" only if a publish route exists.
# It did not: `POST /admin/questions/{id}/publish` is named in blueprint v3 §7.3 and
# was absent, which meant no question created by the ingestion flow could ever reach
# a student except by a database edit.


@router.post("/admin/questions/{question_id}/publish", summary="Publish a question")
async def publish_question(
    question_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    caller: User = Depends(require_permission(Permission.PUBLISH_CONTENT)),
):
    """Content Manager or above (v3 §8.2, "Publish: No" for Editor).

    Two things come from the token and are never read from the body: WHICH row was
    verified (``caller.id``) and WHETHER the caller may do it at all. The database
    then enforces the invariant that a published question carries a verifier, so a
    bug here cannot produce unattributed content.
    """
    store = SqlPublishingStore()
    question = await store.load_publishable_question(question_id, session=session)
    if question is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Question not found",
            detail=f"No question with id {question_id}",
            type_slug="not-found",
        )

    try:
        check_publishable(question)
    except AlreadyPublished as exc:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Already published",
            detail=str(exc),
            type_slug="conflict",
        )
    except NotPublishable as exc:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Not publishable from this status",
            detail=str(exc),
            type_slug="conflict",
        )
    except Incomplete as exc:
        # 409 rather than 422: the request is well-formed and the caller is allowed.
        # The QUESTION is not ready, and the fix is to edit its answer key - which is
        # why the detail names the missing thing instead of saying "invalid".
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="This question is not ready to publish",
            detail=str(exc),
            type_slug="conflict",
        )

    result = await store.publish_question(question_id, verifier_id=caller.id, session=session)
    await session.commit()

    return success(
        {
            "questionId": str(result.content_id),
            "status": result.status,
            "previousStatus": result.previous_status,
            "verifiedBy": str(result.verified_by),
        },
        request_id=get_request_id(),
    )
