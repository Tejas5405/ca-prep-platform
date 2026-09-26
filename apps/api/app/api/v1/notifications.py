"""The student's inbox.

VERIFIED BEFORE BUILDING: there was no inbox. ``rq_worker.send_notification_job``
logged a line and returned - a stand-in that left nothing anywhere - and there was no
notifications table, no route and no screen. The table, the fan-out and the read paths
are new; the worker's function is now the only thing left to wire to a real email
provider, and that is configuration rather than code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.identity import get_current_user
from app.models.engagement import Notification
from app.models.user import User

router = APIRouter(tags=["notifications"])


def _payload(row: Notification) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "linkUrl": row.link_url,
        "read": row.read_at is not None,
        "readAt": row.read_at,
        "createdAt": row.created_at,
    }


@router.get("/notifications", summary="Your notifications")
async def list_notifications(
    unread_only: bool = False,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Your rows only, newest first. ``user_id`` is part of the predicate.

    There is no route that reads another student's notifications and no parameter that
    could select one: the filter is the caller's own id, taken from the verified token
    and resolved to a ``users.id``, not from anything the client sends.
    """
    conditions = [Notification.user_id == user.id]
    if unread_only:
        conditions.append(Notification.read_at.is_(None))
    base = select(Notification).where(*conditions)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(Notification.created_at.desc())
                .limit(limit)
                .offset((page - 1) * limit)
            )
        )
        .scalars()
        .all()
    )
    unread = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
    ).scalar_one()
    return paginated(
        [_payload(row) for row in rows],
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
        # The badge on the bell: computed, not tracked in the browser, so it is right
        # on a second device and after a hard refresh.
        extra={"unread": int(unread)},
    )


@router.get("/notifications/unread-count", summary="Unread badge count")
async def unread_count(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """A tiny endpoint, on purpose: the header polls this and must not pull 30 rows."""
    unread = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
    ).scalar_one()
    return success({"unread": int(unread)}, request_id=get_request_id())


@router.post("/notifications/{notification_id}/read", summary="Mark one as read")
async def mark_read(
    notification_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Mark read. The ``user_id`` clause is what makes this safe.

    Without it, any signed-in student could mark any other student's notification as
    read by guessing an id - and while that is a small harm, it is the same class of
    mistake as reading their data, and it is one clause to prevent.
    """
    result = await session.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=datetime.now(UTC))
    )
    if not result.rowcount:
        await session.rollback()
        return problem(
            status=404,
            title="Not found",
            detail="No such notification for this account.",
            type_slug="notifications",
        )
    await session.commit()
    return success({"id": str(notification_id), "read": True}, request_id=get_request_id())


@router.post("/notifications/read-all", summary="Mark everything as read")
async def mark_all_read(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return success({"marked": int(result.rowcount or 0)}, request_id=get_request_id())
