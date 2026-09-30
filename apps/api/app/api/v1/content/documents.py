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
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import Permission, require_permission
from app.integrations.supabase_storage import storage_from_settings
from app.models.user import User
from app.repositories.content import SqlContentStore
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit

from ._shared import (
    MAX_BULK_SELECTION,
    DocumentKindRef,
    _document_payload,
    logger,
)

router = APIRouter(tags=["content"])

"""Admin document management: list, edit, text, download, archive."""


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
