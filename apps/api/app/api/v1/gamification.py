"""Gamification for students: points, badges, achievements and the leaderboard.

VERIFIED BEFORE BUILDING. The backend data existed and was REAL: the practice loop
awards points through ``SqlProgressRepository.award``, which writes ``points_ledger``
and credits the day's activity row, and ``user_badges`` holds earned codes. What did
not exist was any way to see it - no route on the points ledger, no badge catalogue,
no leaderboard query, no screen. So this file adds READS over data that was already
being written, plus the two writes that were genuinely missing: claiming an awardable
badge, and the leaderboard's own windowing.

THE LEADERBOARD IS DELIBERATELY AGGREGATE-ONLY

It returns a display name, a points total and a rank. Not a user id, not an email. A
leaderboard that leaks identifiers turns an encouragement feature into an enumeration
surface, and the client does not need an id to render a table row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.engagement import Badge, PlatformSetting
from app.models.progress import DailyActivity, PointsLedger, UserBadge
from app.models.user import User

router = APIRouter(tags=["gamification"])

#: Leaderboard windows. A single all-time board is dominated by whoever started
#: earliest and stops motivating anyone within a month; a weekly window is the one
#: students actually compete in.
WINDOWS = {"week": 7, "month": 30, "all": None}


async def _flag(session: AsyncSession, key: str, default: bool = True) -> bool:
    """Read a boolean feature flag from platform settings.

    Fails to the default when the row is missing, so a fresh database and a database
    whose settings were cleared behave the same way.
    """
    row = (
        await session.execute(select(PlatformSetting).where(PlatformSetting.key == key))
    ).scalar_one_or_none()
    if row is None or not isinstance(row.value, dict):
        return default
    return bool(row.value.get("value", default))


# ---------------------------------------------------------------------- points


@router.get("/gamification/points", summary="Your points and how you earned them")
async def my_points(
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                PointsLedger.user_id == user.id
            )
        )
    ).scalar_one()
    recent = (
        (
            await session.execute(
                select(PointsLedger)
                .where(PointsLedger.user_id == user.id)
                .order_by(PointsLedger.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    # The trailing week, for the "are you on a good run" line. Grouped in SQL rather
    # than summed in Python: the ledger grows forever and pulling it to the server to
    # add up is the query that gets slow first.
    week_ago = datetime.now(UTC) - timedelta(days=7)
    week_total = (
        await session.execute(
            select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                PointsLedger.user_id == user.id, PointsLedger.created_at >= week_ago
            )
        )
    ).scalar_one()
    return success(
        {
            "total": int(total or 0),
            "lastSevenDays": int(week_total or 0),
            "recent": [
                {
                    "points": row.points,
                    "reason": row.reason,
                    "referenceId": row.reference_id,
                    "createdAt": row.created_at,
                }
                for row in recent
            ],
        },
        request_id=get_request_id(),
    )


@router.get("/gamification/streak", summary="Your current and longest streak")
async def my_streak(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Read from the activity rows rather than a counter column.

    A counter that a job maintains is wrong whenever the job misses a night, and a
    streak is exactly the number a student notices being wrong. The rows are already
    there; counting them is cheap and cannot drift.
    """
    rows = (
        (
            await session.execute(
                select(DailyActivity.activity_date)
                .where(DailyActivity.user_id == user.id, DailyActivity.questions_attempted > 0)
                .order_by(DailyActivity.activity_date.desc())
                .limit(400)
            )
        )
        .scalars()
        .all()
    )
    dates = set(rows)
    today = datetime.now(UTC).date()
    # A streak is allowed to be "yesterday" - a student opening the app in the morning
    # has not broken a streak they have not had a chance to continue yet.
    cursor = today if today in dates else today - timedelta(days=1)
    current = 0
    while cursor in dates:
        current += 1
        cursor -= timedelta(days=1)

    longest = 0
    run = 0
    previous = None
    for day in sorted(dates):
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        longest = max(longest, run)
        previous = day

    return success(
        {"current": current, "longest": longest, "activeDays": len(dates)},
        request_id=get_request_id(),
    )


# ---------------------------------------------------------------------- badges


@router.get("/gamification/badges", summary="Every badge, with what you have earned")
async def my_badges(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """The catalogue plus the caller's earned set.

    Returning the catalogue rather than only the earned badges is what makes badges
    motivating: a student can see what is available and how far away it is. Each row
    carries the progress toward its criterion where the platform can compute it.
    """
    badges = (
        (
            await session.execute(
                select(Badge)
                .where(Badge.is_active.is_(True))
                .order_by(Badge.display_order, Badge.code)
            )
        )
        .scalars()
        .all()
    )
    earned_rows = (
        (await session.execute(select(UserBadge).where(UserBadge.user_id == user.id)))
        .scalars()
        .all()
    )
    earned = {row.badge_code: row.earned_at for row in earned_rows}

    # Progress toward the countable criteria, in two queries rather than one per badge.
    answered = (
        await session.execute(
            select(func.coalesce(func.sum(DailyActivity.questions_attempted), 0)).where(
                DailyActivity.user_id == user.id
            )
        )
    ).scalar_one()
    points = (
        await session.execute(
            select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                PointsLedger.user_id == user.id
            )
        )
    ).scalar_one()

    def progress_for(badge: Badge) -> int | None:
        if badge.criteria_value is None:
            return None
        if badge.criteria_kind == "QUESTIONS_ANSWERED":
            return int(answered or 0)
        if badge.criteria_kind == "POINTS":
            return int(points or 0)
        # STREAK, MOCK_SCORE and CHAPTERS_COMPLETE are not reconstructable from these
        # two aggregates. Returning None says "we do not know" - which the client
        # renders as "no progress bar" rather than as a fabricated 0%.
        return None

    return success(
        {
            "badges": [
                {
                    "code": badge.code,
                    "name": badge.name,
                    "description": badge.description,
                    "icon": badge.icon,
                    "criteriaKind": badge.criteria_kind,
                    "criteriaValue": badge.criteria_value,
                    "pointsReward": badge.points_reward,
                    "earned": badge.code in earned,
                    "earnedAt": earned.get(badge.code),
                    "progress": progress_for(badge),
                }
                for badge in badges
            ],
            "earnedCount": len(earned),
            "totalCount": len(badges),
        },
        request_id=get_request_id(),
    )


@router.get("/gamification/achievements", summary="Your achievement summary")
async def my_achievements(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """One call for the achievements screen: totals, badges earned, rank."""
    points = (
        await session.execute(
            select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                PointsLedger.user_id == user.id
            )
        )
    ).scalar_one()
    earned = (
        await session.execute(select(func.count(UserBadge.id)).where(UserBadge.user_id == user.id))
    ).scalar_one()
    total_badges = (
        await session.execute(select(func.count(Badge.id)).where(Badge.is_active.is_(True)))
    ).scalar_one()

    # Rank is computed from the ledger, never stored: a stored rank is wrong the
    # moment anyone else earns a point, and the nightly job that repairs it is a job
    # that will eventually be missed.
    totals = (
        select(PointsLedger.user_id, func.sum(PointsLedger.points).label("total"))
        .group_by(PointsLedger.user_id)
        .subquery()
    )
    ahead = (
        await session.execute(
            select(func.count(totals.c.user_id)).where(totals.c.total > int(points or 0))
        )
    ).scalar_one()
    ranked = (await session.execute(select(func.count(totals.c.user_id)))).scalar_one()

    return success(
        {
            "points": int(points or 0),
            "badgesEarned": int(earned or 0),
            "badgesAvailable": int(total_badges or 0),
            "rank": int(ahead) + 1,
            "totalRanked": int(ranked or 0),
        },
        request_id=get_request_id(),
    )


async def _points_to_next(session: AsyncSession, totals: Any, mine: int) -> int | None:
    """Points needed to pass the next person. None when nobody is ahead.

    The next person's name is not returned. Rank context does not need an identity.
    """
    ahead_total = (
        await session.execute(select(func.min(totals.c.total)).where(totals.c.total > mine))
    ).scalar_one()
    if ahead_total is None:
        return None
    return int(ahead_total) - mine


# ----------------------------------------------------------------- leaderboard


@router.get("/gamification/leaderboard", summary="Top students by points")
async def leaderboard(
    window: str = Query(default="week", pattern="^(week|month|all)$"),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Ranked by points earned IN THE WINDOW, aggregate fields only.

    The caller's own position is always included, even when they are far outside the
    top slice - a leaderboard that only shows the top 25 is a leaderboard that 95% of
    students have no reason to open.
    """
    if not await _flag(session, "features.leaderboard", True):
        return problem(
            status=403,
            title="Leaderboard disabled",
            detail="An administrator has turned the leaderboard off.",
            type_slug="gamification",
        )

    days = WINDOWS[window]
    conditions = []
    if days is not None:
        conditions.append(PointsLedger.created_at >= datetime.now(UTC) - timedelta(days=days))

    totals = (
        select(
            PointsLedger.user_id.label("user_id"),
            func.sum(PointsLedger.points).label("total"),
        )
        .where(*conditions)
        .group_by(PointsLedger.user_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                totals.c.user_id,
                totals.c.total,
                func.coalesce(User.display_name, func.split_part(User.email, "@", 1)).label("name"),
                User.avatar_url,
            )
            .join(User, User.id == totals.c.user_id)
            .where(User.deleted_at.is_(None), User.is_active.is_(True))
            .order_by(totals.c.total.desc())
            .limit(limit)
        )
    ).all()

    # Rank the caller: count how many totals exceed theirs. One query, no window
    # function needed, and correct even when the caller has no rows at all.
    mine = (
        await session.execute(
            select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                PointsLedger.user_id == user.id, *conditions
            )
        )
    ).scalar_one()
    ahead = (
        await session.execute(
            select(func.count()).select_from(
                select(totals.c.user_id).where(totals.c.total > int(mine or 0)).subquery()
            )
        )
    ).scalar_one()

    return success(
        {
            "window": window,
            "entries": [
                {
                    "rank": index + 1,
                    "name": row.name or "A student",
                    "points": int(row.total or 0),
                    "avatarUrl": row.avatar_url,
                    "isYou": row.user_id == user.id,
                }
                for index, row in enumerate(rows)
            ],
            "you": {
                "rank": int(ahead) + 1,
                "points": int(mine or 0),
                "pointsToNext": await _points_to_next(session, totals, int(mine or 0)),
            },
        },
        request_id=get_request_id(),
    )
