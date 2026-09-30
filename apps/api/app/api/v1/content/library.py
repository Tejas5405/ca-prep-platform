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

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.identity import get_current_user
from app.integrations.supabase_storage import storage_from_settings
from app.models.user import User
from app.repositories.content import SqlContentStore
from app.services.analytics import Event, record_event
from app.services.entitlements import resolve_viewer

from ._shared import (
    DocumentKindRef,
    _document_payload,
)

router = APIRouter(tags=["content"])

"""The read-only study library: browse, pages, file, search."""


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
