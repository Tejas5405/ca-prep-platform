"""Baseline platform rows: feature-flag defaults and the badge catalogue.

WHY THIS EXISTS SEPARATELY FROM THE MIGRATION

Migration ``0008`` inserts these rows, and that is the production path - a fresh
database comes up with a working settings screen and a badge catalogue. But two other
situations need the same rows and cannot use SQL in a migration:

  * a database whose settings were cleared by hand (the owner deleting a flag row
    should not be able to break the feature that reads it);
  * the test suite, which TRUNCATES every table between tests for isolation. A test
    that asserts "the eight default badges are visible" would fail against a truncated
    database for a reason that has nothing to do with the code under test.

So the defaults are DATA in one module, and ``ensure_platform_defaults`` writes them
idempotently. The migration's SQL is a copy of this list - they must be kept in step,
and the test ``test_the_seeded_defaults_match_what_the_api_serves`` fails if they
drift, which is the only reliable way to keep two writers of the same rows honest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import Badge, PlatformSetting


@dataclass(frozen=True)
class SettingDefault:
    key: str
    value: Any
    description: str
    is_public: bool


@dataclass(frozen=True)
class BadgeDefault:
    code: str
    name: str
    description: str
    icon: str
    criteria_kind: str
    criteria_value: int | None
    points_reward: int
    display_order: int


#: Feature flags and learning defaults. NOTHING SECRET: the public subset is served to
#: anonymous clients, so a credential here would be a credential on a public endpoint.
#: The API refuses a key containing "secret", "key", "token" or "password" for exactly
#: that reason - see ``admin.put_setting``.
DEFAULT_SETTINGS: tuple[SettingDefault, ...] = (
    SettingDefault("platform.name", "CA Prep", "Shown in the app header and emails.", True),
    SettingDefault(
        "platform.support_email",
        "support@caprep.in",
        "Shown on the privacy and terms pages.",
        True,
    ),
    SettingDefault(
        "features.ai_assistant",
        False,
        (
            "When on, students can ask the library. Answers are quotations from documents "
            "they may read. A generated explanation is not called."
        ),
        True,
    ),
    SettingDefault(
        "features.ai_content_generation",
        False,
        "Master switch for generating questions from uploaded material.",
        False,
    ),
    SettingDefault("features.leaderboard", True, "Show the points leaderboard to students.", True),
    SettingDefault("features.gamification", True, "Points, badges and streaks.", True),
    SettingDefault(
        "features.registration_open",
        True,
        "When false, only existing users can sign in.",
        True,
    ),
    SettingDefault(
        "learning.free_question_quota",
        50,
        "Questions a free student may attempt per day.",
        True,
    ),
    SettingDefault(
        "learning.default_daily_goal_minutes",
        120,
        "Default daily study goal for a new account.",
        True,
    ),
    SettingDefault(
        "payments.enabled",
        True,
        "Show the upgrade flow. Actual charges need Razorpay keys in the environment.",
        True,
    ),
)


#: THE BADGE CATALOGUE. ``user_badges`` stored a code with no table saying what codes
#: exist, so this is the missing half: what each code means, how it is earned, and what
#: it is worth. Criteria the platform can evaluate automatically use a ``criteria_kind``
#: with a threshold; MANUAL is for the ones only a human can judge.
DEFAULT_BADGES: tuple[BadgeDefault, ...] = (
    BadgeDefault(
        "FIRST_STEPS",
        "First Steps",
        "Answer your first question.",
        "🎯",
        "QUESTIONS_ANSWERED",
        1,
        5,
        10,
    ),
    BadgeDefault(
        "CENTURY", "Century", "Answer 100 questions.", "💯", "QUESTIONS_ANSWERED", 100, 50, 20
    ),
    BadgeDefault(
        "GRINDER", "Grinder", "Answer 1000 questions.", "⚙️", "QUESTIONS_ANSWERED", 1000, 250, 30
    ),
    BadgeDefault(
        "WEEK_STREAK",
        "Seven Days Straight",
        "Study seven days in a row.",
        "🔥",
        "STREAK",
        7,
        40,
        40,
    ),
    BadgeDefault(
        "MONTH_STREAK",
        "A Month Unbroken",
        "Study thirty days in a row.",
        "🏔️",
        "STREAK",
        30,
        150,
        50,
    ),
    BadgeDefault(
        "MOCK_FINISHER", "Sat The Paper", "Complete a full mock exam.", "📝", "MANUAL", None, 30, 60
    ),
    BadgeDefault(
        "DISTINCTION",
        "Distinction",
        "Score 75% or more in a mock exam.",
        "🏆",
        "MOCK_SCORE",
        75,
        100,
        70,
    ),
    BadgeDefault("POINTS_1000", "Thousand Club", "Earn 1000 points.", "💎", "POINTS", 1000, 0, 80),
)


async def ensure_platform_defaults(session: AsyncSession) -> dict[str, int]:
    """Insert any missing baseline row. Idempotent, and safe to call on every boot.

    Existing rows are LEFT ALONE: an owner who turned the AI assistant off must not
    have it switched back on by a restart, which is what an upsert would do.
    """
    created_settings = 0
    created_badges = 0

    existing_keys = set((await session.execute(select(PlatformSetting.key))).scalars())
    for default in DEFAULT_SETTINGS:
        if default.key in existing_keys:
            continue
        session.add(
            PlatformSetting(
                id=uuid.uuid4(),
                key=default.key,
                value={"value": default.value},
                description=default.description,
                is_public=default.is_public,
            )
        )
        created_settings += 1

    existing_codes = set((await session.execute(select(Badge.code))).scalars())
    for badge in DEFAULT_BADGES:
        if badge.code in existing_codes:
            continue
        session.add(
            Badge(
                id=uuid.uuid4(),
                code=badge.code,
                name=badge.name,
                description=badge.description,
                icon=badge.icon,
                criteria_kind=badge.criteria_kind,
                criteria_value=badge.criteria_value,
                points_reward=badge.points_reward,
                display_order=badge.display_order,
                is_active=True,
            )
        )
        created_badges += 1

    await session.flush()
    return {"settings": created_settings, "badges": created_badges}
