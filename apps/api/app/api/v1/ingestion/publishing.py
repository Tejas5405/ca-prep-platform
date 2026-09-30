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

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.user import User
from app.repositories.draft_review import SqlPublishingStore
from app.services.publishing import (
    AlreadyPublished,
    Incomplete,
    NotPublishable,
    check_publishable,
)

router = APIRouter(tags=["ingestion"])

"""Publishing a reviewed question."""


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
