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
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import (
    Permission,
    require_permission,
)
from app.models.engagement import Badge, Notification
from app.models.progress import UserBadge
from app.models.user import User
from app.schemas.base import StrictRequest
from app.services.audit import AuditAction, record_audit

router = APIRouter(tags=["admin"])

"""Badge catalogue and awarding."""


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
