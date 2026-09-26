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

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.identity import get_current_user
from app.core.permissions import Permission, require_permission
from app.integrations.supabase_storage import StorageError, storage_from_settings
from app.models.enums import DocumentKind
from app.models.user import User
from app.repositories.content import SqlContentStore
from app.schemas.base import StrictRequest, UuidRef
from app.services.analytics import Event, record_event
from app.services.audit import AuditAction, record_audit
from app.services.content_library import AccessDecision, Viewer, can_read
from app.services.entitlements import resolve_viewer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["content"])

#: Cap on one bulk request. 500 matches the folder size the owner actually has; the
#: limit exists so a scripted client cannot ask for 100,000 signed URLs in one call
#: and turn the API into an amplification vector.
MAX_BATCH_FILES = 500
#: Cap on a metadata update across a selection.
MAX_BULK_SELECTION = 1000


# --------------------------------------------------------------------- schemas


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


#: A document kind sent in a request body.
#:
#: THE SAME TRAP AS ``UuidRef`` in ``app/schemas/base.py``, and this one was live for
#: about ten minutes: JSON has no enum type, so every client sends the MEMBER NAME as a
#: string, and under ``strict=True`` Pydantic refuses that with
#: ``Input should be an instance of DocumentKind`` (``is_instance_of``). Every upload
#: and every metadata edit through HTTP would have been a 422 - while unit tests that
#: passed the enum member itself stayed green. Found by an integration test that posts
#: a real JSON body, which is the standard this repository already set for UuidRef.
#:
#: ``strict=False`` here relaxes exactly one thing: the string ``"NOTES"`` becomes
#: ``DocumentKind.NOTES``. It is still a CLOSED set - ``"PAST_PAPERS"`` is a 422 naming
#: the field, not a 500 from the database CHECK - which is the whole point of typing the
#: field instead of accepting any string up to 30 characters.
DocumentKindRef = Annotated[DocumentKind, Field(strict=False)]


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


class UpdateDocumentIn(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    kind: DocumentKindRef | None = Field(default=None)
    course_id: UuidRef | None = None
    subject_id: UuidRef | None = None
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None
    module: str | None = Field(default=None, max_length=120)
    difficulty: str | None = Field(default=None, max_length=10)
    access_tier: str | None = Field(default=None, max_length=20)
    syllabus_scheme: str | None = Field(default=None, max_length=20)
    tags: list[str] | None = None
    is_published: bool | None = None
    allow_download: bool | None = None


class BulkMetadataIn(StrictRequest):
    """Apply one set of metadata to a selection. The bulk-categorisation step."""

    document_ids: list[UuidRef] = Field(min_length=1, max_length=MAX_BULK_SELECTION)
    course_id: UuidRef | None = None
    subject_id: UuidRef | None = None
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None
    module: str | None = Field(default=None, max_length=120)
    kind: DocumentKindRef | None = Field(default=None)
    difficulty: str | None = Field(default=None, max_length=10)
    access_tier: str | None = Field(default=None, max_length=20)
    is_published: bool | None = None


class AccessRuleIn(StrictRequest):
    """An explicit grant or denial of access to one document."""

    scope: str = Field(pattern="^(ROLE|USER|COURSE|PLAN|TIER)$")
    effect: str = Field(default="ALLOW", pattern="^(ALLOW|DENY)$")
    role: str | None = Field(default=None, max_length=20)
    user_id: UuidRef | None = None
    course_id: UuidRef | None = None
    plan_code: str | None = Field(default=None, max_length=20)
    tier: str | None = Field(default=None, max_length=20)
    reason: str | None = Field(default=None, max_length=300)
    expires_at: datetime | None = None


# --------------------------------------------------------------------- payloads


def _viewer_payload(viewer: Viewer) -> dict[str, Any]:
    """What the client needs to render doors it cannot open.

    Deliberately NOT the full permission list: the browser's copy of "what may I do"
    is a UI hint, and the authoritative check happens on every request. Shipping the
    matrix here would invite someone to trust it.
    """
    return {"tier": viewer.tier, "role": viewer.role, "isStaff": viewer.is_staff}


def _document_payload(document: Any, *, viewer: Viewer | None = None) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "title": document.title,
        "kind": document.kind,
        "status": document.status,
        "originalFilename": document.original_filename,
        "mimeType": document.mime_type,
        "sizeBytes": document.size_bytes,
        "pageCount": document.page_count,
        "extractedChars": document.extracted_chars,
        "ocrPages": document.ocr_pages,
        "confidence": float(document.confidence) if document.confidence is not None else None,
        "error": document.error,
        "version": document.version,
        "accessTier": document.access_tier,
        "isPublished": document.is_published,
        "allowDownload": document.allow_download,
        "difficulty": document.difficulty,
        "module": document.module,
        "tags": document.tags or [],
        "courseId": str(document.course_id) if document.course_id else None,
        "subjectId": str(document.subject_id) if document.subject_id else None,
        "chapterId": str(document.chapter_id) if document.chapter_id else None,
        "topicId": str(document.topic_id) if document.topic_id else None,
        "batchId": str(document.batch_id) if document.batch_id else None,
        "processedAt": document.processed_at,
        "createdAt": document.created_at,
        "updatedAt": document.updated_at,
        "canRead": viewer is None or can_read(document, viewer) == AccessDecision.GRANTED,
    }


# ============================================================ STUDENT LIBRARY


@router.get("/content/library", summary="Study material you have access to")
async def list_library(
    q: str | None = Query(default=None, max_length=200),
    kind: DocumentKindRef | None = Query(default=None),
    course_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=24, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """The reading list, filtered by the caller's access rules IN THE QUERY.

    Every row returned here has already passed ``document_filter``: unpublished
    documents, documents the caller's tier does not reach, soft-deleted rows and
    explicitly denied ones are not "hidden by the client" - they were never selected.
    """
    viewer = await resolve_viewer(session, user)
    store = SqlContentStore(session)
    documents, total = await store.list_documents(
        viewer=viewer,
        kind=kind,
        course_id=course_id,
        subject_id=subject_id,
        chapter_id=chapter_id,
        search=q,
        limit=limit,
        offset=(page - 1) * limit,
    )
    return paginated(
        [_document_payload(document, viewer=viewer) for document in documents],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/content/documents/{document_id}", summary="One document, if you may read it")
async def get_library_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    viewer = await resolve_viewer(session, user)
    store = SqlContentStore(session)
    document = await store.get_document(document_id, viewer=viewer)
    if document is None:
        # ONE ANSWER for "does not exist" and "not allowed", because distinguishing
        # them tells a student that a document they cannot open exists - which is
        # itself information about the catalogue.
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="That document is not available to you.",
            type_slug="content",
        )
    await record_event(
        session,
        Event.DOCUMENT_OPENED,
        user_id=viewer.user_id,
        role=viewer.role,
        properties={"documentId": str(document.id), "title": document.title},
    )
    await session.commit()
    return success(_document_payload(document, viewer=viewer), request_id=get_request_id())


@router.get("/content/documents/{document_id}/pages", summary="Extracted text of a document")
async def get_library_pages(
    document_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """The extracted text, paginated by PDF page.

    This is the read the viewer exists for: reading the text IS reading the document,
    so it is gated exactly like the file itself. There is no unauthenticated path to
    this data and no route that returns a document's text without an access check.
    """
    viewer = await resolve_viewer(session, user)
    store = SqlContentStore(session)
    document = await store.get_document(document_id, viewer=viewer)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="That document is not available to you.",
            type_slug="content",
        )
    pages, total = await store.pages(document_id, limit=limit, offset=(page - 1) * limit)
    return success(
        {
            "documentId": str(document_id),
            "title": document.title,
            "pages": [
                {
                    "pageNumber": row.page_number,
                    "text": row.text,
                    "charCount": row.char_count,
                    "usedOcr": row.used_ocr,
                }
                for row in pages
            ],
            "count": len(pages),
            "total": total,
            "page": page,
            "limit": limit,
            "hasMore": page * limit < total,
        },
        request_id=get_request_id(),
    )


@router.get(
    "/content/documents/{document_id}/file",
    summary="A short-lived URL for the original PDF",
)
async def get_library_file(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Mint a viewing URL - and only mint a DOWNLOAD URL when the document allows it.

    The distinction is enforced here rather than by hiding a button. A student who
    calls this endpoint directly gets a URL whose disposition is ``inline``; asking
    for a download of a document with ``allow_download = false`` changes nothing,
    because the server never produces a URL with an attachment disposition for it.
    """
    viewer = await resolve_viewer(session, user)
    store = SqlContentStore(session)
    document = await store.get_document(document_id, viewer=viewer)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="That document is not available to you.",
            type_slug="content",
        )
    if document.status not in {"INDEXED", "COMPLETED"} and not viewer.is_staff:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Not ready",
            detail="This document has not finished processing yet.",
            type_slug="content",
        )

    from app.integrations.supabase_storage import DOWNLOAD_URL_TTL_SECONDS

    # `signed_url`, not `signed_download_url` - the latter never existed. A short-lived
    # signed URL is also the ONLY way a student reaches the file: the bucket is private
    # and no storage credential is ever handed to the browser.
    storage = storage_from_settings()
    url = await storage.signed_url(
        document.bucket,
        document.storage_path,
        expires_in=DOWNLOAD_URL_TTL_SECONDS,
    )
    return success(
        {
            "url": url,
            "expiresInSeconds": DOWNLOAD_URL_TTL_SECONDS,
            "downloadable": document.allow_download,
            "filename": document.original_filename,
        },
        request_id=get_request_id(),
    )


@router.get("/content/search", summary="Search inside your study material")
async def search_library(
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Full-text search over EXTRACTED PAGE TEXT, scoped to permitted documents.

    The scope is a subquery over ``document_filter``: a hit inside a document the
    caller may not read cannot be returned, because the page's parent document is not
    in the id set being searched. Search is the easiest place to leak content by
    forgetting a join - which is why the filter is applied to the DOCUMENT ids rather
    than to anything about the page.
    """
    viewer = await resolve_viewer(session, user)
    store = SqlContentStore(session)
    hits = await store.search_pages(q, viewer, limit=limit)
    await record_event(
        session,
        Event.DOCUMENT_SEARCHED,
        user_id=viewer.user_id,
        role=viewer.role,
        properties={"query": q[:120], "hits": len(hits)},
    )
    await session.commit()
    return success(
        {
            "query": q,
            "hits": [
                {
                    "documentId": str(hit.document_id),
                    "title": hit.title,
                    "pageNumber": hit.page_number,
                    # Highlighted with <mark>, which the client renders as TEXT and
                    # not as HTML. See the study-screens test for the same rule.
                    "excerpt": hit.excerpt,
                    "score": round(hit.rank, 4),
                }
                for hit in hits
            ],
            "count": len(hits),
        },
        request_id=get_request_id(),
    )


# ============================================================ ADMIN CONTENT


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


@router.get("/admin/content/documents", summary="Every document, with filters")
async def admin_list_documents(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    kind: DocumentKindRef | None = Query(default=None),
    course_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_CONTENT)),
) -> Any:
    """The library table the admin works in: 500 rows, filterable by state."""
    store = SqlContentStore(session)
    documents, total = await store.list_documents(
        status=status_filter,
        kind=kind,
        course_id=course_id,
        subject_id=subject_id,
        batch_id=batch_id,
        search=q,
        include_archived=include_archived,
        limit=limit,
        offset=(page - 1) * limit,
    )
    payload = [_document_payload(document) for document in documents]
    if q and q.strip():
        # One extra query for the whole page. It answers "why is this row here?": the
        # search matches extracted TEXT, so a result whose title looks unrelated is
        # correct and the page number is what proves it.
        matches = await store.text_matches_for([document.id for document in documents], q)
        for item in payload:
            page_number = matches.get(uuid.UUID(item["id"]))
            item["matchedPage"] = page_number
            item["matchSource"] = "TEXT" if page_number is not None else "METADATA"
    return paginated(
        payload,
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/admin/content/batches/{batch_id}", summary="Progress of one bulk upload")
async def batch_progress(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_CONTENT)),
) -> Any:
    """Per-state counts for a batch, plus the failures with their reasons.

    Computed with one grouped aggregate, so it stays correct when the admin closes the
    browser mid-upload and comes back - the batch IS the rows, not the page's memory.
    """
    from sqlalchemy import func, select

    from app.models.content import ContentDocument

    rows = (
        await session.execute(
            select(ContentDocument.status, func.count(ContentDocument.id))
            .where(ContentDocument.batch_id == batch_id)
            .group_by(ContentDocument.status)
        )
    ).all()
    counts = {row[0]: int(row[1]) for row in rows}
    failures = (
        (
            await session.execute(
                select(ContentDocument)
                .where(
                    ContentDocument.batch_id == batch_id,
                    ContentDocument.status == "FAILED",
                )
                .order_by(ContentDocument.created_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    done = sum(counts.get(name, 0) for name in ("INDEXED", "COMPLETED", "ARCHIVED"))
    total = sum(counts.values())
    return success(
        {
            "batchId": str(batch_id),
            "counts": counts,
            "total": total,
            "done": done,
            "failed": counts.get("FAILED", 0),
            "progressPercent": round(done / total * 100) if total else 0,
            "failures": [
                {
                    "documentId": str(document.id),
                    "filename": document.original_filename,
                    "error": document.error,
                }
                for document in failures
            ],
        },
        request_id=get_request_id(),
    )


@router.post(
    "/admin/content/documents/{document_id}/restore",
    summary="Bring an archived document back",
)
async def restore_document(
    document_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    """Un-archive. The document comes back UNPUBLISHED, so it cannot reappear in front
    of students because somebody clicked restore while tidying up."""
    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )
    if not await store.restore(document_id):
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Not archived",
            detail="That document is not archived.",
            type_slug="content",
        )
    await record_audit(
        session,
        AuditAction.DOCUMENT_UPDATED,
        actor=actor,
        summary=f"Restored '{document.title}' from the archive",
        target_type="document",
        target_id=document_id,
        request=request,
    )
    await session.commit()
    return success({"documentId": str(document_id), "restored": True}, request_id=get_request_id())


@router.post("/admin/content/bulk-metadata", summary="Categorise many documents at once")
async def bulk_metadata(
    payload: BulkMetadataIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    """Apply metadata to a selection - the step that makes 500 files manageable.

    Processing state is deliberately NOT settable here: an admin must not be able to
    mark a document COMPLETED by hand, because that would publish unprocessed text.
    """
    values: dict[str, Any] = {}
    for field in (
        "course_id",
        "subject_id",
        "chapter_id",
        "topic_id",
        "module",
        "kind",
        "difficulty",
        "access_tier",
        "is_published",
    ):
        value = getattr(payload, field)
        if value is not None:
            values[field] = value
    if not values:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Nothing to change",
            detail="Send at least one metadata field.",
            type_slug="content",
        )

    store = SqlContentStore(session)
    changed = await store.bulk_assign(list(payload.document_ids), values)
    await record_audit(
        session,
        AuditAction.DOCUMENT_UPDATED,
        actor=actor,
        summary=f"Bulk metadata update on {changed} document(s)",
        target_type="document",
        changes={"fields": sorted(values), "count": changed},
        request=request,
    )
    await session.commit()
    return success({"updated": changed, "fields": sorted(values)}, request_id=get_request_id())


@router.patch("/admin/content/documents/{document_id}", summary="Edit document metadata")
async def update_document(
    document_id: uuid.UUID,
    payload: UpdateDocumentIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )

    values = {
        field: value
        for field, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not values:
        return success(_document_payload(document), request_id=get_request_id())

    before = {field: getattr(document, field, None) for field in values}
    updated = await store.update_document(document_id, values)
    await record_audit(
        session,
        AuditAction.DOCUMENT_UPDATED,
        actor=actor,
        summary=f"Updated '{document.title}'",
        target_type="document",
        target_id=document_id,
        changes={
            field: {
                "from": str(before[field]) if before[field] is not None else None,
                "to": str(value) if value is not None else None,
            }
            for field, value in values.items()
        },
        request=request,
    )
    await session.commit()
    return success(_document_payload(updated), request_id=get_request_id())


@router.get("/admin/content/documents/{document_id}/text", summary="View extracted text")
async def admin_view_text(
    document_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_CONTENT)),
) -> Any:
    """The raw/clean text an editor needs before generating questions from it."""
    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )
    pages, total = await store.pages(document_id, limit=limit, offset=(page - 1) * limit)
    return success(
        {
            "documentId": str(document_id),
            "title": document.title,
            "status": document.status,
            "pages": [
                {
                    "pageNumber": row.page_number,
                    "text": row.text,
                    "rawText": row.raw_text,
                    "charCount": row.char_count,
                    "tier": row.extraction_tier,
                    "confidence": float(row.confidence) if row.confidence is not None else None,
                    "usedOcr": row.used_ocr,
                }
                for row in pages
            ],
            "count": len(pages),
            "total": total,
            "page": page,
            "limit": limit,
            "hasMore": page * limit < total,
        },
        request_id=get_request_id(),
    )


@router.get(
    "/admin/content/documents/{document_id}/download",
    summary="Signed URL for the original file",
)
async def admin_download(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    """The ORIGINAL file, which is never deleted when text is extracted."""
    from app.integrations.supabase_storage import DOWNLOAD_URL_TTL_SECONDS

    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )
    # The ORIGINAL file is downloadable by an admin even when students are not allowed
    # to download it: `allow_download` governs the student surface, and an admin needs
    # the source to check a generated question against.
    storage = storage_from_settings()
    url = await storage.signed_url(
        document.bucket,
        document.storage_path,
        expires_in=DOWNLOAD_URL_TTL_SECONDS,
    )
    return success(
        {"url": url, "expiresInSeconds": DOWNLOAD_URL_TTL_SECONDS}, request_id=get_request_id()
    )


@router.post("/admin/content/documents/{document_id}/reprocess", summary="Re-run the pipeline")
async def reprocess_document(
    document_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    """Queue another extraction run, without touching the original file.

    Re-processing is how a failed OCR pass is retried after the extractor improves,
    and how a document imported before a bug fix is repaired. The previous pages stay
    in place until the new run succeeds - ``replace_pages`` is transactional - so a
    failed retry does not lose good text.
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
        document_id, {"ingestion_job_id": job_id, "status": "QUEUED", "error": None}
    )
    enqueued = False
    try:
        from app.workers.rq_worker import enqueue_ingestion

        enqueue_ingestion(str(job_id), document.storage_path, document.bucket)
        enqueued = True
    except Exception:  # noqa: BLE001
        logger.warning("could not enqueue reprocess job %s", job_id, exc_info=True)

    await record_audit(
        session,
        AuditAction.DOCUMENT_REPROCESSED,
        actor=actor,
        summary=f"Re-queued '{document.title}'",
        target_type="document",
        target_id=document_id,
        request=request,
    )
    await session.commit()
    return success(
        {"documentId": str(document_id), "jobId": str(job_id), "enqueued": enqueued},
        request_id=get_request_id(),
    )


@router.delete("/admin/content/documents/{document_id}", summary="Archive a document")
async def archive_document(
    document_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    """ARCHIVE: out of the student library, still in the admin list, reversible.

    There is deliberately no route that erases a document. Questions generated from
    it reference the row and the file is the evidence a question was reviewed against;
    an endpoint that removed either would break the traceability the library exists
    for. ``POST .../restore`` brings it back.
    """
    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )
    await store.archive(document_id)
    await record_audit(
        session,
        AuditAction.DOCUMENT_ARCHIVED,
        actor=actor,
        summary=f"Archived '{document.title}'",
        target_type="document",
        target_id=document_id,
        request=request,
    )
    await session.commit()
    return success({"documentId": str(document_id), "archived": True}, request_id=get_request_id())


# ------------------------------------------------------------------ access rules


@router.get("/admin/content/documents/{document_id}/access", summary="Access rules for a document")
async def list_access_rules(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )
    rules = await store.rules_for(document_id)
    return success(
        {
            "documentId": str(document_id),
            "accessTier": document.access_tier,
            "isPublished": document.is_published,
            "rules": [
                {
                    "id": str(rule.id),
                    "scope": rule.scope,
                    "effect": rule.effect,
                    "role": rule.role,
                    "userId": str(rule.user_id) if rule.user_id else None,
                    "courseId": str(rule.course_id) if rule.course_id else None,
                    "planCode": rule.plan_code,
                    "tier": rule.tier,
                    "reason": rule.reason,
                    "expiresAt": rule.expires_at,
                    "createdAt": rule.created_at,
                }
                for rule in rules
            ],
        },
        request_id=get_request_id(),
    )


@router.post(
    "/admin/content/documents/{document_id}/access",
    status_code=status.HTTP_201_CREATED,
    summary="Grant or deny access to a document",
)
async def add_access_rule(
    document_id: uuid.UUID,
    payload: AccessRuleIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    """Add one rule. The CHECK constraint refuses a rule with no target."""
    from sqlalchemy.exc import IntegrityError

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
        rule = await store.add_rule(
            document_id=document_id,
            scope=payload.scope,
            effect=payload.effect,
            role=payload.role,
            user_id=payload.user_id,
            course_id=payload.course_id,
            plan_code=payload.plan_code,
            tier=payload.tier,
            reason=payload.reason,
            expires_at=payload.expires_at,
            granted_by=actor.id,
        )
    except IntegrityError:
        # The unique constraint caught a duplicate target; the CHECK caught a rule with
        # no target. Both are the admin's mistake rather than the server's, and both
        # deserve a sentence rather than a 500.
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Rule not valid",
            detail="A rule like this already exists, or its target is missing.",
            type_slug="content",
        )

    # The summary names the target, because "ACCESS granted" with no "to whom" is an
    # audit line that answers nothing when it is read six months later.
    target = (
        payload.role or payload.tier or payload.plan_code or payload.user_id or payload.course_id
    )
    await record_audit(
        session,
        AuditAction.ACCESS_GRANTED if payload.effect == "ALLOW" else AuditAction.ACCESS_REVOKED,
        actor=actor,
        summary=f"{payload.effect} {payload.scope} access to '{document.title}' for {target}",
        target_type="document",
        target_id=document_id,
        changes={"scope": payload.scope, "effect": payload.effect},
        request=request,
    )
    await session.commit()
    return success({"id": str(rule.id)}, request_id=get_request_id())


@router.delete("/admin/content/access/{rule_id}", summary="Remove an access rule")
async def delete_access_rule(
    rule_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    store = SqlContentStore(session)
    if not await store.delete_rule(rule_id):
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Rule not found",
            detail="No such access rule.",
            type_slug="content",
        )
    await record_audit(
        session,
        AuditAction.ACCESS_REVOKED,
        actor=actor,
        summary="Removed an access rule",
        target_type="access_rule",
        target_id=rule_id,
        request=request,
    )
    await session.commit()
    return success({"id": str(rule_id), "deleted": True}, request_id=get_request_id())
