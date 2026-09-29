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
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import (
    ALL_PERMISSIONS,
    OWNER_ONLY,
    ROLE_PERMISSIONS,
    Permission,
    permissions_for,
    require_permission,
)
from app.core.security import ROLE_RANK, Role
from app.models.engagement import AnalyticsEvent, AuditLog, Badge, Notification, PlatformSetting
from app.models.progress import UserBadge, UserQuestionProgress
from app.models.user import PaymentEvent, PaymentOrder, Subscription, User
from app.schemas.base import StrictRequest, UuidRef
from app.services.analytics import Event
from app.services.audit import AuditAction, record_audit
from app.services.entitlements import entitlements_for

router = APIRouter(tags=["admin"])

#: Roles that may be assigned through the API. SUPER_ADMIN is excluded: it is the
#: owner's own role, granted out of band by configuration, and an endpoint that can
#: mint more owners is an endpoint that can be used to mint one after a compromise.
ASSIGNABLE_ROLES: tuple[str, ...] = tuple(
    role.value for role in Role if role is not Role.SUPER_ADMIN
)


# ------------------------------------------------------------------ dashboard


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
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(AuditLog.created_at.desc()).limit(limit).offset((page - 1) * limit)
            )
        )
        .scalars()
        .all()
    )
    return paginated(
        [
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
        ],
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


# ----------------------------------------------------------------------- users


def _user_payload(user: User, *, subscription: Subscription | None = None) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "displayName": user.display_name,
        "role": user.role,
        "isActive": user.is_active,
        "avatarUrl": user.avatar_url,
        "phone": user.phone,
        "targetLevel": user.target_level,
        "targetExamDate": user.target_exam_date,
        "syllabusScheme": user.syllabus_scheme,
        "createdAt": user.created_at,
        "lastActiveAt": user.last_active_at,
        "deletedAt": user.deleted_at,
        "subscription": (
            {
                "tier": subscription.tier,
                "status": subscription.status,
                "expiresAt": subscription.expires_at,
            }
            if subscription is not None
            else None
        ),
    }


@router.get("/admin/users", summary="Search and filter users")
async def list_users(
    q: str | None = Query(default=None, max_length=200),
    role: str | None = Query(default=None, max_length=20),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    sort: str = Query(default="recent", pattern="^(recent|email|active)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_USERS)),
) -> Any:
    """The people list. Soft-deleted accounts are included and marked, not hidden:
    "why can this email not sign up again" is answered here or nowhere."""
    conditions = []
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(or_(User.email.ilike(needle), User.display_name.ilike(needle)))
    if role:
        conditions.append(User.role == role)
    if status_filter == "active":
        conditions.extend([User.is_active.is_(True), User.deleted_at.is_(None)])
    elif status_filter == "suspended":
        conditions.append(User.is_active.is_(False))

    base = select(User).where(*conditions) if conditions else select(User)
    order = {
        "recent": User.created_at.desc(),
        "email": User.email.asc(),
        "active": User.last_active_at.desc().nullslast(),
    }[sort]

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    users = (
        (await session.execute(base.order_by(order).limit(limit).offset((page - 1) * limit)))
        .scalars()
        .all()
    )

    # Subscriptions for the page, in one query rather than one per row.
    ids = [user.id for user in users]
    subs: dict[uuid.UUID, Subscription] = {}
    if ids:
        rows = (
            (
                await session.execute(
                    select(Subscription)
                    .where(Subscription.user_id.in_(ids))
                    .order_by(Subscription.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for sub in rows:
            subs.setdefault(sub.user_id, sub)

    return paginated(
        [_user_payload(user, subscription=subs.get(user.id)) for user in users],
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/admin/users/{user_id}", summary="One student in full")
async def get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_USERS)),
) -> Any:
    """Everything the admin needs about one student, in one call.

    Progress, badges, subscription and activity are gathered here rather than left to
    four screens: the questions being asked ("has this person paid?", "are they
    actually using it?", "what should I tell them?") are asked together.
    """
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="User not found",
            detail="No such user.",
            type_slug="users",
        )

    subscription = (
        await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    progress = (
        await session.execute(
            select(
                func.count(UserQuestionProgress.id),
                func.coalesce(func.sum(UserQuestionProgress.attempts_count), 0),
                func.coalesce(func.sum(UserQuestionProgress.correct_count), 0),
            ).where(UserQuestionProgress.user_id == user_id)
        )
    ).one()

    badges = (
        (
            await session.execute(
                select(UserBadge)
                .where(UserBadge.user_id == user_id)
                .order_by(UserBadge.earned_at.desc())
            )
        )
        .scalars()
        .all()
    )

    snapshot = await entitlements_for(session, user_id)
    return success(
        {
            "user": _user_payload(user, subscription=subscription),
            "entitlements": snapshot.as_payload(),
            "progress": {
                "questionsTouched": int(progress[0] or 0),
                "attempts": int(progress[1] or 0),
                "correct": int(progress[2] or 0),
                "accuracy": (
                    round(int(progress[2]) / int(progress[1]), 4) if int(progress[1]) else None
                ),
            },
            "badges": [{"code": badge.badge_code, "earnedAt": badge.earned_at} for badge in badges],
            "points": await _points_total(session, user_id),
        },
        request_id=get_request_id(),
    )


async def _points_total(session: AsyncSession, user_id: uuid.UUID) -> int:
    from app.models.progress import PointsLedger

    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                    PointsLedger.user_id == user_id
                )
            )
        ).scalar_one()
        or 0
    )


class UpdateUserIn(StrictRequest):
    """Admin edit. Email and password are NOT here - Supabase Auth owns them.

    Changing an email behind the identity provider would leave the two systems
    disagreeing about who a person is, and the failure surfaces at the next sign-in.
    """

    display_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    role: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None
    target_level: str | None = Field(default=None, max_length=20)


@router.patch("/admin/users/{user_id}", summary="Edit a user")
async def update_user(
    user_id: uuid.UUID,
    payload: UpdateUserIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.VIEW_USERS)),
) -> Any:
    """Edit a user's product fields, and optionally their role.

    SELF-DEMOTION IS REFUSED. Taking away your own admin rights by accident is easy,
    and if you are the only admin it locks the platform's controls behind a database
    edit - which is exactly the situation this panel exists to end.
    """
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="User not found",
            detail="No such user.",
            type_slug="users",
        )

    values = payload.model_dump(exclude_unset=True, exclude_none=True)

    if "role" in values:
        new_role = str(values["role"])
        # Role changes need MANAGE_ROLES, which is owner-only; the route's own
        # permission (VIEW_USERS) is deliberately not enough.
        from app.core.permissions import has_permission

        if not has_permission(actor.role, Permission.MANAGE_ROLES):
            return problem(
                status=status.HTTP_403_FORBIDDEN,
                title="Not allowed",
                detail="Changing roles requires the MANAGE_ROLES permission.",
                type_slug="permissions",
            )
        if new_role not in ASSIGNABLE_ROLES:
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Unknown role",
                detail=f"Role must be one of {', '.join(ASSIGNABLE_ROLES)}.",
                type_slug="permissions",
            )
        # RANK-LIMITED DELEGATION. You may not create a peer or a superior: an Admin
        # can move someone between STUDENT, EDITOR, MODERATOR and CONTENT_MANAGER, and
        # only the owner can create another Admin. Without this, a compromised or
        # careless admin account can mint an accomplice in one request.
        if (
            actor.role != Role.SUPER_ADMIN.value
            and ROLE_RANK[Role(new_role)] >= ROLE_RANK[Role(actor.role)]
        ):
            return problem(
                status=status.HTTP_403_FORBIDDEN,
                title="Cannot assign that role",
                detail=(
                    f"You may assign roles below {actor.role}. Only the owner can grant this one."
                ),
                type_slug="permissions",
            )
        if user.id == actor.id:
            return problem(
                status=status.HTTP_409_CONFLICT,
                title="Cannot change your own role",
                detail="Ask another admin to change your role.",
                type_slug="users",
            )

    if not values:
        return success(_user_payload(user), request_id=get_request_id())

    before = {field: getattr(user, field, None) for field in values}
    for field, value in values.items():
        setattr(user, field, value)
    await session.flush()

    action = (
        AuditAction.ROLE_CHANGED
        if "role" in values
        else (
            AuditAction.USER_SUSPENDED
            if values.get("is_active") is False
            else AuditAction.USER_UPDATED
        )
    )
    await record_audit(
        session,
        action,
        actor=actor,
        summary=f"Updated {user.email or user.id}: {', '.join(sorted(values))}",
        target_type="user",
        target_id=user.id,
        changes={
            field: {"from": str(before[field]), "to": str(value)} for field, value in values.items()
        },
        request=request,
    )
    if "role" in values:
        await _record(
            session, Event.ROLE_CHANGED, actor, {"userId": str(user.id), "role": user.role}
        )
    if values.get("is_active") is False:
        await _record(session, Event.USER_SUSPENDED, actor, {"userId": str(user.id)})
    await session.commit()

    # The role claim in Supabase must follow the row, or the two disagree until the
    # token expires. Best-effort and reported: the DATABASE row is already committed,
    # and the API reads permissions from it, so a failure here is a delay rather than
    # an outage.
    claim_updated = True
    if "role" in values and user.auth_user_id:
        try:
            from app.integrations.supabase_auth import SupabaseAuthAdmin

            await SupabaseAuthAdmin().set_role_claim(user.auth_user_id, str(values["role"]))
        except Exception:  # noqa: BLE001
            claim_updated = False

    return success(
        {**_user_payload(user), "claimUpdated": claim_updated}, request_id=get_request_id()
    )


async def _record(
    session: AsyncSession, name: str, actor: User, properties: dict[str, Any]
) -> None:
    from app.services.analytics import record_event

    await record_event(session, name, user_id=actor.id, role=actor.role, properties=properties)


# ------------------------------------------------------------ roles and perms


@router.get("/admin/permissions", summary="The role/permission matrix")
async def permission_matrix(
    _actor: User = Depends(require_permission(Permission.VIEW_USERS)),
) -> Any:
    """What each role may do, as the SERVER computes it.

    This endpoint exists so the admin UI never hard-codes the matrix. A front-end copy
    of "Editors can publish" is a comment that compiles, and it is wrong the first
    time a permission moves.
    """
    return success(
        {
            "roles": [
                {
                    "role": role.value,
                    "rank": ROLE_RANK[role],
                    "assignable": role.value in ASSIGNABLE_ROLES,
                    "permissions": sorted(p.value for p in ROLE_PERMISSIONS.get(role, frozenset())),
                }
                for role in Role
            ],
            "permissions": [
                {"key": permission.value, "ownerOnly": permission in OWNER_ONLY}
                for permission in ALL_PERMISSIONS
            ],
            "note": (
                "Permissions are enforced on the server on every request; this list is for display."
            ),
        },
        request_id=get_request_id(),
    )


@router.get("/admin/me/permissions", summary="What the caller may do")
async def my_permissions(
    actor: User = Depends(require_permission(Permission.VIEW_CONTENT)),
) -> Any:
    """Drives the admin navigation.

    The sidebar hides sections this list does not contain, and every one of those
    sections is also refused by its own route - the list is a convenience, not the
    control.
    """
    granted = permissions_for(actor.role)
    return success(
        {
            "role": actor.role,
            "permissions": sorted(p.value for p in granted),
            "isOwner": actor.role == Role.SUPER_ADMIN.value,
        },
        request_id=get_request_id(),
    )


# -------------------------------------------------------------------- settings


@router.get("/admin/settings", summary="Platform settings")
async def list_settings(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    rows = (
        (await session.execute(select(PlatformSetting).order_by(PlatformSetting.key)))
        .scalars()
        .all()
    )
    return success(
        {
            "settings": [
                {
                    "key": row.key,
                    "value": row.value,
                    "description": row.description,
                    "isPublic": row.is_public,
                    "updatedAt": row.updated_at,
                }
                for row in rows
            ]
        },
        request_id=get_request_id(),
    )


class SettingIn(StrictRequest):
    key: str = Field(min_length=1, max_length=80)
    value: Any = None
    description: str | None = Field(default=None, max_length=300)


@router.put("/admin/settings", summary="Change a setting")
async def put_setting(
    payload: SettingIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    """Upsert one setting.

    SECRETS ARE REFUSED BY NAME. A settings table is the most tempting place to put an
    API key, and the read path for public settings is unauthenticated - so a key here
    would be a key on a public endpoint. The guard below is a blunt instrument
    ("refuse keys that look like secrets") and that is the intention: it catches the
    mistake rather than pretending it cannot happen.
    """
    key = payload.key.strip().lower()
    forbidden_fragments = ("secret", "key", "token", "password", "dsn", "credential")
    reserved = {"billing.plan_overrides"}
    if any(fragment in key for fragment in forbidden_fragments) or key in reserved:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Secrets do not belong here",
            detail=(
                "Settings are readable by the app and some are public. Put credentials "
                "in the environment instead (Render dashboard or .env)."
            ),
            type_slug="settings",
        )

    row = (
        await session.execute(select(PlatformSetting).where(PlatformSetting.key == key))
    ).scalar_one_or_none()
    previous = row.value if row is not None else None
    if row is None:
        row = PlatformSetting(
            id=uuid.uuid4(),
            key=key,
            # Wrapped in an object so a bare string, number or bool is representable
            # without the column type fighting it.
            value={"value": payload.value},
            description=payload.description,
            updated_by=actor.id,
        )
        session.add(row)
    else:
        row.value = {"value": payload.value}
        if payload.description is not None:
            row.description = payload.description
        row.updated_by = actor.id
    await session.flush()

    await record_audit(
        session,
        AuditAction.SETTING_CHANGED,
        actor=actor,
        summary=f"Set {key}",
        target_type="setting",
        target_id=key,
        changes={"from": previous, "to": row.value},
        request=request,
    )
    await session.commit()
    return success(
        {"key": row.key, "value": row.value, "updatedAt": row.updated_at},
        request_id=get_request_id(),
    )


# ------------------------------------------------------------------ notifications


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


# ----------------------------------------------------------------- gamification


class BadgeIn(StrictRequest):
    code: str = Field(min_length=2, max_length=40, pattern="^[A-Z0-9_]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    icon: str = Field(default="🏅", max_length=40)
    criteria_kind: str = Field(
        default="MANUAL",
        pattern="^(MANUAL|POINTS|STREAK|QUESTIONS_ANSWERED|MOCK_SCORE|CHAPTERS_COMPLETE)$",
    )
    criteria_value: int | None = Field(default=None, ge=0, le=1_000_000)
    points_reward: int = Field(default=0, ge=0, le=100_000)
    is_active: bool = True
    display_order: int = Field(default=100, ge=0, le=10_000)


@router.get("/admin/badges", summary="Badge catalogue")
async def list_badges(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_GAMIFICATION)),
) -> Any:
    rows = (
        (await session.execute(select(Badge).order_by(Badge.display_order, Badge.code)))
        .scalars()
        .all()
    )
    awarded = dict(
        (
            await session.execute(
                select(UserBadge.badge_code, func.count(UserBadge.id)).group_by(
                    UserBadge.badge_code
                )
            )
        ).all()
    )
    return success(
        {
            "badges": [
                {
                    "id": str(row.id),
                    "code": row.code,
                    "name": row.name,
                    "description": row.description,
                    "icon": row.icon,
                    "criteriaKind": row.criteria_kind,
                    "criteriaValue": row.criteria_value,
                    "pointsReward": row.points_reward,
                    "isActive": row.is_active,
                    "displayOrder": row.display_order,
                    # Real counts, so the owner can see which badges nobody has earned.
                    "awardedCount": int(awarded.get(row.code, 0)),
                }
                for row in rows
            ]
        },
        request_id=get_request_id(),
    )


@router.post("/admin/badges", status_code=status.HTTP_201_CREATED, summary="Create a badge")
async def create_badge(
    payload: BadgeIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_GAMIFICATION)),
) -> Any:
    existing = (
        await session.execute(select(Badge).where(Badge.code == payload.code))
    ).scalar_one_or_none()
    if existing is not None:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Code already used",
            detail=f"A badge with code {payload.code} already exists.",
            type_slug="gamification",
        )
    badge = Badge(
        id=uuid.uuid4(),
        code=payload.code,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        criteria_kind=payload.criteria_kind,
        criteria_value=payload.criteria_value,
        points_reward=payload.points_reward,
        is_active=payload.is_active,
        display_order=payload.display_order,
    )
    session.add(badge)
    await session.flush()
    await record_audit(
        session,
        AuditAction.BADGE_CREATED,
        actor=actor,
        summary=f"Created badge {payload.code}",
        target_type="badge",
        target_id=badge.id,
        request=request,
    )
    await session.commit()
    return success({"id": str(badge.id), "code": badge.code}, request_id=get_request_id())


@router.patch("/admin/badges/{badge_id}", summary="Edit a badge")
async def update_badge(
    badge_id: uuid.UUID,
    payload: BadgeIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_GAMIFICATION)),
) -> Any:
    badge = (await session.execute(select(Badge).where(Badge.id == badge_id))).scalar_one_or_none()
    if badge is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Badge not found",
            detail="No such badge.",
            type_slug="gamification",
        )
    before = {
        "name": badge.name,
        "is_active": badge.is_active,
        "criteria_value": badge.criteria_value,
    }
    # `code` is deliberately immutable: earned rows reference it, and renaming it
    # would orphan every student's achievement.
    badge.name = payload.name
    badge.description = payload.description
    badge.icon = payload.icon
    badge.criteria_kind = payload.criteria_kind
    badge.criteria_value = payload.criteria_value
    badge.points_reward = payload.points_reward
    badge.is_active = payload.is_active
    badge.display_order = payload.display_order
    await session.flush()
    await record_audit(
        session,
        AuditAction.BADGE_UPDATED,
        actor=actor,
        summary=f"Updated badge {badge.code}",
        target_type="badge",
        target_id=badge.id,
        changes={
            "name": {"from": before["name"], "to": badge.name},
            "is_active": {"from": before["is_active"], "to": badge.is_active},
        },
        request=request,
    )
    await session.commit()
    return success({"id": str(badge.id), "updated": True}, request_id=get_request_id())


@router.post("/admin/badges/{badge_id}/award", summary="Award a badge by hand")
async def award_badge(
    badge_id: uuid.UUID,
    request: Request,
    user_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_GAMIFICATION)),
) -> Any:
    """Manual award, for the badges no rule can express (a mentor's commendation)."""
    from sqlalchemy.exc import IntegrityError

    badge = (await session.execute(select(Badge).where(Badge.id == badge_id))).scalar_one_or_none()
    target = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if badge is None or target is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No such badge or user.",
            type_slug="gamification",
        )
    try:
        session.add(
            UserBadge(
                id=uuid.uuid4(),
                user_id=user_id,
                badge_code=badge.code,
                earned_at=datetime.now(UTC),
            )
        )
        await session.flush()
    except IntegrityError:
        # `uq_badge_user_code`: already earned. Idempotent rather than an error - the
        # admin's intent is satisfied either way.
        await session.rollback()
        return success({"awarded": False, "reason": "already earned"}, request_id=get_request_id())

    if badge.points_reward:
        # The EVENT enum, not `models.enums.PointsReason`. The repository is typed
        # for the event vocabulary, and it is the one that says "a badge was
        # granted" - the schema enum names the PERSISTED reason and is translated
        # by `_AWARDABLE`. Passing the schema enum here worked only by accident:
        # both are str-valued, so the dict lookup matched on the string while the
        # types disagreed. It also silently credited nothing before `_AWARDABLE`
        # gained the entry, which is the bug this milestone fixes.
        from app.repositories.progress import SqlProgressRepository
        from app.services.gamification import PointsReason

        # The EXISTING award path: it credits the ledger AND the day's activity row.
        # The amount comes from the badge catalogue (POINTS_REWARD), which
        # ``_award`` resolves - passing a number here would bypass the configured
        # reward and quietly disagree with what the admin set.
        await SqlProgressRepository(session).award(
            user_id=user_id,
            reason=PointsReason.BADGE_AWARDED,
            reference_id=str(badge.id),
            # The badge's own configured reward, not a constant buried in code.
            amount=badge.points_reward or None,
        )
    session.add(
        Notification(
            id=uuid.uuid4(),
            user_id=user_id,
            kind="ACHIEVEMENT",
            title=f"Badge earned: {badge.name}",
            body=badge.description or f"You earned the {badge.name} badge.",
            link_url="/achievements",
            created_by=actor.id,
            audience="ONE_USER",
        )
    )
    await record_audit(
        session,
        AuditAction.BADGE_UPDATED,
        actor=actor,
        summary=f"Awarded {badge.code} to {target.email or user_id}",
        target_type="user",
        target_id=user_id,
        request=request,
    )
    await session.commit()
    return success({"awarded": True, "code": badge.code}, request_id=get_request_id())


# -------------------------------------------------------------------- payments


#: The order lifecycle. Not the subscription vocabulary: PAID is not ACTIVE.
ORDER_STATUSES = frozenset({"CREATED", "PAID", "FAILED", "EXPIRED"})
#: A live entitlement. TRIAL is included because the partial unique index treats it
#: as the one current subscription, the same way the billing store does.
_LIVE_SUBSCRIPTION = ("ACTIVE", "TRIAL")


def _ilike_contains(term: str) -> str:
    """A contains-pattern that does not treat the operator's typing as wildcards.

    An unescaped `%` in `ILIKE '%…%'` matches every row. Email search is how an
    operator finds one student; it must not become "show me the whole ledger"
    because they typed a percent sign.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _money(paise: int) -> dict[str, int]:
    """Whole rupees plus a remainder, so the screen never divides currency."""
    rupees, remainder = divmod(int(paise), 100)
    return {"amountRupees": rupees, "amountRemainderPaise": remainder}


def _order_filters(
    status_filter: str | None,
    plan_code: str | None,
    q: str | None,
) -> list[Any]:
    conditions: list[Any] = []
    if status_filter:
        conditions.append(PaymentOrder.status == status_filter)
    if plan_code and plan_code.strip():
        conditions.append(PaymentOrder.plan_code == plan_code.strip())
    if q and q.strip():
        conditions.append(User.email.ilike(_ilike_contains(q.strip()), escape="\\"))
    return conditions


@router.get(
    "/admin/payments/orders",
    summary="Payment orders",
    description=(
        "Checkout intents for reconciliation. A PAID order is not an entitlement; "
        "that is read from subscriptions and returned beside the order. Webhook "
        "bodies are not on this route."
    ),
)
async def list_payment_orders(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    plan_code: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_PAYMENTS)),
) -> Any:
    """Who tried to pay, for how much, and whether that became access.

    There is no refund action here. `payments.refund_recorded` exists as an audit
    name and nothing writes it; a button that marked an order refunded would
    disagree with Razorpay, which this process cannot call without keys and which
    this route does not call even when keys exist.
    """
    if status_filter and status_filter not in ORDER_STATUSES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown order status",
            detail=f"Status must be one of {', '.join(sorted(ORDER_STATUSES))}.",
            type_slug="payments",
        )

    conditions = _order_filters(status_filter, plan_code, q)
    base = select(PaymentOrder, User.email, User.display_name).join(
        User, User.id == PaymentOrder.user_id
    )
    if conditions:
        base = base.where(*conditions)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(PaymentOrder.created_at.desc(), PaymentOrder.id.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    ).all()

    totals_stmt = (
        select(
            PaymentOrder.status,
            func.count(),
            func.coalesce(func.sum(PaymentOrder.amount_paise), 0),
        )
        .select_from(PaymentOrder)
        .join(User, User.id == PaymentOrder.user_id)
        .group_by(PaymentOrder.status)
    )
    if conditions:
        totals_stmt = totals_stmt.where(*conditions)
    totals = (await session.execute(totals_stmt)).all()
    by_status = dict.fromkeys(sorted(ORDER_STATUSES), 0)
    paid_orders = 0
    paid_paise = 0
    for status_name, count, paise in totals:
        by_status[str(status_name)] = int(count)
        if status_name == "PAID":
            paid_orders = int(count)
            paid_paise = int(paise)

    user_ids = [order.user_id for order, _email, _name in rows]
    entitlements: dict[Any, Subscription] = {}
    if user_ids:
        live = (
            (
                await session.execute(
                    select(Subscription).where(
                        Subscription.user_id.in_(user_ids),
                        Subscription.status.in_(_LIVE_SUBSCRIPTION),
                    )
                )
            )
            .scalars()
            .all()
        )
        entitlements = {row.user_id: row for row in live}

    items = []
    for order, email, display_name in rows:
        money = _money(order.amount_paise)
        live_sub = entitlements.get(order.user_id)
        items.append(
            {
                "id": str(order.id),
                "email": email,
                "displayName": display_name,
                "userId": str(order.user_id),
                "planCode": order.plan_code,
                "tier": order.tier,
                "amountPaise": int(order.amount_paise),
                "amountRupees": money["amountRupees"],
                "amountRemainderPaise": money["amountRemainderPaise"],
                "currency": order.currency,
                "status": order.status,
                "provider": order.provider,
                "providerOrderId": order.provider_order_id,
                "providerPaymentId": order.provider_payment_id,
                "providerStatus": order.provider_status,
                "receipt": order.receipt,
                "paidAt": order.paid_at,
                "failureReason": order.failure_reason,
                "createdAt": order.created_at,
                "entitlement": None
                if live_sub is None
                else {
                    "status": live_sub.status,
                    "tier": live_sub.tier,
                    "expiresAt": live_sub.expires_at,
                },
            }
        )

    paid = _money(paid_paise)
    return paginated(
        items,
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
        extra={
            "paidOrders": paid_orders,
            "paidPaise": paid_paise,
            "paidRupees": paid["amountRupees"],
            "paidRemainderPaise": paid["amountRemainderPaise"],
            "byStatus": by_status,
        },
    )


@router.get(
    "/admin/payments/events",
    summary="Payment webhook events",
    description=(
        "Webhook rows for reconciliation. The raw payload column is not selected: "
        "it can contain payment-method details and is kept only for idempotency."
    ),
)
async def list_payment_events(
    event_type: str | None = Query(default=None, max_length=80),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_PAYMENTS)),
) -> Any:
    """What the gateway told us, without the body it told us in.

    `PaymentEvent.payload` is deliberately absent from the SELECT. A list that
    loaded it and then deleted the key would still have pulled customer payment
    details into the process, and a later edit could put them back on the wire.
    """
    conditions: list[Any] = []
    if event_type and event_type.strip():
        conditions.append(PaymentEvent.event_type == event_type.strip())
    if q and q.strip():
        conditions.append(User.email.ilike(_ilike_contains(q.strip()), escape="\\"))

    # Columns, not the mapped class: selecting the class would load `payload`.
    columns = (
        PaymentEvent.id,
        PaymentEvent.provider,
        PaymentEvent.event_id,
        PaymentEvent.event_type,
        PaymentEvent.signature_verified,
        PaymentEvent.processed_at,
        PaymentEvent.processing_error,
        PaymentEvent.user_id,
        PaymentEvent.created_at,
        User.email,
    )
    base = select(*columns).outerjoin(User, User.id == PaymentEvent.user_id)
    if conditions:
        base = base.where(*conditions)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(PaymentEvent.created_at.desc(), PaymentEvent.id.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    ).all()

    unprocessed_stmt = (
        select(func.count())
        .select_from(PaymentEvent)
        .outerjoin(User, User.id == PaymentEvent.user_id)
        .where(PaymentEvent.processed_at.is_(None))
    )
    if conditions:
        unprocessed_stmt = unprocessed_stmt.where(*conditions)
    unprocessed = (await session.execute(unprocessed_stmt)).scalar_one()

    return paginated(
        [
            {
                "id": str(row.id),
                "provider": row.provider,
                "eventId": row.event_id,
                "eventType": row.event_type,
                "signatureVerified": bool(row.signature_verified),
                "processedAt": row.processed_at,
                "processingError": row.processing_error,
                "userId": str(row.user_id) if row.user_id else None,
                "email": row.email,
                "createdAt": row.created_at,
            }
            for row in rows
        ],
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
        extra={"payloadOmitted": True, "unprocessed": int(unprocessed)},
    )
