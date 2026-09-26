"""Doubt threads - blueprint v3 §18.1, the largest P0 item that was missing.

WHO MAY DO WHAT, and why each rule is here rather than in the UI:

* A student sees their own threads and no others. Enforced in the repository's
  WHERE clause, not by a check after the fetch.
* Staff (anything above STUDENT) see the queue.
* Only staff may answer as staff. ``is_staff_answer`` is set from the caller's
  role, never from the request body - otherwise a student writing
  ``"isStaffAnswer": true`` would have their reply rendered with the authority of
  a teacher's.
* Only the author may close their own doubt; only staff may resolve it. A student
  marking a doubt resolved before it is answered is how a queue looks emptier than
  it is.

The status transitions live here because they are authorization decisions, and the
repository stays a data-access object that the tests can drive directly.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.identity import get_current_user, is_staff
from app.models.doubt import Doubt, DoubtReply
from app.models.user import User
from app.repositories.doubts import STUDENT_STATUSES, SqlDoubtRepository
from app.schemas.base import StrictRequest, UuidRef

router = APIRouter(tags=["doubts"])

MAX_PAGE_SIZE = 50


class CreateDoubtIn(StrictRequest):
    title: str = Field(min_length=5, max_length=300)
    body: str = Field(min_length=2, max_length=5000)
    subject_id: UuidRef | None = None
    chapter_id: UuidRef | None = None
    question_id: UuidRef | None = None
    priority: str = Field(default="NORMAL", max_length=10)


class ReplyIn(StrictRequest):
    body: str = Field(min_length=2, max_length=5000)


class StatusIn(StrictRequest):
    status: str = Field(max_length=20)
    resolution_note: str | None = Field(default=None, max_length=2000)


def _reply_payload(reply: DoubtReply, author: User | None) -> dict[str, Any]:
    return {
        "id": str(reply.id),
        "body": reply.body,
        "isStaffAnswer": reply.is_staff_answer,
        "isAccepted": reply.is_accepted,
        "upvotes": reply.upvotes,
        "createdAt": reply.created_at.isoformat(),
        # A deleted author's reply keeps its text: the thread is the record of how
        # the doubt was handled, and dropping the reply would rewrite that history.
        "author": {
            "displayName": author.display_name if author else None,
            "role": author.role if author else None,
        },
    }


def _doubt_payload(
    doubt: Doubt, replies: list[tuple[DoubtReply, User]] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(doubt.id),
        "title": doubt.title,
        "body": doubt.body,
        "status": doubt.status,
        "priority": doubt.priority,
        "subjectId": str(doubt.subject_id) if doubt.subject_id else None,
        "chapterId": str(doubt.chapter_id) if doubt.chapter_id else None,
        "questionId": str(doubt.question_id) if doubt.question_id else None,
        "replyCount": doubt.reply_count,
        "acceptedReplyId": str(doubt.accepted_reply_id) if doubt.accepted_reply_id else None,
        "resolutionNote": doubt.resolution_note,
        "createdAt": doubt.created_at.isoformat(),
        "lastActivityAt": doubt.last_activity_at.isoformat(),
    }
    if replies is not None:
        payload["replies"] = [_reply_payload(reply, author) for reply, author in replies]
    return payload


@router.post("/doubts", status_code=status.HTTP_201_CREATED, summary="Ask a doubt")
async def create_doubt(
    payload: CreateDoubtIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    if payload.priority not in {"LOW", "NORMAL", "HIGH"}:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid priority",
            detail="priority must be LOW, NORMAL or HIGH.",
            type_slug="doubts",
        )

    doubt = await SqlDoubtRepository(session).create(
        user_id=user.id,
        title=payload.title,
        body=payload.body,
        subject_id=payload.subject_id,
        chapter_id=payload.chapter_id,
        question_id=payload.question_id,
        priority=payload.priority,
    )
    await session.commit()
    return success(_doubt_payload(doubt), request_id=get_request_id())


@router.get("/doubts", summary="List doubts")
async def list_doubts(
    status_filter: str | None = None,
    subject_id: uuid.UUID | None = None,
    page: int = 1,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """The student's own threads; staff see everything.

    ``status_filter`` rather than ``status`` only because the module imports
    FastAPI's ``status``; the wire name is documented as ``status_filter``.
    """
    page = max(1, page)
    limit = min(MAX_PAGE_SIZE, max(1, limit))

    rows, total = await SqlDoubtRepository(session).list_for(
        user_id=user.id,
        is_staff=is_staff(user),
        status=status_filter,
        subject_id=subject_id,
        page=page,
        limit=limit,
    )
    return paginated(
        [_doubt_payload(doubt) for doubt in rows],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/doubts/{doubt_id}", summary="Read a doubt thread")
async def read_doubt(
    doubt_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    repo = SqlDoubtRepository(session)
    doubt = await repo.get_for(doubt_id=doubt_id, user_id=user.id, is_staff=is_staff(user))
    if doubt is None:
        # 404 rather than 403 for someone else's doubt: a 403 confirms that the id
        # exists, which turns the endpoint into an enumeration oracle.
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Doubt not found",
            detail="No such doubt for this account.",
            type_slug="doubts",
        )

    replies = (await repo.replies_for([doubt.id])).get(doubt.id, [])
    return success(_doubt_payload(doubt, replies), request_id=get_request_id())


@router.post(
    "/doubts/{doubt_id}/replies",
    status_code=status.HTTP_201_CREATED,
    summary="Reply to a doubt",
)
async def reply_to_doubt(
    doubt_id: uuid.UUID,
    payload: ReplyIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    repo = SqlDoubtRepository(session)
    staff = is_staff(user)
    doubt = await repo.get_for(doubt_id=doubt_id, user_id=user.id, is_staff=staff)
    if doubt is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Doubt not found",
            detail="No such doubt for this account.",
            type_slug="doubts",
        )
    if doubt.status == "CLOSED":
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Thread closed",
            detail="This doubt was closed. Ask a new one if it is still unclear.",
            type_slug="doubts",
        )

    reply = await repo.add_reply(
        doubt_id=doubt.id,
        user_id=user.id,
        body=payload.body,
        # From the ROLE, never from the payload.
        is_staff_answer=staff,
    )
    await session.commit()
    return success(_reply_payload(reply, user), request_id=get_request_id())


@router.patch("/doubts/{doubt_id}", summary="Change a doubt's status")
async def set_doubt_status(
    doubt_id: uuid.UUID,
    payload: StatusIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    repo = SqlDoubtRepository(session)
    staff = is_staff(user)
    doubt = await repo.get_for(doubt_id=doubt_id, user_id=user.id, is_staff=staff)
    if doubt is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Doubt not found",
            detail="No such doubt for this account.",
            type_slug="doubts",
        )

    wanted = payload.status.upper()
    allowed = STUDENT_STATUSES | {"ANSWERED", "RESOLVED"} if staff else STUDENT_STATUSES
    if wanted not in allowed:
        return problem(
            status=status.HTTP_403_FORBIDDEN if not staff else status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Not allowed",
            detail=(
                f"You may set {sorted(allowed)}."
                if not staff
                else f"status must be one of {sorted(allowed)}."
            ),
            type_slug="doubts",
        )
    if wanted == "RESOLVED" and not staff:
        # Unreachable while `allowed` is right, and kept as a second lock: this is
        # the rule that keeps the resolution rate meaningful.
        return problem(
            status=status.HTTP_403_FORBIDDEN,
            title="Not allowed",
            detail="Only staff can mark a doubt resolved.",
            type_slug="doubts",
        )

    await repo.set_status(
        doubt=doubt,
        status=wanted,
        resolved_by=user.id if wanted == "RESOLVED" else None,
        resolution_note=payload.resolution_note,
    )
    await session.commit()
    return success(_doubt_payload(doubt), request_id=get_request_id())


@router.post(
    "/doubts/{doubt_id}/replies/{reply_id}/accept",
    summary="Accept a reply as the answer",
)
async def accept_reply(
    doubt_id: uuid.UUID,
    reply_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """The asker (or staff) accepts one reply, which resolves the doubt."""
    repo = SqlDoubtRepository(session)
    doubt = await repo.get_for(doubt_id=doubt_id, user_id=user.id, is_staff=is_staff(user))
    if doubt is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Doubt not found",
            detail="No such doubt for this account.",
            type_slug="doubts",
        )

    reply = await repo.reply(reply_id)
    if reply is None or reply.doubt_id != doubt.id:
        # The reply must belong to THIS doubt: without the check, any reply id would
        # be acceptable on any thread the caller can see.
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Reply not found",
            detail="That reply does not belong to this doubt.",
            type_slug="doubts",
        )

    await repo.accept_reply(doubt=doubt, reply=reply, user_id=user.id)
    await session.commit()
    replies = (await repo.replies_for([doubt.id])).get(doubt.id, [])
    return success(_doubt_payload(doubt, replies), request_id=get_request_id())
