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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.core.permissions import Permission, require_permission
from app.models.campus import (
    MentorshipRequest,
    StudyGroup,
    StudyGroupMember,
)
from app.models.user import User
from app.schemas.base import StrictRequest

from ._shared import (
    _uuid,
)

router = APIRouter(tags=["campus"])

"""Study groups and mentorship."""


class GroupIn(StrictRequest):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class JoinIn(StrictRequest):
    group_id: str = Field(min_length=32, max_length=36)


class MentorshipIn(StrictRequest):
    topic: str = Field(min_length=2, max_length=120)
    note: str = Field(min_length=2, max_length=1000)


class MentorAssignIn(StrictRequest):
    mentor_id: str | None = Field(default=None, max_length=36)
    status: str = Field(pattern="^(OPEN|MATCHED|CLOSED)$")


@router.get("/campus/groups", summary="Study groups, without email addresses")
async def list_groups(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (await session.execute(select(StudyGroup).order_by(StudyGroup.created_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(StudyGroupMember.group_id, func.count()).group_by(StudyGroupMember.group_id)
            )
        ).all()
    )
    mine = set(
        (
            await session.execute(
                select(StudyGroupMember.group_id).where(StudyGroupMember.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "groups": [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "description": row.description,
                    "memberCount": int(counts.get(row.id, 0)),
                    "joined": row.id in mine,
                }
                for row in rows
            ],
            "note": "Member lists show display names only. Email addresses are not returned.",
        },
        request_id=get_request_id(),
    )


@router.post("/campus/groups", summary="Create a study group")
async def create_group(
    payload: GroupIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    group = StudyGroup(
        id=uuid.uuid4(),
        name=payload.name.strip(),
        description=payload.description,
        created_by=user.id,
    )
    session.add(group)
    session.add(StudyGroupMember(id=uuid.uuid4(), group_id=group.id, user_id=user.id, role="OWNER"))
    await session.commit()
    return success({"id": str(group.id), "name": group.name}, request_id=get_request_id())


@router.post("/campus/groups/join", summary="Join a study group")
async def join_group(
    payload: JoinIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    group_id = _uuid(payload.group_id)
    if group_id is None or await session.get(StudyGroup, group_id) is None:
        return problem(
            status=404,
            title="Group not found",
            detail="That group does not exist.",
            type_slug="campus",
        )
    session.add(
        StudyGroupMember(id=uuid.uuid4(), group_id=group_id, user_id=user.id, role="MEMBER")
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return success({"joined": True, "alreadyMember": True}, request_id=get_request_id())
    return success({"joined": True, "alreadyMember": False}, request_id=get_request_id())


@router.get("/campus/groups/{group_id}/members", summary="Display names in a group")
async def group_members(
    group_id: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    parsed = _uuid(group_id)
    if parsed is None:
        return problem(
            status=404,
            title="Group not found",
            detail="That group does not exist.",
            type_slug="campus",
        )
    member = (
        await session.execute(
            select(StudyGroupMember).where(
                StudyGroupMember.group_id == parsed, StudyGroupMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        return problem(
            status=403,
            title="Not a member",
            detail="Join the group before reading its member list.",
            type_slug="campus",
        )
    rows = (
        await session.execute(
            select(User.display_name, StudyGroupMember.role)
            .join(StudyGroupMember, StudyGroupMember.user_id == User.id)
            .where(StudyGroupMember.group_id == parsed)
            .order_by(StudyGroupMember.created_at)
        )
    ).all()
    return success(
        {
            "members": [{"displayName": name or "Student", "role": role} for name, role in rows],
            "emailsIncluded": False,
        },
        request_id=get_request_id(),
    )


@router.post("/campus/mentorship", summary="Ask for a mentor")
async def request_mentor(
    payload: MentorshipIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    row = MentorshipRequest(
        id=uuid.uuid4(),
        student_id=user.id,
        topic=payload.topic.strip(),
        note=payload.note.strip(),
        status="OPEN",
    )
    session.add(row)
    await session.commit()
    return success(
        {
            "id": str(row.id),
            "status": "OPEN",
            "mentorAssigned": False,
            "note": "The request is saved. Nobody has been assigned, and no message was sent.",
        },
        request_id=get_request_id(),
    )


@router.get("/campus/mentorship", summary="Your mentorship requests")
async def my_mentorship(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(MentorshipRequest)
                .where(MentorshipRequest.student_id == user.id)
                .order_by(MentorshipRequest.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "requests": [
                {
                    "id": str(row.id),
                    "topic": row.topic,
                    "status": row.status,
                    "mentorAssigned": row.mentor_id is not None,
                }
                for row in rows
            ]
        },
        request_id=get_request_id(),
    )


@router.get("/admin/mentorship", summary="Open mentorship requests")
async def admin_mentorship(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> Any:
    rows = (
        (
            await session.execute(
                select(MentorshipRequest).order_by(MentorshipRequest.created_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "requests": [
                {
                    "id": str(row.id),
                    "topic": row.topic,
                    "status": row.status,
                    "mentorAssigned": row.mentor_id is not None,
                }
                for row in rows
            ],
            "note": "Student emails are not included. Assign a mentor by id if you already know the account.",
        },
        request_id=get_request_id(),
    )


@router.patch("/admin/mentorship/{request_id}", summary="Assign a mentor or close a request")
async def assign_mentor(
    request_id: str,
    payload: MentorAssignIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> Any:
    parsed = _uuid(request_id)
    row = await session.get(MentorshipRequest, parsed) if parsed else None
    if row is None:
        return problem(
            status=404,
            title="Request not found",
            detail="That request does not exist.",
            type_slug="campus",
        )
    mentor_id = _uuid(payload.mentor_id) if payload.mentor_id else None
    if payload.status == "MATCHED" and mentor_id is None:
        return problem(
            status=422,
            title="Mentor required",
            detail="MATCHED needs a mentor id. This route does not pick one for you.",
            type_slug="campus",
        )
    if mentor_id is not None and await session.get(User, mentor_id) is None:
        return problem(
            status=404,
            title="Mentor not found",
            detail="That user does not exist.",
            type_slug="campus",
        )
    row.mentor_id = mentor_id
    row.status = payload.status
    await session.commit()
    return success(
        {"id": str(row.id), "status": row.status, "messageSent": False},
        request_id=get_request_id(),
    )
