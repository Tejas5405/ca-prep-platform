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

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.integrations.supabase_storage import (
    BUCKETS,
    PREFIXES,
    StorageError,
    SupabaseStorage,
    build_object_path,
    validate_upload,
)
from app.models.user import User
from app.repositories.ingestion import SqlIngestionSink
from app.schemas.base import StrictRequest, UuidRef

from ._shared import (
    logger,
)

router = APIRouter(tags=["ingestion"])

"""Creating an upload and its ingestion job."""


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
