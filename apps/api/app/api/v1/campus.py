"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.core.permissions import Permission, require_permission
from app.models.campus import (
    CalendarEvent,
    ExamModeSitting,
    Experiment,
    ExperimentAssignment,
    FormulaEntry,
    ForumPost,
    ForumThread,
    GlossaryEntry,
    MarketplaceListing,
    MentorshipRequest,
    PomodoroSession,
    ProctorEvent,
    StudyGroup,
    StudyGroupMember,
    SupportTicket,
)
from app.models.engagement import AnalyticsEvent
from app.models.progress import MockAttempt, Referral
from app.models.question import LawNotice, Question, QuestionFlag, QuestionOption
from app.models.user import User
from app.repositories.practice import grade
from app.repositories.progress import SqlProgressRepository
from app.schemas.base import StrictRequest
from app.services.audit import AuditAction, record_audit
from app.services.gamification import PointsReason

router = APIRouter(tags=["campus"])

_CHALLENGE = "campus.challenge_answered"
_REVIEW_STATES = {
    "CURRENT",
    "VERIFICATION_REQUIRED",
    "NEEDS_UPDATE",
    "PENDING_ADMIN_REVIEW",
}
_PROCTOR = {"TAB_HIDDEN", "TAB_VISIBLE", "WINDOW_BLUR", "COPY_ATTEMPT", "PASTE_ATTEMPT"}
_DISCUSSION = "Student discussion. Not an ICAI ruling and not a model answer."
_NOTE = "An editor's study note. Not an ICAI extract and not a statute."


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


class ThreadIn(StrictRequest):
    title: str = Field(min_length=2, max_length=140)
    body: str = Field(min_length=2, max_length=2000)
    subject_label: str | None = Field(default=None, max_length=80)


class PostIn(StrictRequest):
    body: str = Field(min_length=2, max_length=2000)


class FormulaIn(StrictRequest):
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=2, max_length=2000)
    subject_label: str | None = Field(default=None, max_length=80)
    published: bool = False


class GlossaryIn(StrictRequest):
    term: str = Field(min_length=2, max_length=120)
    definition: str = Field(min_length=2, max_length=1000)
    published: bool = False


class TicketIn(StrictRequest):
    subject: str = Field(min_length=2, max_length=140)
    body: str = Field(min_length=2, max_length=2000)


class TicketStatusIn(StrictRequest):
    status: str = Field(pattern="^(OPEN|ACKNOWLEDGED|CLOSED)$")


class ExperimentIn(StrictRequest):
    key: str = Field(min_length=2, max_length=60, pattern="^[a-z0-9_]+$")
    description: str = Field(min_length=2, max_length=500)


class ExperimentActiveIn(StrictRequest):
    active: bool


class ListingIn(StrictRequest):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=2, max_length=1000)
    plan_code: str | None = Field(default=None, max_length=40)
    price_paise: int | None = Field(default=None, ge=0, le=10_000_000)


class CalendarIn(StrictRequest):
    title: str = Field(min_length=2, max_length=140)
    starts_at: datetime
    ends_at: datetime | None = None


class PomodoroIn(StrictRequest):
    minutes: int = Field(ge=1, le=120)
    completed: bool = False


class ExamModeIn(StrictRequest):
    attempt_id: str = Field(min_length=32, max_length=36)


class ProctorIn(StrictRequest):
    attempt_id: str = Field(min_length=32, max_length=36)
    event_type: str = Field(min_length=4, max_length=32)


class ChallengeIn(StrictRequest):
    chosen_option: str = Field(min_length=1, max_length=8)


class RedeemIn(StrictRequest):
    code: str = Field(min_length=4, max_length=16)


class ReviewStateIn(StrictRequest):
    review_state: str = Field(min_length=7, max_length=32)


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _day_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


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


@router.get("/campus/formulas", summary="Published study notes")
async def list_formulas(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(FormulaEntry)
                .where(FormulaEntry.published.is_(True))
                .order_by(FormulaEntry.title)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "entries": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "body": row.body,
                    "subjectLabel": row.subject_label,
                }
                for row in rows
            ],
            "authority": "editor_note",
            "note": _NOTE,
        },
        request_id=get_request_id(),
    )


@router.post("/admin/formulas", summary="Add a study note")
async def create_formula(
    payload: FormulaIn,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    row = FormulaEntry(
        id=uuid.uuid4(),
        title=payload.title.strip(),
        body=payload.body.strip(),
        subject_label=payload.subject_label,
        published=payload.published,
        created_by=actor.id,
    )
    session.add(row)
    await session.commit()
    return success(
        {"id": str(row.id), "published": row.published, "authority": "editor_note", "note": _NOTE},
        request_id=get_request_id(),
    )


@router.get("/campus/glossary", summary="Published glossary")
async def list_glossary(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(GlossaryEntry)
                .where(GlossaryEntry.published.is_(True))
                .order_by(GlossaryEntry.term)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "entries": [
                {"term": row.term, "definition": row.definition, "source": row.source}
                for row in rows
            ],
            "note": "Platform entries describe this product. Editor entries are notes, not statutes.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/glossary", summary="Add a glossary entry")
async def create_glossary(
    payload: GlossaryIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    row = GlossaryEntry(
        id=uuid.uuid4(),
        term=payload.term.strip(),
        definition=payload.definition.strip(),
        source="editor",
        published=payload.published,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=409,
            title="Term already exists",
            detail="That glossary term is already saved.",
            type_slug="campus",
        )
    return success({"id": str(row.id), "source": "editor"}, request_id=get_request_id())


@router.post("/campus/support", summary="Save a support ticket")
async def create_ticket(
    payload: TicketIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    row = SupportTicket(
        id=uuid.uuid4(),
        user_id=user.id,
        subject=payload.subject.strip(),
        body=payload.body.strip(),
        status="OPEN",
        email_sent=False,
    )
    session.add(row)
    await session.commit()
    return success(
        {
            "id": str(row.id),
            "status": "OPEN",
            "emailSent": False,
            "note": "Saved for an operator to read. No email was sent.",
        },
        request_id=get_request_id(),
    )


@router.get("/campus/support", summary="Your support tickets")
async def my_tickets(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(SupportTicket)
                .where(SupportTicket.user_id == user.id)
                .order_by(SupportTicket.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "tickets": [
                {
                    "id": str(row.id),
                    "subject": row.subject,
                    "status": row.status,
                    "emailSent": False,
                }
                for row in rows
            ]
        },
        request_id=get_request_id(),
    )


@router.get("/admin/support", summary="Support queue")
async def admin_tickets(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> Any:
    rows = (
        (
            await session.execute(
                select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "tickets": [
                {
                    "id": str(row.id),
                    "subject": row.subject,
                    "body": row.body,
                    "status": row.status,
                    "emailSent": False,
                }
                for row in rows
            ],
            "note": "No email is sent from this queue.",
        },
        request_id=get_request_id(),
    )


@router.patch("/admin/support/{ticket_id}", summary="Change a ticket status")
async def update_ticket(
    ticket_id: str,
    payload: TicketStatusIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> Any:
    parsed = _uuid(ticket_id)
    row = await session.get(SupportTicket, parsed) if parsed else None
    if row is None:
        return problem(
            status=404,
            title="Ticket not found",
            detail="That ticket does not exist.",
            type_slug="campus",
        )
    row.status = payload.status
    await session.commit()
    return success(
        {"id": str(row.id), "status": row.status, "emailSent": False}, request_id=get_request_id()
    )


@router.get("/campus/experiments/{key}", summary="Your variant, if an experiment is active")
async def experiment_variant(
    key: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    experiment = (
        await session.execute(select(Experiment).where(Experiment.key == key))
    ).scalar_one_or_none()
    if experiment is None or not experiment.active:
        return success(
            {
                "key": key,
                "active": False,
                "variant": None,
                "note": "No active experiment. Nothing was assigned.",
            },
            request_id=get_request_id(),
        )
    existing = (
        await session.execute(
            select(ExperimentAssignment).where(
                ExperimentAssignment.experiment_id == experiment.id,
                ExperimentAssignment.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    variants = [str(item) for item in (experiment.variants or ["A", "B"]) if str(item)]
    if not variants:
        variants = ["A", "B"]
    if existing is None:
        digest = hashlib.sha256(f"{experiment.key}:{user.id}".encode()).hexdigest()
        variant = variants[int(digest[:8], 16) % len(variants)]
        existing = ExperimentAssignment(
            id=uuid.uuid4(), experiment_id=experiment.id, user_id=user.id, variant=variant
        )
        session.add(existing)
        await session.commit()
    return success(
        {
            "key": experiment.key,
            "active": True,
            "variant": existing.variant,
            "note": "A stored assignment. It does not measure a conversion.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/experiments", summary="Create an inactive experiment")
async def create_experiment(
    payload: ExperimentIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    row = Experiment(
        id=uuid.uuid4(),
        key=payload.key,
        description=payload.description.strip(),
        active=False,
        variants=["A", "B"],
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=409,
            title="Key exists",
            detail="That experiment key is already used.",
            type_slug="campus",
        )
    return success(
        {"id": str(row.id), "key": row.key, "active": False}, request_id=get_request_id()
    )


@router.patch("/admin/experiments/{key}", summary="Turn an experiment on or off")
async def set_experiment(
    key: str,
    payload: ExperimentActiveIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    row = (
        await session.execute(select(Experiment).where(Experiment.key == key))
    ).scalar_one_or_none()
    if row is None:
        return problem(
            status=404,
            title="Experiment not found",
            detail="That key does not exist.",
            type_slug="campus",
        )
    row.active = payload.active
    await session.commit()
    return success({"key": row.key, "active": row.active}, request_id=get_request_id())


@router.get("/campus/marketplace", summary="Listings that do not take payment")
async def list_marketplace(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(MarketplaceListing)
                .where(MarketplaceListing.active.is_(True))
                .order_by(MarketplaceListing.title)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "listings": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "description": row.description,
                    "planCode": row.plan_code,
                    "pricePaise": row.price_paise,
                    "takesPayment": False,
                }
                for row in rows
            ],
            "note": "A listing does not create an order. Paid plans are bought on the upgrade page.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/marketplace", summary="Add a listing that cannot charge")
async def create_listing(
    payload: ListingIn,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_PLANS)),
) -> Any:
    row = MarketplaceListing(
        id=uuid.uuid4(),
        title=payload.title.strip(),
        description=payload.description.strip(),
        plan_code=payload.plan_code,
        price_paise=payload.price_paise,
        active=True,
        takes_payment=False,
        created_by=actor.id,
    )
    session.add(row)
    await session.commit()
    return success(
        {"id": str(row.id), "takesPayment": False, "orderCreated": False},
        request_id=get_request_id(),
    )


@router.get("/campus/calendar", summary="Events you added")
async def list_calendar(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(CalendarEvent)
                .where(CalendarEvent.user_id == user.id)
                .order_by(CalendarEvent.starts_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "events": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "startsAt": row.starts_at.isoformat(),
                    "endsAt": row.ends_at.isoformat() if row.ends_at else None,
                    "source": "student",
                }
                for row in rows
            ],
            "synced": False,
            "note": "These events are stored here. They are not synced to Google or ICAI.",
        },
        request_id=get_request_id(),
    )


@router.post("/campus/calendar", summary="Add an event")
async def create_calendar_event(
    payload: CalendarIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    starts = (
        payload.starts_at if payload.starts_at.tzinfo else payload.starts_at.replace(tzinfo=UTC)
    )
    ends = payload.ends_at
    if ends is not None and ends.tzinfo is None:
        ends = ends.replace(tzinfo=UTC)
    row = CalendarEvent(
        id=uuid.uuid4(),
        user_id=user.id,
        title=payload.title.strip(),
        starts_at=starts,
        ends_at=ends,
        source="student",
    )
    session.add(row)
    await session.commit()
    return success({"id": str(row.id), "synced": False}, request_id=get_request_id())


@router.post("/campus/pomodoro", summary="Record a timer you ran")
async def record_pomodoro(
    payload: PomodoroIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    row = PomodoroSession(
        id=uuid.uuid4(),
        user_id=user.id,
        minutes=payload.minutes,
        completed=payload.completed,
        reported_by_client=True,
    )
    session.add(row)
    await session.commit()
    return success(
        {
            "id": str(row.id),
            "minutes": row.minutes,
            "verified": False,
            "note": "Recorded as you reported it. The server did not time the session.",
        },
        request_id=get_request_id(),
    )


@router.get("/campus/pomodoro", summary="Timers you reported")
async def list_pomodoro(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(PomodoroSession)
                .where(PomodoroSession.user_id == user.id)
                .order_by(PomodoroSession.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "sessions": [
                {
                    "id": str(row.id),
                    "minutes": row.minutes,
                    "completed": row.completed,
                    "verified": False,
                }
                for row in rows
            ],
            "note": "Self-reported. Not a verified study log.",
        },
        request_id=get_request_id(),
    )


@router.post("/campus/exam-mode", summary="Mark a mock as an exam sitting")
async def start_exam_mode(
    payload: ExamModeIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    attempt_id = _uuid(payload.attempt_id)
    attempt = await session.get(MockAttempt, attempt_id) if attempt_id else None
    if attempt is None or attempt.user_id != user.id:
        return problem(
            status=404,
            title="Attempt not found",
            detail="Start a mock paper first. Exam mode attaches to that attempt.",
            type_slug="campus",
        )
    existing = (
        await session.execute(
            select(ExamModeSitting).where(ExamModeSitting.attempt_id == attempt.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            ExamModeSitting(
                id=uuid.uuid4(),
                user_id=user.id,
                attempt_id=attempt.id,
                hides_answers_until_submit=True,
                camera_used=False,
            )
        )
        await session.commit()
    return success(
        {
            "attemptId": str(attempt.id),
            "hidesAnswersUntilSubmit": True,
            "cameraUsed": False,
            "locksBrowser": False,
            "note": (
                "The mock route already withholds answers until you submit. "
                "Exam mode records that you sat it that way. It does not open a camera "
                "or lock the browser."
            ),
        },
        request_id=get_request_id(),
    )


@router.post("/campus/proctor-events", summary="Record a browser signal")
async def record_proctor_event(
    payload: ProctorIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    if payload.event_type not in _PROCTOR:
        return problem(
            status=422,
            title="Unknown event",
            detail="Only tab and clipboard signals are recorded. There is no camera event.",
            type_slug="campus",
        )
    attempt_id = _uuid(payload.attempt_id)
    attempt = await session.get(MockAttempt, attempt_id) if attempt_id else None
    if attempt is None or attempt.user_id != user.id:
        return problem(
            status=404,
            title="Attempt not found",
            detail="That attempt is not yours.",
            type_slug="campus",
        )
    session.add(
        ProctorEvent(
            id=uuid.uuid4(),
            user_id=user.id,
            attempt_id=attempt.id,
            event_type=payload.event_type,
        )
    )
    await session.commit()
    return success(
        {"recorded": True, "blocked": False, "cameraUsed": False},
        request_id=get_request_id(),
    )


async def _todays_question(session: AsyncSession) -> Question | None:
    rows = (
        (
            await session.execute(
                select(Question)
                .where(
                    Question.status == "PUBLISHED",
                    Question.question_type == "MCQ",
                    Question.deleted_at.is_(None),
                )
                .order_by(Question.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    return rows[datetime.now(UTC).date().toordinal() % len(rows)]


@router.get("/campus/challenge", summary="Today's published question")
async def daily_challenge(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    question = await _todays_question(session)
    if question is None:
        return success(
            {
                "available": False,
                "question": None,
                "note": "No published MCQ is in the bank, so no challenge was invented.",
            },
            request_id=get_request_id(),
        )
    options = (
        await session.execute(
            select(QuestionOption.label, QuestionOption.text)
            .where(QuestionOption.question_id == question.id)
            .order_by(QuestionOption.label)
        )
    ).all()
    answered = (
        await session.execute(
            select(AnalyticsEvent.properties).where(
                AnalyticsEvent.user_id == user.id,
                AnalyticsEvent.name == _CHALLENGE,
                AnalyticsEvent.created_at >= _day_start(),
            )
        )
    ).scalar_one_or_none()
    return success(
        {
            "available": True,
            "question": {
                "id": str(question.id),
                "text": question.text,
                "options": [{"label": label, "text": text} for label, text in options],
            },
            "alreadyAnswered": answered is not None,
            "correctAnswerIncluded": False,
        },
        request_id=get_request_id(),
    )


@router.post("/campus/challenge", summary="Answer today's question once")
async def answer_challenge(
    payload: ChallengeIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    prior = (
        await session.execute(
            select(AnalyticsEvent).where(
                AnalyticsEvent.user_id == user.id,
                AnalyticsEvent.name == _CHALLENGE,
                AnalyticsEvent.created_at >= _day_start(),
            )
        )
    ).scalar_one_or_none()
    if prior is not None:
        props = prior.properties or {}
        return success(
            {
                "alreadyAnswered": True,
                "isCorrect": props.get("isCorrect"),
                "pointsAwarded": 0,
                "note": "Today's challenge is already answered. Points are not awarded twice.",
            },
            request_id=get_request_id(),
        )
    question = await _todays_question(session)
    if question is None:
        return problem(
            status=404,
            title="No challenge",
            detail="No published MCQ is in the bank.",
            type_slug="campus",
        )
    chosen = payload.chosen_option.strip().upper()
    correct = grade(question, chosen)
    points = 0
    if correct is True:
        points = await SqlProgressRepository(session).award(
            user_id=user.id,
            reason=PointsReason.DAILY_CHALLENGE_CORRECT,
            reference_id=datetime.now(UTC).date().isoformat(),
        )
    session.add(
        AnalyticsEvent(
            id=uuid.uuid4(),
            name=_CHALLENGE,
            user_id=user.id,
            role=getattr(user, "role", None),
            properties={"questionId": str(question.id), "isCorrect": correct, "chosen": chosen},
        )
    )
    await session.commit()
    return success(
        {
            "alreadyAnswered": False,
            "isCorrect": correct,
            "pointsAwarded": points,
            "correctAnswer": question.correct_answer if correct is not None else None,
            "note": "Marked against the published key. This is not a new ruling.",
        },
        request_id=get_request_id(),
    )


async def _ensure_code(session: AsyncSession, user: User) -> str:
    if user.referral_code:
        return user.referral_code
    for _ in range(5):
        code = secrets.token_hex(4).upper()
        user.referral_code = code
        try:
            await session.commit()
            return code
        except IntegrityError:
            await session.rollback()
            await session.refresh(user)
    raise RuntimeError("could not allocate a referral code")


@router.get("/campus/referrals", summary="Your referral code and counts")
async def referral_status(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    code = await _ensure_code(session, user)
    signups = (
        await session.execute(
            select(func.count()).select_from(Referral).where(Referral.referrer_user_id == user.id)
        )
    ).scalar_one()
    converted = (
        await session.execute(
            select(func.count())
            .select_from(Referral)
            .where(Referral.referrer_user_id == user.id, Referral.converted_at.is_not(None))
        )
    ).scalar_one()
    return success(
        {
            "code": code,
            "signups": int(signups),
            "conversions": int(converted),
            "premiumGranted": False,
            "note": "Sharing the code records a signup. It does not grant Premium.",
        },
        request_id=get_request_id(),
    )


@router.post("/campus/referrals/redeem", summary="Record a referral without granting Premium")
async def redeem_referral(
    payload: RedeemIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    code = payload.code.strip().upper()
    referrer = (
        await session.execute(select(User).where(User.referral_code == code))
    ).scalar_one_or_none()
    if referrer is None:
        return problem(
            status=404,
            title="Code not found",
            detail="That referral code does not exist.",
            type_slug="campus",
        )
    if referrer.id == user.id:
        return problem(
            status=422,
            title="Own code",
            detail="You cannot redeem your own code.",
            type_slug="campus",
        )
    existing = (
        await session.execute(select(Referral).where(Referral.referred_user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        return success(
            {"recorded": True, "alreadyRecorded": True, "premiumGranted": False},
            request_id=get_request_id(),
        )
    session.add(
        Referral(
            id=uuid.uuid4(),
            referrer_user_id=referrer.id,
            referred_user_id=user.id,
            code_used=code,
            signed_up_at=datetime.now(UTC),
            converted_at=None,
            reward_granted=False,
        )
    )
    await session.commit()
    return success(
        {
            "recorded": True,
            "alreadyRecorded": False,
            "premiumGranted": False,
            "note": "The relationship is saved. Premium is not granted until a paid conversion, and this route does not record one.",
        },
        request_id=get_request_id(),
    )


@router.get("/admin/review-queue", summary="Questions waiting on a review state")
async def review_queue(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    rows = (
        await session.execute(
            select(Question.id, Question.review_state, Question.status, Question.text)
            .where(Question.review_state != "CURRENT")
            .order_by(Question.updated_at.desc())
            .limit(100)
        )
    ).all()
    return success(
        {
            "questions": [
                {
                    "id": str(row.id),
                    "reviewState": row.review_state,
                    "status": row.status,
                    "excerpt": (row.text or "")[:240],
                }
                for row in rows
            ],
            "answersChanged": False,
            "note": "A review state does not publish a question and does not change its answer.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/review-queue/run", summary="Mark questions that need another look")
async def reverify(
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    """Set review states from open flags and law notices. Do not touch answers."""
    flags = (
        await session.execute(
            select(QuestionFlag.question_id, QuestionFlag.reason).where(
                QuestionFlag.status == "OPEN"
            )
        )
    ).all()
    needs_update: set[uuid.UUID] = set()
    pending: set[uuid.UUID] = set()
    for question_id, reason in flags:
        if reason == "HISTORICAL":
            needs_update.add(question_id)
        else:
            pending.add(question_id)
    notices = (await session.execute(select(LawNotice.matched_question_ids))).scalars().all()
    for raw in notices:
        for item in raw or []:
            parsed = _uuid(str(item))
            if parsed is not None:
                needs_update.add(parsed)
    pending -= needs_update
    marked_update = 0
    marked_pending = 0
    if needs_update:
        result = await session.execute(
            update(Question)
            .where(Question.id.in_(needs_update), Question.review_state != "NEEDS_UPDATE")
            .values(review_state="NEEDS_UPDATE")
        )
        marked_update = result.rowcount or 0
    if pending:
        result = await session.execute(
            update(Question)
            .where(Question.id.in_(pending), Question.review_state == "CURRENT")
            .values(review_state="PENDING_ADMIN_REVIEW")
        )
        marked_pending = result.rowcount or 0
    await record_audit(
        session,
        AuditAction.QUESTION_REVIEWED,
        actor=actor,
        summary=f"Review states marked: {marked_update} need update, {marked_pending} pending",
        target_type="question",
        target_id="reverify",
        changes={
            "needsUpdate": marked_update,
            "pendingReview": marked_pending,
            "answersChanged": False,
        },
    )
    await session.commit()
    return success(
        {
            "needsUpdate": marked_update,
            "pendingReview": marked_pending,
            "answersChanged": False,
            "publishedAutomatically": False,
            "note": "Open historical flags and law-notice matches are NEEDS_UPDATE. Other open flags are PENDING_ADMIN_REVIEW. Answers were not rewritten.",
        },
        request_id=get_request_id(),
    )


@router.patch("/admin/questions/{question_id}/review-state", summary="Set a review state")
async def set_review_state(
    question_id: str,
    payload: ReviewStateIn,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    if payload.review_state not in _REVIEW_STATES:
        return problem(
            status=422,
            title="Unknown review state",
            detail="Use CURRENT, VERIFICATION_REQUIRED, NEEDS_UPDATE, or PENDING_ADMIN_REVIEW.",
            type_slug="campus",
        )
    parsed = _uuid(question_id)
    question = await session.get(Question, parsed) if parsed else None
    if question is None:
        return problem(
            status=404,
            title="Question not found",
            detail="That question does not exist.",
            type_slug="campus",
        )
    question.review_state = payload.review_state
    await record_audit(
        session,
        AuditAction.QUESTION_REVIEWED,
        actor=actor,
        summary=f"Review state set to {payload.review_state}",
        target_type="question",
        target_id=str(question.id),
        changes={"reviewState": payload.review_state, "answersChanged": False},
    )
    await session.commit()
    return success(
        {
            "id": str(question.id),
            "reviewState": question.review_state,
            "status": question.status,
            "answersChanged": False,
        },
        request_id=get_request_id(),
    )
