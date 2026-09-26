"""Progress endpoints - what the student has done, and what to do next.

ONE ENDPOINT, NOT SIX. The dashboard needs totals, a subject breakdown, the weak
and strong chapters, the level and the recent activity together, and it needs them
in one render. Six round trips would be six spinners on one screen, and they would
not even agree with each other: each would read the database at a slightly
different moment.

The per-question detail is deliberately NOT here. A student asking "how did I do
on question 47" is asking a different question from "where am I", and mixing them
makes the common case slow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import success
from app.core.identity import get_current_user
from app.models.progress import PracticeAttempt
from app.models.user import User
from app.repositories.progress import SqlProgressRepository
from app.repositories.revision import SqlRevisionRepository

router = APIRouter(tags=["progress"])


@router.get("/progress/overview", summary="Dashboard: totals, weak areas, activity")
async def overview(
    days: int = 14,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Everything the dashboard renders, in one response."""
    progress = SqlProgressRepository(session)

    totals = await progress.totals(user.id)
    subject_rows = await progress.subject_breakdown(user.id)
    chapter_stats = await progress.chapter_stats(user.id)
    focus, strong = progress.focus_and_strong(chapter_stats)
    activity = await progress.recent_activity(user.id, days=min(120, max(1, days)))
    profile = await progress.profile_snapshot(user.id)
    revision = await SqlRevisionRepository(session).stats(user_id=user.id)

    return success(
        {
            "totals": {
                "attempts": totals["attempts"],
                "correct": totals["correct"],
                "pendingReview": totals["pendingReview"],
                "accuracy": totals["accuracy"],
            },
            "bySubject": subject_rows,
            "focusAreas": focus,
            "strongAreas": strong,
            "profile": profile,
            "recentActivity": activity,
            "revision": revision,
        },
        request_id=get_request_id(),
    )


@router.get("/progress/projection", summary="Recent practice accuracy, not an exam prediction")
async def projection(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Compare the last 14 days of graded practice with the 14 before that.

    Fewer than five graded attempts is not enough to report a rate. The response
    never contains a predicted ICAI mark. ``examScore`` stays null on purpose.
    """
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(PracticeAttempt.is_correct, PracticeAttempt.created_at).where(
                PracticeAttempt.user_id == user.id,
                PracticeAttempt.created_at >= now - timedelta(days=28),
                PracticeAttempt.is_correct.is_not(None),
            )
        )
    ).all()
    recent_cut = now - timedelta(days=14)
    recent = [flag for flag, created in rows if created >= recent_cut]
    previous = [flag for flag, created in rows if created < recent_cut]

    def _rate(flags: list[bool]) -> float | None:
        if not flags:
            return None
        return round(sum(1 for flag in flags if flag) / len(flags), 3)

    enough = len(rows) >= 5
    return success(
        {
            "enoughData": enough,
            "gradedAttempts": len(rows),
            "recentAccuracy": _rate(recent) if enough else None,
            "previousAccuracy": _rate(previous) if enough else None,
            "examScore": None,
            "note": (
                "This is recent practice accuracy, not a predicted ICAI mark."
                if enough
                else "Fewer than five graded attempts in 28 days. No rate was invented."
            ),
        },
        request_id=get_request_id(),
    )
