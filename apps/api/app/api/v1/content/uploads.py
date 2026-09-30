"""The content library: student reading and admin bulk upload.

TWO AUDIENCES, ONE FILE, DELIBERATELY

The student routes and the admin routes are separated by path (``/content/...`` vs
``/admin/content/...``) and by permission, but they live together because they share
one idea and one access rule. Splitting them into two modules is how the student list
and the admin list drift apart - one gains a filter the other lacks, one forgets the
access clause. Anyone touching document visibility should have to scroll past both.

BULK UPLOAD, WHICH IS THE POINT OF THE WHOLE FEATURE

500 PDFs cannot go through the API as 500 request bodies. Two facts shape the design:

  1. The bytes never touch the API. Each file gets its own short-lived signed URL and
     the browser PUTs straight to Supabase Storage, in parallel, with a concurrency
     cap. Render never buffers a 50 MB file, and one slow upload cannot block the
     other 499.
  2. One request creates the batch. ``POST /admin/content/uploads`` takes a manifest
     of up to 500 entries, computes duplicates by checksum, and returns a row per
     file with its upload URL and its document id. The admin screen then works
     through that list, showing per-file state and retrying only what failed.

That is how 500 files become one operation with a progress bar instead of an
afternoon of clicking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.integrations.supabase_storage import StorageError, storage_from_settings
from app.models.enums import DocumentKind
from app.models.user import User
from app.repositories.content import SqlContentStore
from app.schemas.base import StrictRequest, UuidRef
from app.services.analytics import Event, record_event
from app.services.audit import AuditAction, record_audit

from ._shared import (
    MAX_BATCH_FILES,
    DocumentKindRef,
    logger,
)

router = APIRouter(tags=["content"])

"""Creating uploads and starting extraction."""


class UploadFileIn(StrictRequest):
    """One file's intent, declared BEFORE its bytes move.

    The checksum is computed in the browser (``crypto.subtle.digest``) and sent here.
    It is what makes duplicate detection work on the manifest pass, before 500 files
    have been uploaded, and it is re-computed server-side after the bytes land so a
    truncated transfer is caught rather than indexed.
    """

    filename: str = Field(min_length=1, max_length=300)
    size_bytes: int = Field(ge=0, le=50 * 1024 * 1024)
    content_type: str = Field(default="application/pdf", max_length=100)
    checksum_sha256: str = Field(min_length=64, max_length=64)
    #: Optional per-file placement. The batch-level values are used when absent, which
    #: is what makes "drop a folder of Financial Reporting material" one decision.
    title: str | None = Field(default=None, max_length=300)
    subject_id: UuidRef | None = None
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None


class CreateUploadsIn(StrictRequest):
    """A batch manifest. One call for up to 500 files."""

    files: list[UploadFileIn] = Field(min_length=1, max_length=MAX_BATCH_FILES)
    kind: DocumentKindRef = Field(default=DocumentKind.STUDY_MATERIAL)
    course_id: UuidRef | None = None
    subject_id: UuidRef | None = None
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None
    module: str | None = Field(default=None, max_length=120)
    difficulty: str = Field(default="MEDIUM", max_length=10)
    access_tier: str = Field(default="PREMIUM", max_length=20)
    syllabus_scheme: str | None = Field(default=None, max_length=20)
    tags: list[str] = Field(default_factory=list)
    #: When true, files whose checksum is already in the library are reported as
    #: duplicates and are NOT given an upload URL. When false the admin has decided to
    #: keep a second copy, which is occasionally right (a corrected reprint).
    skip_duplicates: bool = True


@router.post(
    "/admin/content/uploads",
    status_code=status.HTTP_201_CREATED,
    summary="Begin a bulk upload (up to 500 files)",
)
async def create_uploads(
    payload: CreateUploadsIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    """Create the batch and hand back one signed upload URL per file.

    THE THREE THINGS THIS DOES THAT A LOOP OF SINGLE UPLOADS CANNOT

    1. Duplicate detection across the WHOLE library, by checksum, before a byte moves.
       Uploading 500 files that overlap with the 200 already here is the expected case.
    2. One batch id, so progress is a query rather than a tally in the browser, and an
       interrupted session can be resumed by looking at the batch.
    3. Storage paths that cannot collide: ``originals/<year>/<month>/<uuid>/<name>``.
       Two files called "Chapter 5.pdf" from two folders are two documents.
    """
    from app.integrations.supabase_storage import (
        BUCKETS,
        PREFIXES,
        UPLOAD_URL_TTL_SECONDS,
        build_object_path,
        validate_upload,
    )

    # Reject a bad batch BEFORE creating any row: a manifest with one invalid entry
    # should not leave 499 half-created documents behind.
    for entry in payload.files:
        try:
            validate_upload(
                filename=entry.filename,
                content_type=entry.content_type,
                size_bytes=entry.size_bytes,
            )
        except StorageError as exc:
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="File rejected",
                detail=f"{entry.filename}: {exc}",
                type_slug="content",
            )

    try:
        storage = storage_from_settings()
    except StorageError:
        # NOT `except Exception`: an unconfigured deployment is a 503 the owner can act
        # on, whereas a wiring mistake must surface as a 500 rather than be misreported
        # as a missing environment variable. That is exactly how the previous version of
        # this route hid a broken storage client behind a correct-looking error.
        return problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Storage not configured",
            detail="SUPABASE_URL and SUPABASE_SECRET_KEY must be set to accept uploads.",
            type_slug="content",
        )

    store = SqlContentStore(session)
    batch_id = uuid.uuid4()
    accepted: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    for entry in payload.files:
        existing = (
            await store.checksum_owner(entry.checksum_sha256) if payload.skip_duplicates else None
        )
        if existing is not None:
            duplicates.append(
                {
                    "filename": entry.filename,
                    "reason": "DUPLICATE",
                    "existingDocumentId": str(existing.id),
                    "existingTitle": existing.title,
                }
            )
            continue

        # SERVER-GENERATED path. The client's filename is a readable suffix only; the
        # unique component is a uuid4, so one admin cannot overwrite another's object
        # by declaring the same name. The month segment groups a batch for a future
        # lifecycle rule without moving objects.
        storage_path = build_object_path(
            owner_id=str(actor.id),
            filename=entry.filename,
            prefix=f"{PREFIXES['originals']}/{datetime.now(UTC):%Y/%m}",
        )
        document = await store.create_document(
            title=entry.title or entry.filename.rsplit(".", 1)[0][:300],
            kind=payload.kind,
            bucket=BUCKETS["questions"],
            storage_path=storage_path,
            original_filename=entry.filename,
            mime_type=entry.content_type,
            size_bytes=entry.size_bytes,
            checksum_sha256=entry.checksum_sha256,
            uploaded_by=actor.id,
            course_id=payload.course_id,
            subject_id=entry.subject_id or payload.subject_id,
            chapter_id=entry.chapter_id or payload.chapter_id,
            topic_id=entry.topic_id or payload.topic_id,
            module=payload.module,
            difficulty=payload.difficulty,
            access_tier=payload.access_tier,
            batch_id=batch_id,
            tags=payload.tags,
        )
        try:
            minted = await storage.create_signed_upload_url(document.bucket, storage_path)
            upload_url = minted["uploadUrl"]
        except StorageError as exc:
            await store.set_status(document.id, "FAILED", error=str(exc))
            duplicates.append(
                {"filename": entry.filename, "reason": "STORAGE_ERROR", "detail": str(exc)}
            )
            continue

        accepted.append(
            {
                "documentId": str(document.id),
                "filename": entry.filename,
                "storagePath": storage_path,
                "uploadUrl": upload_url,
                "expiresInSeconds": UPLOAD_URL_TTL_SECONDS,
            }
        )

    await record_audit(
        session,
        AuditAction.BULK_UPLOAD_COMPLETED,
        actor=actor,
        summary=(
            f"Bulk upload prepared: {len(accepted)} file(s) accepted, {len(duplicates)} skipped"
        ),
        target_type="batch",
        target_id=batch_id,
        changes={"accepted": len(accepted), "skipped": len(duplicates)},
        request=request,
    )
    await record_event(
        session,
        Event.BULK_UPLOAD_STARTED,
        user_id=actor.id,
        role=actor.role,
        properties={"batchId": str(batch_id), "files": len(payload.files)},
    )
    await session.commit()

    return success(
        {
            "batchId": str(batch_id),
            "accepted": accepted,
            "skipped": duplicates,
            "acceptedCount": len(accepted),
            "skippedCount": len(duplicates),
            # The client PUTs in parallel; this is the concurrency the API is sized for
            # and the number the upload screen should use.
            "recommendedConcurrency": 4,
            "nextStep": (
                "PUT each file to its uploadUrl, then call POST /admin/content/documents/{id}/start"
            ),
        },
        request_id=get_request_id(),
    )


@router.post(
    "/admin/content/documents/{document_id}/start",
    summary="Queue processing once the bytes are in storage",
)
async def start_processing(
    document_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    """Confirm the object exists, then hand the document to the pipeline.

    THE EXISTENCE CHECK IS NOT PARANOIA. Without it, a client that never uploaded
    would enqueue a job that fails ten minutes later on a download error, and the
    admin sees a storage outage instead of "your upload did not complete". The check
    turns an asynchronous mystery into an immediate 409.
    """
    from app.repositories.ingestion import SqlIngestionSink

    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )

    try:
        storage = storage_from_settings()
        exists = await storage.exists(document.bucket, document.storage_path)
    except StorageError as exc:
        return problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Storage unavailable",
            detail=str(exc),
            type_slug="content",
        )
    if not exists:
        await store.set_status(document_id, "FAILED", error="Uploaded object not found in storage.")
        await session.commit()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Upload incomplete",
            detail="The file was never stored. Upload it again and retry.",
            type_slug="content",
        )

    # The job row is what the worker and the admin queue read. It is created in
    # QUEUED and linked from the document, so the document keeps its own lifecycle
    # while the run has a history.
    sink = SqlIngestionSink()
    # ``open_job``, not ``create_job``: one ingestion job exists per stored object
    # (``uq_ingestion_job_object``), so a second press of Re-process RE-OPENS the run
    # instead of inserting a row the constraint refuses. Inserting was a 500 carrying a
    # PostgreSQL message - on the retry path the admin uses after a failure.
    job_id = await sink.open_job(
        bucket=document.bucket,
        storage_path=document.storage_path,
        uploaded_by=actor.id,
        course_id=document.course_id,
        subject_id=document.subject_id,
        # The request's session: the job row and the document update must commit
        # together, so a crash between them cannot leave a job nobody references.
        session=session,
    )
    await store.update_document(
        document_id,
        {
            "ingestion_job_id": job_id,
            "status": "QUEUED",
            "error": None,
        },
    )

    enqueued = False
    try:
        from app.workers.rq_worker import enqueue_ingestion

        enqueue_ingestion(str(job_id), document.storage_path, document.bucket)
        enqueued = True
    except Exception:  # noqa: BLE001 - Redis may be absent in development
        # Recorded, not fatal: the document stays QUEUED and the admin can retry, and
        # the response says exactly that rather than pretending a worker picked it up.
        logger.warning("could not enqueue ingestion job %s", job_id, exc_info=True)

    await record_audit(
        session,
        AuditAction.DOCUMENT_UPLOADED,
        actor=actor,
        summary=f"Queued '{document.title}' for processing",
        target_type="document",
        target_id=document_id,
        request=request,
    )
    await session.commit()
    return success(
        {
            "documentId": str(document_id),
            "jobId": str(job_id),
            "status": "QUEUED",
            "enqueued": enqueued,
            "note": None if enqueued else "Queued in the database; the worker was not reachable.",
        },
        request_id=get_request_id(),
    )
