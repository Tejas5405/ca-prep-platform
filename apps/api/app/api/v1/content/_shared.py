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
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import Field

from app.models.enums import DocumentKind
from app.services.content_library import AccessDecision, Viewer, can_read

router = APIRouter(tags=["content"])

"""Constants, logger, the DocumentKind alias and payload helpers shared by
more than one sub-module. Unchanged; only relocated."""


logger = logging.getLogger(__name__)


#: Cap on one bulk request. 500 matches the folder size the owner actually has; the
#: limit exists so a scripted client cannot ask for 100,000 signed URLs in one call
#: and turn the API into an amplification vector.
MAX_BATCH_FILES = 500


MAX_BATCH_FILES = 500
#: Cap on a metadata update across a selection.
MAX_BULK_SELECTION = 1000


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
