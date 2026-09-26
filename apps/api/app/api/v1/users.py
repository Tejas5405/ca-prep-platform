"""The signed-in student's own profile.

``GET /me`` is the endpoint the app calls once per session to answer "who am I,
what am I entitled to, and where was I". It exists so the dashboard does not have
to assemble that from three endpoints that can disagree.

PROVISIONING HAPPENS HERE. ``get_current_user`` creates the row on the first
authenticated request from a new Supabase account, so the first screen after
sign-in is a working screen rather than a 404 - and the values come from the
VERIFIED token, never from the request body.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user, is_staff
from app.core.permissions import Permission, require_permission
from app.core.security import Role
from app.integrations.supabase_auth import SupabaseAuthAdmin
from app.models.user import User, UserProfile
from app.schemas.base import DateRef, StrictRequest
from app.services.entitlements import entitlements_for

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])

TARGET_LEVELS = {"FOUNDATION", "INTERMEDIATE", "FINAL"}
#: 15 minutes is a real daily habit; 16 hours is not a goal, it is a typo.
MIN_DAILY_GOAL = 15
MAX_DAILY_GOAL = 960


class UpdateProfileIn(StrictRequest):
    """Every field optional: PATCH means "change these", not "replace everything".

    ``None`` is not distinguishable from "leave it" here - a student clearing their
    exam date sends null, which is a legitimate value for a field with no default.
    That is why the fields are applied only when present via ``model_fields_set``.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    target_level: str | None = Field(default=None, max_length=20)
    target_exam_date: DateRef | None = None
    daily_goal_minutes: int | None = Field(default=None, ge=MIN_DAILY_GOAL, le=MAX_DAILY_GOAL)
    timezone: str | None = Field(default=None, max_length=64)
    syllabus_scheme: str | None = Field(default=None, max_length=20)
    college: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    attempt_number: int | None = Field(default=None, ge=1, le=10)


def _payload(
    user: User, profile: UserProfile | None, entitlements: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "displayName": user.display_name,
        "role": user.role,
        "isStaff": is_staff(user),
        "targetLevel": user.target_level,
        "targetExamDate": user.target_exam_date.isoformat() if user.target_exam_date else None,
        "syllabusScheme": user.syllabus_scheme,
        "dailyGoalMinutes": user.daily_goal_minutes,
        "timezone": user.timezone,
        "referralCode": user.referral_code,
        "profile": {
            "college": profile.college if profile else None,
            "city": profile.city if profile else None,
            "attemptNumber": profile.attempt_number if profile else None,
            "currentStreak": profile.current_streak if profile else 0,
            "longestStreak": profile.longest_streak if profile else 0,
            "totalPoints": profile.total_points if profile else 0,
            "currentLevel": profile.current_level if profile else 1,
        },
        "entitlements": entitlements,
    }


@router.get("/me", summary="The signed-in student's profile")
async def read_me(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    profile = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    ).scalar_one_or_none()
    return success(
        _payload(user, profile, (await entitlements_for(session, user.id)).as_payload()),
        request_id=get_request_id(),
    )


@router.patch("/me", summary="Update the signed-in student's profile")
async def update_me(
    payload: UpdateProfileIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Update study context: target level, exam date, daily goal, timezone.

    None of these are privileges, so a student may set them freely. Role is absent
    from this schema on purpose - self-service role changes are how a student
    becomes an editor.
    """
    changed = payload.model_fields_set

    if payload.target_level is not None:
        if payload.target_level.upper() not in TARGET_LEVELS:
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Unknown level",
                detail=f"targetLevel must be one of {sorted(TARGET_LEVELS)}.",
                type_slug="users",
            )
        user.target_level = payload.target_level.upper()

    if "target_exam_date" in changed:
        # Explicitly nullable: clearing the date is how a student says "I have not
        # decided yet", and that is a legitimate answer at sign-up.
        user.target_exam_date = payload.target_exam_date

    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.daily_goal_minutes is not None:
        user.daily_goal_minutes = payload.daily_goal_minutes
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.syllabus_scheme is not None:
        user.syllabus_scheme = payload.syllabus_scheme

    profile = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user.id)
        session.add(profile)

    if payload.college is not None:
        profile.college = payload.college
    if payload.city is not None:
        profile.city = payload.city
    if payload.attempt_number is not None:
        profile.attempt_number = payload.attempt_number

    await session.commit()
    return success(
        _payload(user, profile, (await entitlements_for(session, user.id)).as_payload()),
        request_id=get_request_id(),
    )


# ============================================================== role assignment
#
# WHY THIS IS NOT PART OF `PATCH /me`
#
# Role is absent from `UpdateProfileIn` on purpose: self-service role changes are
# how a student becomes an editor. Writing it needs a second endpoint that a
# student cannot reach, and an authorization check that reads the caller's role
# from the TOKEN rather than the request.

#: Roles a caller may assign through this endpoint.
#:
#: SUPER_ADMIN is deliberately NOT assignable here. It is the role that can grant
#: every other role, so making it reachable from an HTTP endpoint turns one
#: compromised admin session into a permanent escalation that no other admin can
#: undo. It is granted out of band, by the same allow-list mechanism that
#: bootstraps the first admin.
ASSIGNABLE_ROLES = {role.value for role in Role if role is not Role.SUPER_ADMIN}


class AssignRoleIn(StrictRequest):
    """The role to grant. One field, and it must be a known role."""

    role: str = Field(min_length=1, max_length=20)
    reason: str | None = Field(default=None, max_length=500)


@router.post(
    "/users/{user_id}/role",
    summary="Grant or revoke a staff role",
    description=(
        "Requires MANAGE_ROLES (Admin and above). Updates the platform row AND the "
        "Supabase Auth role claim. The permission check reads the ROW, so a demotion "
        "takes effect on the next request; the claim write is what the client's token "
        "and the Supabase-side row show, and the two are reported separately so a "
        "promotion that has not propagated is not reported as done."
    ),
)
async def assign_role(
    user_id: uuid.UUID,
    payload: AssignRoleIn,
    session: AsyncSession = Depends(get_db),
    caller: User = Depends(require_permission(Permission.MANAGE_ROLES)),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Two writes, and why neither alone is enough.

    The DATABASE ROW decides what the person IS: it is what ``is_staff`` reads for
    staff-only branches, what audit columns point at, and the record that survives
    every token refresh - and it is what ``require_permission`` reads, so this row
    write is the one that takes effect immediately. The CLAIM is what the person's
    existing token carries and what the Supabase dashboard shows; writing it is what
    makes the change visible without a fresh sign-in.

    Writing only the row is the "promoted but locked out" bug. Writing only the
    claim is worse: the permission exists in a token and nowhere else, so it
    disappears at the next sign-in and nothing in the database records that it was
    ever granted.
    """
    role = payload.role.upper()
    if role not in ASSIGNABLE_ROLES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown role",
            detail=f"role must be one of {sorted(ASSIGNABLE_ROLES)}.",
            type_slug="users",
            errors=[{"field": "role", "message": "unknown role"}],
        )

    target = await session.get(User, user_id)
    if target is None or target.deleted_at is not None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="User not found",
            detail=f"No active user with id {user_id}",
            type_slug="not-found",
        )

    # AN ADMIN MUST NOT DEMOTE THEMSELVES.
    #
    # Not paternalism: with a small staff, the most common way to lose the ability
    # to assign roles at all is the last admin demoting themselves while tidying
    # up, and the recovery path is a database edit by the person who just locked
    # themselves out. There is no second admin to undo it.
    #
    # THE COMPARISON IS ROW TO ROW, and both sides are platform rows now.
    # `principal.auth_user_id` would be the Supabase user
    # id and `target.id` is this platform's primary key - two different id spaces
    # that are both UUIDs, so comparing them directly is a check that silently never
    # fires. `caller` is the resolved row for the token that made the request.
    if target.id == caller.id and role != Role.ADMIN.value:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Cannot change your own role",
            detail=(
                "An administrator cannot demote themselves. Ask another admin to do "
                "it - the alternative is a staff team with nobody who can assign "
                "roles."
            ),
            type_slug="conflict",
        )

    previous = target.role
    target.role = role
    await session.commit()

    # The claim write happens AFTER the row commits, and its failure is reported
    # rather than rolled back into the row change: the two live in different
    # systems and cannot be made atomic, so the honest outcome is "the row changed,
    # the claim did not, here is what to do next".
    async with SupabaseAuthAdmin(settings) as admin:
        claim_updated = await admin.set_role_claim(target.auth_user_id, role)

    logger.info(
        "role change user=%s auth_user_id=%s %s -> %s by=%s claim=%s",
        target.id,
        target.auth_user_id,
        previous,
        role,
        caller.auth_user_id,
        claim_updated,
    )

    return success(
        {
            "userId": str(target.id),
            "email": target.email,
            "previousRole": previous,
            "role": role,
            # The field the caller must not ignore. False means the row is updated
            # and the token still carries the old role, so the person cannot use
            # their new permissions until the claim is written.
            "claimUpdated": claim_updated,
            "note": (
                None
                if claim_updated
                else (
                    "The role is recorded, but the Supabase role claim was not "
                    "written because no secret key is configured here. Set "
                    "SUPABASE_SECRET_KEY and repeat this call, or the user's token "
                    "will keep the previous role."
                )
            ),
        },
        request_id=get_request_id(),
    )
