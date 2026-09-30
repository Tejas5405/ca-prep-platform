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
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, success
from app.core.pagination import InvalidCursor, paginate_cursor
from app.core.permissions import (
    Permission,
    require_permission,
)
from app.models.engagement import AnalyticsEvent, AuditLog
from app.models.user import User

router = APIRouter(tags=["admin"])

"""Dashboard, analytics rollup and the audit log."""


@router.get("/admin/dashboard", summary="Platform overview")
async def dashboard(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_ANALYTICS)),
) -> Any:
    """Every headline number, from the database.

    One aggregate call rather than twenty counts: the owner opens this page every
    morning and it must not get slower as the library grows. Figures the platform
    genuinely cannot know yet (AI usage with no provider key) are returned as zero
    WITH the flag that explains it, rather than padded with a plausible number.
    """
    from app.repositories.content import SqlContentStore

    counts = await SqlContentStore(session).dashboard_counts()
    return success(
        {
            "students": {
                "total": counts.total_students,
                "active": counts.active_students,
                "newThisWeek": counts.new_students_7d,
            },
            "content": {
                "documents": counts.total_documents,
                "processing": counts.processing_documents,
                "failed": counts.failed_documents,
                "indexed": counts.indexed_documents,
                "pages": counts.total_pages,
                "extractedChars": counts.extracted_chars,
                "storageBytes": counts.storage_bytes,
            },
            "curriculum": {
                "courses": counts.total_courses,
                "subjects": counts.total_subjects,
                "chapters": counts.total_chapters,
                "questions": counts.total_questions,
                "publishedQuestions": counts.published_questions,
                "mockTests": counts.total_mocks,
            },
            "activity": {
                "mockAttempts": counts.mock_attempts,
                "completedAttempts": counts.completed_attempts,
                "notificationsSent": counts.notifications_sent,
                "aiEvents": counts.ai_events,
            },
            "money": {
                # Rupees, because every screen in this product quotes rupees and a
                # dashboard that silently switches units is a dashboard that gets
                # misread by a factor of a hundred.
                "revenueRupees": counts.total_revenue_paise // 100,
                "successfulPayments": counts.successful_payments,
                "failedPayments": counts.failed_payments,
                "activeSubscriptions": counts.active_subscriptions,
            },
            "generatedAt": datetime.now(UTC),
        },
        request_id=get_request_id(),
    )


@router.get("/admin/analytics", summary="Event analytics")
async def analytics_summary(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_ANALYTICS)),
) -> Any:
    """Event counts over a window, plus a daily series for the busiest few.

    VERIFIED RATHER THAN ASSUMED. Before this round ``analytics_events`` did not
    exist and nothing was written anywhere, so an "analytics" screen would have been a
    page of zeroes explained as growth. The events below are the ones the platform
    actually emits - each has a call site that was added in this round - and this
    endpoint reads the same table those call sites write.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    totals = (
        await session.execute(
            select(AnalyticsEvent.name, func.count(AnalyticsEvent.id))
            .where(AnalyticsEvent.created_at >= since)
            .group_by(AnalyticsEvent.name)
            .order_by(func.count(AnalyticsEvent.id).desc())
        )
    ).all()

    daily = (
        await session.execute(
            select(
                func.date_trunc("day", AnalyticsEvent.created_at).label("day"),
                func.count(AnalyticsEvent.id),
            )
            .where(AnalyticsEvent.created_at >= since)
            .group_by("day")
            .order_by("day")
        )
    ).all()

    return success(
        {
            "windowDays": days,
            "totals": [{"event": row[0], "count": int(row[1])} for row in totals],
            "daily": [{"date": row[0].date().isoformat(), "count": int(row[1])} for row in daily],
            "totalEvents": sum(int(row[1]) for row in totals),
        },
        request_id=get_request_id(),
    )


@router.get("/admin/audit", summary="Audit log")
async def audit_log(
    action: str | None = Query(default=None, max_length=60),
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(
        default=None,
        description=(
            "Opaque keyset position from a previous page's meta.nextCursor. Supplying "
            "it switches to cursor pagination, which is stable under concurrent "
            "inserts - offset pagination can repeat and skip rows on an audit log, "
            "which is read precisely because something is wrong."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_AUDIT)),
) -> Any:
    """Who did what. Newest first, because the question is always "what just happened"."""
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if actor_user_id:
        conditions.append(AuditLog.actor_user_id == actor_user_id)
    if target_type:
        conditions.append(AuditLog.target_type == target_type)

    base = select(AuditLog).where(*conditions) if conditions else select(AuditLog)
    rows: Sequence[AuditLog]

    # Keyset is the DEFAULT for this route, including the first page.
    #
    # The first attempt at this only ran keyset when `cursor` was supplied - which
    # is a dead end, because a client cannot obtain a cursor without first
    # receiving one. The first page therefore has to be able to EMIT a cursor, so
    # it runs keyset too. `page` is kept for the legacy offset walk and is
    # honoured only when the caller actually asks for page > 1.
    if cursor or page <= 1:
        # Keyset path. No COUNT(*): the whole reason to page by cursor is that
        # counting an append-only log on every request is the cost being avoided.
        # `data` stays a list and the cursor facts go in `meta`, so this is the
        # SAME envelope as the offset path - a client does not have to detect
        # which kind of response it received.
        try:
            rows, next_cursor, has_more = await paginate_cursor(
                session, AuditLog, query=base, cursor=cursor, limit=limit
            )
        except InvalidCursor as exc:
            # A bad cursor is the CLIENT's problem, and must not surface as a
            # 500: that sends an operator to the server logs for a mistyped query
            # parameter, and tells the client the server is broken.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        total: int | None = None
        page_number = 1
        extra = {"nextCursor": next_cursor} if next_cursor else None
    else:
        total = (
            await session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            (
                await session.execute(
                    base.order_by(AuditLog.created_at.desc())
                    .limit(limit)
                    .offset((page - 1) * limit)
                )
            )
            .scalars()
            .all()
        )
        next_cursor = None
        has_more = False
        page_number = page
        extra = None

    payload = [
        {
            "id": str(row.id),
            "action": row.action,
            "summary": row.summary,
            "actorEmail": row.actor_email,
            "actorRole": row.actor_role,
            "actorUserId": str(row.actor_user_id) if row.actor_user_id else None,
            "targetType": row.target_type,
            "targetId": row.target_id,
            "changes": row.changes,
            "ipAddress": row.ip_address,
            "createdAt": row.created_at,
        }
        for row in rows
    ]
    # `total` is deliberately absent on the cursor path: it is None there, so the
    # count is not claimed rather than being reported as a misleading 0. A client
    # walking with a cursor does not need a total; a client that wants one omits
    # the cursor and gets the offset path, which counts.
    envelope = paginated(
        payload,
        total=int(total or 0),
        page=page_number,
        limit=limit,
        request_id=get_request_id(),
        extra=extra,
    )
    envelope["meta"]["hasMore"] = has_more
    return envelope
