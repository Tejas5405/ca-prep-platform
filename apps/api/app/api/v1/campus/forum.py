"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.campus import (
    ForumPost,
    ForumThread,
)
from app.models.user import User
from app.schemas.base import StrictRequest

from ._shared import (
    _DISCUSSION,
    _uuid,
)

router = APIRouter(tags=["campus"])

"""Discussion threads and replies."""


class ThreadIn(StrictRequest):
    title: str = Field(min_length=2, max_length=140)
    body: str = Field(min_length=2, max_length=2000)
    subject_label: str | None = Field(default=None, max_length=80)


class PostIn(StrictRequest):
    body: str = Field(min_length=2, max_length=2000)


@router.get("/campus/forum", summary="Discussion threads")
async def list_threads(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(ForumThread).order_by(ForumThread.created_at.desc()).limit(40)
            )
        )
        .scalars()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(ForumPost.thread_id, func.count()).group_by(ForumPost.thread_id)
            )
        ).all()
    )
    return success(
        {
            "threads": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "subjectLabel": row.subject_label,
                    "posts": int(counts.get(row.id, 0)),
                }
                for row in rows
            ],
            "disclaimer": _DISCUSSION,
        },
        request_id=get_request_id(),
    )


@router.post("/campus/forum", summary="Start a discussion")
async def create_thread(
    payload: ThreadIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    thread = ForumThread(
        id=uuid.uuid4(),
        title=payload.title.strip(),
        subject_label=payload.subject_label,
        created_by=user.id,
    )
    session.add(thread)
    session.add(
        ForumPost(
            id=uuid.uuid4(), thread_id=thread.id, author_id=user.id, body=payload.body.strip()
        )
    )
    await session.commit()
    return success(
        {"id": str(thread.id), "disclaimer": _DISCUSSION},
        request_id=get_request_id(),
    )


@router.get("/campus/forum/{thread_id}", summary="One discussion, without email addresses")
async def read_thread(
    thread_id: str,
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    parsed = _uuid(thread_id)
    thread = await session.get(ForumThread, parsed) if parsed else None
    if thread is None:
        return problem(
            status=404,
            title="Thread not found",
            detail="That thread does not exist.",
            type_slug="campus",
        )
    posts = (
        await session.execute(
            select(ForumPost, User.display_name)
            .join(User, User.id == ForumPost.author_id)
            .where(ForumPost.thread_id == thread.id)
            .order_by(ForumPost.created_at)
        )
    ).all()
    return success(
        {
            "id": str(thread.id),
            "title": thread.title,
            "disclaimer": _DISCUSSION,
            "posts": [
                {"id": str(post.id), "body": post.body, "author": name or "Student"}
                for post, name in posts
            ],
            "emailsIncluded": False,
        },
        request_id=get_request_id(),
    )


@router.post("/campus/forum/{thread_id}/posts", summary="Reply in a discussion")
async def reply_thread(
    thread_id: str,
    payload: PostIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    parsed = _uuid(thread_id)
    thread = await session.get(ForumThread, parsed) if parsed else None
    if thread is None:
        return problem(
            status=404,
            title="Thread not found",
            detail="That thread does not exist.",
            type_slug="campus",
        )
    post = ForumPost(
        id=uuid.uuid4(), thread_id=thread.id, author_id=user.id, body=payload.body.strip()
    )
    session.add(post)
    await session.commit()
    return success({"id": str(post.id), "disclaimer": _DISCUSSION}, request_id=get_request_id())
