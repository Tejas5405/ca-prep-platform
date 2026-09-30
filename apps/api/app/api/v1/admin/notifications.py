"""The owner's back office: platform numbers, people, permissions, settings, logs.

WHAT THIS FILE IS FOR

Everything here answers a question the owner has to answer without a database client:
"What is the platform doing?", "who is on it?", "what may they do?", "who changed
this?", and "turn that feature off". Each route requires a PERMISSION rather than a
role, so a delegated accountant can be given payments and nothing else - see
``app/core/permissions.py`` for why that distinction exists and what it costs.

WHY THE PERMISSION CHECKS READ THE DATABASE

``require_permission`` resolves the caller's role from ``users.role`` on every
request instead of trusting the token claim alone. An access token lives for an hour;
"revoke this person's admin access" must not mean "it takes effect within an hour",
especially when the permission being revoked gates deleting the entire library.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import (
    Permission,
    require_permission,
)
from app.core.security import Role
from app.models.engagement import Notification
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services.analytics import Event
from app.services.audit import AuditAction, record_audit
from app.services.entitlements import entitlements_for

from ._shared import (
    _record,
)

router = APIRouter(tags=["admin"])

"""Broadcasts and notification history."""


class BroadcastIn(StrictRequest):
    """Send a notification to an audience.

    THE AUDIENCE IS A NAME, NOT A FILTER THE CLIENT BUILDS. "All students", "premium
    students", "one user" are the three shapes the owner actually needs, and each is
    resolved server-side into a set of user ids. A client-supplied filter would be an
    authorization decision made by the browser.
    """

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=2000)
    kind: str = Field(default="ANNOUNCEMENT", max_length=20)
    audience: str = Field(
        default="ALL_STUDENTS", pattern="^(ALL_STUDENTS|PREMIUM|FREE|ONE_USER|ROLE)$"
    )
    user_id: UuidRef | None = None
    role: str | None = Field(default=None, max_length=20)
    link_url: str | None = Field(default=None, max_length=300)


@router.post(
    "/admin/notifications",
    status_code=status.HTTP_201_CREATED,
    summary="Send an in-app notification",
)
async def send_notification(
    payload: BroadcastIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_NOTIFICATIONS)),
) -> Any:
    """Fan out to one row per recipient.

    The link is validated as an IN-APP path. An absolute URL authored by an admin and
    rendered as a clickable link is a phishing primitive, and this is the only place
    that text enters the product.
    """
    if payload.link_url and not payload.link_url.startswith("/"):
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid link",
            detail="Links must be in-app paths starting with '/'.",
            type_slug="notifications",
        )

    recipients = await _resolve_audience(session, payload)
    if not recipients:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Nobody to notify",
            detail="That audience matched no active user.",
            type_slug="notifications",
        )

    broadcast_id = uuid.uuid4()
    for user_id in recipients:
        session.add(
            Notification(
                id=uuid.uuid4(),
                user_id=user_id,
                kind=payload.kind,
                title=payload.title[:200],
                body=payload.body,
                link_url=payload.link_url,
                created_by=actor.id,
                audience=payload.audience,
                broadcast_id=broadcast_id,
            )
        )
    await record_audit(
        session,
        AuditAction.NOTIFICATION_SENT,
        actor=actor,
        summary=f"Sent '{payload.title}' to {len(recipients)} recipient(s)",
        target_type="broadcast",
        target_id=broadcast_id,
        changes={"audience": payload.audience, "recipients": len(recipients)},
        request=request,
    )
    await _record(session, Event.NOTIFICATION_SENT, actor, {"broadcastId": str(broadcast_id)})
    await session.commit()
    return success(
        {"broadcastId": str(broadcast_id), "recipients": len(recipients)},
        request_id=get_request_id(),
    )


async def _resolve_audience(session: AsyncSession, payload: BroadcastIn) -> list[uuid.UUID]:
    """Turn an audience name into user ids. Server-side, always."""
    base = select(User.id).where(User.is_active.is_(True), User.deleted_at.is_(None))
    if payload.audience == "ALL_STUDENTS":
        base = base.where(User.role == Role.STUDENT.value)
    elif payload.audience == "ROLE":
        base = base.where(User.role == (payload.role or Role.STUDENT.value))
    elif payload.audience == "ONE_USER":
        base = (
            base.where(User.id == payload.user_id)
            if payload.user_id
            else base.where(User.id.is_(None))
        )
    else:
        # PREMIUM / FREE are decided by the ENTITLEMENT service, not by a column:
        # an expired subscription still says PREMIUM in the database, and notifying
        # those students as paying customers is the exact mistake this indirection
        # exists to prevent.
        candidates = list(
            (await session.execute(base.where(User.role == Role.STUDENT.value))).scalars()
        )
        ids: list[uuid.UUID] = []
        for user_id in candidates:
            snapshot = await entitlements_for(session, user_id)
            if payload.audience == "PREMIUM" and snapshot.is_premium:
                ids.append(user_id)
            elif payload.audience == "FREE" and not snapshot.is_premium:
                ids.append(user_id)
        return ids
    return list((await session.execute(base)).scalars())


@router.get("/admin/notifications", summary="Notification history")
async def notification_history(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_NOTIFICATIONS)),
) -> Any:
    """Grouped by broadcast: one row per send, with its reach and read count."""
    grouped = (
        select(
            Notification.broadcast_id,
            func.min(Notification.title).label("title"),
            func.min(Notification.kind).label("kind"),
            func.count(Notification.id).label("recipients"),
            func.count(Notification.read_at).label("read"),
            func.min(Notification.created_at).label("sent_at"),
        )
        .where(Notification.broadcast_id.is_not(None))
        .group_by(Notification.broadcast_id)
        .subquery()
    )
    total = (await session.execute(select(func.count()).select_from(grouped))).scalar_one()
    rows = (
        await session.execute(
            select(grouped)
            .order_by(grouped.c.sent_at.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    ).all()
    return paginated(
        [
            {
                "broadcastId": str(row.broadcast_id),
                "title": row.title,
                "kind": row.kind,
                "recipients": int(row.recipients),
                "read": int(row.read),
                "sentAt": row.sent_at,
            }
            for row in rows
        ],
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )
