"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.campus import (
    CalendarEvent,
    ExamModeSitting,
    PomodoroSession,
    ProctorEvent,
)
from app.models.progress import MockAttempt
from app.models.user import User
from app.schemas.base import StrictRequest

from ._shared import (
    _PROCTOR,
    _uuid,
)

router = APIRouter(tags=["campus"])

"""Calendar, pomodoro and proctored exam mode."""


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
