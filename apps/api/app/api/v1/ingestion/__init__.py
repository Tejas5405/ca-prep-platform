"""Ingestion: uploads, jobs, draft review, publishing.

Split from the former single-file app/api/v1/ingestion.py. `router` carries the same
paths, methods, tags, names and status codes as before, in the same order.
`CreateUploadIn` and `ReviewDraftIn` are re-exported so existing
`from app.api.v1.ingestion import ...` callers keep resolving."""

from __future__ import annotations

from fastapi import APIRouter

from . import drafts as _drafts
from . import jobs as _jobs
from . import publishing as _publishing
from . import uploads as _uploads
from ._shared import JOB_STAGES, OptionIn, logger
from .drafts import ReviewDraftIn, list_drafts, review_draft
from .jobs import get_job, list_jobs, start_job
from .publishing import publish_question
from .uploads import CreateUploadIn, create_upload

router = APIRouter()
router.routes.extend(_uploads.router.routes)
router.routes.extend(_jobs.router.routes)
router.routes.extend(_drafts.router.routes)
router.routes.extend(_publishing.router.routes)

__all__ = [
    "JOB_STAGES",
    "CreateUploadIn",
    "OptionIn",
    "ReviewDraftIn",
    "create_upload",
    "get_job",
    "list_drafts",
    "list_jobs",
    "logger",
    "publish_question",
    "review_draft",
    "router",
    "start_job",
]
