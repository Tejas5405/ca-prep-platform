"""Mock exam endpoints - blueprint v3 §7.3 and §18.1."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.user import User
from app.repositories.draft_review import SqlPublishingStore
from app.services.publishing import AlreadyPublished, Incomplete

router = APIRouter(tags=["mocks"])

"""Publishing a mock paper."""


@router.post("/admin/mocks/{mock_id}/publish")
async def publish_mock(
    mock_id: str,
    session: AsyncSession = Depends(get_db),
    caller: User = Depends(require_permission(Permission.PUBLISH_CONTENT)),
):
    """Publish a mock paper. Content Manager or above (v3 §8.2).

    THIS USED TO WRITE NOTHING. It answered `200 {"status": "PUBLISHED"}` for any
    id - including ids that did not exist - and left the row in DRAFT, so the
    response was a lie that no client could detect: the paper simply never appeared
    in `GET /mocks`, which lists published papers only.

    A paper may only go live when every question on it is published. Otherwise a
    student opens a paper that cannot be scored, and the report shows a denominator
    built from questions nobody approved.
    """
    try:
        key = uuid.UUID(mock_id)
    except ValueError:
        return problem(
            status=status.HTTP_400_BAD_REQUEST,
            title="Invalid mock id",
            detail="Mock ids are UUIDs.",
            type_slug="validation",
        )

    store = SqlPublishingStore()
    try:
        result = await store.publish_mock(key, verifier_id=caller.id, session=session)
    except AlreadyPublished as exc:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Already published",
            detail=str(exc),
            type_slug="conflict",
        )
    except Incomplete as exc:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="This paper is not ready",
            detail=str(exc),
            type_slug="conflict",
        )

    if result is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Mock paper not found",
            detail=f"No mock paper with id {mock_id}",
            type_slug="not-found",
        )

    await session.commit()
    return success(
        {
            "mockTestId": str(result.content_id),
            "status": result.status,
            "previousStatus": result.previous_status,
            # The person on the record, taken from the TOKEN rather than the body:
            # a verifier the client could name proves nothing.
            "verifiedBy": str(result.verified_by),
            "questionCount": result.question_count,
        },
        request_id=get_request_id(),
    )
