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
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import (
    Permission,
    require_permission,
)
from app.core.security import ROLE_RANK, Role
from app.models.progress import UserBadge, UserQuestionProgress
from app.models.user import Subscription, User
from app.schemas.base import StrictRequest
from app.services.analytics import Event
from app.services.audit import AuditAction, record_audit
from app.services.entitlements import entitlements_for

from ._shared import (
    ASSIGNABLE_ROLES,
    _record,
)

router = APIRouter(tags=["admin"])

"""Listing, reading and editing users, including role changes."""


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
    settings: Settings = Depends(get_settings),
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

            # `settings` is the first positional argument and must be passed: without
            # it the call raises TypeError, the `except` below swallows it, and
            # `claimUpdated` is reported False on every role change - the Supabase
            # claim silently never follows the row. `async with` because
            # SupabaseAuthAdmin only closes the httpx client it builds in
            # `__aexit__`. Phase 4 fixed this in the pre-split admin.py; the same
            # call moved here in the Phase 5 split.
            async with SupabaseAuthAdmin(settings) as admin:
                claim_updated = await admin.set_role_claim(user.auth_user_id, str(values["role"]))
        except Exception:  # noqa: BLE001
            claim_updated = False

    return success(
        {**_user_payload(user), "claimUpdated": claim_updated}, request_id=get_request_id()
    )
