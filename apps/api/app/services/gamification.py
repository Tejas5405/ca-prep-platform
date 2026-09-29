"""Points, levels, streaks, badges and referrals.

CRITICAL: points are EARNED ENTITLEMENTS and live in PostgreSQL (append-only
ledger), never in Redis. Blueprint v3 §10 states the rule for quotas
("Premium entitlement checks should use PostgreSQL subscription state and
invalidate cached entitlement when the subscription changes") and lists
``leaderboard:{period}`` as a Redis key with a 5-15 minute TTL - Redis holds the
leaderboard INDEX, not the balance.

A Redis flush must never delete a student's progress.

Money is in integer paise throughout. Floating-point rupee arithmetic produces
off-by-one-paise credit balances that are tedious to reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

# ---------------------------------------------------------------- points


class PointsReason(str, Enum):
    """EVENT vocabulary - what HAPPENED, in the service layer.

    NOT the persisted contract. That is `app.models.enums.PointsReason`, which
    mirrors the `ck_ledger_reason` CHECK constraint exactly. Only
    `QUESTION_CORRECT` appears in both, and the two lists share nothing else, so
    they are different vocabularies that happen to share a name. `_AWARDABLE` in
    `app/repositories/progress.py` translates between them.

    The seven members with no mapping (`DAILY_LOGIN`, `QUESTION_ATTEMPTED`, the
    four `DOUBT_*` events, `REFERRAL_ACTIVATED`) are deliberately in-memory only:
    they are scored by `POINTS` and used in `LedgerEntry`, but nothing writes them
    to `points_ledger`. Persisting any of them needs a migration and is a separate
    decision.
    """

    DAILY_LOGIN = "DAILY_LOGIN"
    QUESTION_ATTEMPTED = "QUESTION_ATTEMPTED"
    QUESTION_CORRECT = "QUESTION_CORRECT"
    MOCK_COMPLETED = "MOCK_COMPLETED"
    DOUBT_ANSWERED = "DOUBT_ANSWERED"
    DOUBT_ANSWER_ACCEPTED = "DOUBT_ANSWER_ACCEPTED"
    DOUBT_ASKED_QUALITY = "DOUBT_ASKED_QUALITY"
    DOUBT_RESOLVED = "DOUBT_RESOLVED"
    STREAK_7 = "STREAK_7"
    DAILY_CHALLENGE_CORRECT = "DAILY_CHALLENGE_CORRECT"
    REFERRAL_ACTIVATED = "REFERRAL_ACTIVATED"
    # Persisted reasons with no fixed value: a badge's reward is configured by an
    # admin and an adjustment is whatever the admin chose, so both arrive as
    # `amount=` and never read POINTS. They live here so `_AWARDABLE` can name
    # them and so the ledger's reason vocabulary is expressible in one enum.
    BADGE_AWARDED = "BADGE_AWARDED"
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"


POINTS: dict[PointsReason, int] = {
    PointsReason.DAILY_LOGIN: 5,
    PointsReason.QUESTION_ATTEMPTED: 2,
    PointsReason.QUESTION_CORRECT: 10,
    PointsReason.MOCK_COMPLETED: 50,
    PointsReason.DOUBT_ANSWERED: 10,
    PointsReason.DOUBT_ANSWER_ACCEPTED: 50,
    PointsReason.DOUBT_ASKED_QUALITY: 5,
    PointsReason.DOUBT_RESOLVED: 15,
    PointsReason.STREAK_7: 100,
    PointsReason.DAILY_CHALLENGE_CORRECT: 20,
    PointsReason.REFERRAL_ACTIVATED: 50,
}


@dataclass(frozen=True)
class LedgerEntry:
    user_id: str
    reason: PointsReason
    #: attempt id | doubt id | challenge id | date string - scopes idempotency
    ref_id: str | None = None


def ledger_key(entry: LedgerEntry) -> str:
    """Idempotency key.

    Mirrors the database constraint
    ``UNIQUE (user_id, reason, ref_id)``, so a retried RQ job cannot
    double-credit. This is the same discipline the blueprint requires for
    Razorpay webhooks (§13.1: "Treat webhook handling as idempotent").
    """
    return f"{entry.user_id}::{entry.reason.value}::{entry.ref_id or '-'}"


@dataclass(frozen=True)
class AwardResult:
    awarded: bool
    amount: int
    duplicate_of: str | None = None


def award(entry: LedgerEntry, existing_keys: set[str]) -> AwardResult:
    """Pure dedup check. The database unique constraint is the real enforcement."""
    key = ledger_key(entry)
    if key in existing_keys:
        return AwardResult(awarded=False, amount=0, duplicate_of=key)
    return AwardResult(awarded=True, amount=POINTS[entry.reason])


# ---------------------------------------------------------------- levels

LEVELS: tuple[tuple[str, int], ...] = (
    ("BEGINNER", 0),
    ("INTERMEDIATE", 500),
    ("ADVANCED", 2000),
    ("EXPERT", 5000),
    ("MASTER", 10000),
)


def level_rank(points: int) -> int:
    """The 1-based position of the earned level in ``LEVELS``.

    TWO REPRESENTATIONS OF "LEVEL" EXIST, and they are not interchangeable:

      * ``level_for`` / ``LevelProgress.level``   the NAME  ("BEGINNER")
      * this function                             the RANK  (1)

    ``user_profiles.current_level`` is a SmallInteger, so it takes the rank. It was
    being assigned the name, which raised
    ``ValueError: invalid literal for int() with base 10: 'BEGINNER'`` on the first
    correct answer - a crash inside the answer path, found only by running the
    suite against a real database with a real column type.
    """
    # Mirrors level_for exactly - last threshold met wins - so the name and the
    # rank can never disagree about which level a student is on. BEGINNER is 1.
    rank = 1
    for index, (_, threshold) in enumerate(LEVELS):
        if points >= threshold:
            rank = index + 1
    return rank


def level_for(points: int) -> str:
    name = LEVELS[0][0]
    for level_name, threshold in LEVELS:
        if points >= threshold:
            name = level_name
    return name


@dataclass(frozen=True)
class LevelProgress:
    level: str
    next_level: str | None
    points_to_next: int
    progress: float


def level_progress(points: int) -> LevelProgress:
    level = level_for(points)
    index = next(i for i, (n, _) in enumerate(LEVELS) if n == level)
    if index + 1 >= len(LEVELS):
        return LevelProgress(level, None, 0, 1.0)

    floor = LEVELS[index][1]
    next_name, next_min = LEVELS[index + 1]
    span = next_min - floor
    return LevelProgress(
        level=level,
        next_level=next_name,
        points_to_next=next_min - points,
        progress=round((points - floor) / span, 3),
    )


# ---------------------------------------------------------------- streaks


@dataclass(frozen=True)
class StreakState:
    current: int = 0
    longest: int = 0
    last_active_at: date | None = None


def apply_activity(state: StreakState, activity_date: date) -> StreakState:
    """Streak transition rules, stated explicitly.

    same day        -> unchanged (idempotent; many sessions count once)
    next day        -> +1
    2+ day gap      -> reset to 1
    earlier date    -> unchanged (out-of-order processing must not break it)
    """
    last = state.last_active_at
    if last is None:
        return StreakState(1, max(1, state.longest), activity_date)

    gap = (activity_date - last).days
    if gap <= 0:
        return state

    if gap == 1:
        current = state.current + 1
        return StreakState(current, max(current, state.longest), activity_date)

    return StreakState(1, max(1, state.longest), activity_date)


# ---------------------------------------------------------------- badges


class BadgeKind(str, Enum):
    STREAK = "STREAK"
    VOLUME = "VOLUME"
    ACCURACY = "ACCURACY"
    COMMUNITY = "COMMUNITY"
    PERFECT = "PERFECT"


@dataclass(frozen=True)
class BadgeDef:
    code: str
    name: str
    kind: BadgeKind
    threshold: int


BADGES: tuple[BadgeDef, ...] = (
    BadgeDef("STREAK_7", "7-day Streak Warrior", BadgeKind.STREAK, 7),
    BadgeDef("STREAK_30", "30-day Streak", BadgeKind.STREAK, 30),
    BadgeDef("STREAK_100", "100-day Streak", BadgeKind.STREAK, 100),
    BadgeDef("VOLUME_100_DAY", "100 Questions in a Day", BadgeKind.VOLUME, 100),
    BadgeDef("ACCURACY_90", "Subject Master", BadgeKind.ACCURACY, 90),
    BadgeDef("PERFECT_MOCK", "Perfect Mock Test", BadgeKind.PERFECT, 100),
    BadgeDef("COMMUNITY_50", "Community Helper", BadgeKind.COMMUNITY, 50),
)


@dataclass(frozen=True)
class UserStats:
    streak_current: int = 0
    questions_in_day: int = 0
    subject_accuracy_pct: int = 0
    mock_score_pct: int = 0
    accepted_answers: int = 0


def evaluate_badges(stats: UserStats, owned: set[str]) -> list[BadgeDef]:
    values = {
        BadgeKind.STREAK: stats.streak_current,
        BadgeKind.VOLUME: stats.questions_in_day,
        BadgeKind.ACCURACY: stats.subject_accuracy_pct,
        BadgeKind.PERFECT: stats.mock_score_pct,
        BadgeKind.COMMUNITY: stats.accepted_answers,
    }
    return [b for b in BADGES if b.code not in owned and values[b.kind] >= b.threshold]


# ---------------------------------------------------------------- anti-abuse


def accepted_answer_points(answer_author_id: str, doubt_author_id: str, actor_id: str) -> int:
    """Self-acceptance awards nothing - closes the sock-puppet farming loop."""
    if answer_author_id == doubt_author_id:
        return 0
    if actor_id == answer_author_id:
        return 0
    return POINTS[PointsReason.DOUBT_ANSWER_ACCEPTED]


# ---------------------------------------------------------------- referrals

CREDIT_PAISE_PER_ACTIVATION = 5_000  # Rs 50
TIER_THRESHOLDS: tuple[int, ...] = (1, 3, 5, 10)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0, 1
CODE_TTL_DAYS = 90


class ReferralStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVATED = "ACTIVATED"
    SUBSCRIBED = "SUBSCRIBED"
    EXPIRED = "EXPIRED"


def generate_code(name: str, seed: int, length: int = 6) -> str:
    prefix = "".join(c for c in name.upper() if c.isalpha())[:3]
    out: list[str] = []
    x = abs(int(seed)) or 1
    while len(out) < length:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(CODE_ALPHABET[x % len(CODE_ALPHABET)])
    return (prefix + "".join(out))[: max(6, len(prefix) + 3)]


@dataclass(frozen=True)
class ActivationResult:
    credit_paise: int
    tier_bonus_months: int
    awarded: bool
    reason: str | None = None


def activate_referral(
    *,
    referrer_id: str,
    referee_id: str,
    current_status: ReferralStatus,
    new_status: ReferralStatus,
    prior_activations: int,
) -> ActivationResult:
    """Idempotent activation.

    A replayed Razorpay webhook must not credit twice, and credit is tied to a
    paid conversion rather than a bare signup.
    """
    if referrer_id == referee_id:
        return ActivationResult(0, 0, False, "SELF_REFERRAL")
    if new_status in (ReferralStatus.PENDING, ReferralStatus.EXPIRED):
        return ActivationResult(0, 0, False, "UNKNOWN_STATUS")
    if current_status in (ReferralStatus.ACTIVATED, ReferralStatus.SUBSCRIBED):
        return ActivationResult(0, 0, False, "ALREADY_ACTIVATED")
    if new_status is not ReferralStatus.SUBSCRIBED:
        # ACTIVATED alone (signup) earns nothing.
        return ActivationResult(0, 0, True)

    total = prior_activations + 1
    tier_bonus_months = 3 if total == 5 else 0
    return ActivationResult(CREDIT_PAISE_PER_ACTIVATION, tier_bonus_months, True)


@dataclass(frozen=True)
class CreditBalance:
    balance_paise: int = 0
    lifetime_earned_paise: int = 0
    lifetime_spent_paise: int = 0


def apply_credit(balance: CreditBalance, delta_paise: int) -> CreditBalance:
    next_balance = balance.balance_paise + delta_paise
    if next_balance < 0:
        raise ValueError("credit balance cannot go negative")
    return replace(
        balance,
        balance_paise=next_balance,
        lifetime_earned_paise=balance.lifetime_earned_paise
        + (delta_paise if delta_paise > 0 else 0),
        lifetime_spent_paise=balance.lifetime_spent_paise
        + (-delta_paise if delta_paise < 0 else 0),
    )


def format_inr(paise: int) -> str:
    rupees = paise / 100
    if rupees == int(rupees):
        return f"\u20b9{int(rupees):,}"
    return f"\u20b9{rupees:,.2f}"
