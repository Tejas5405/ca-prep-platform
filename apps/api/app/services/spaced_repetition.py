"""SuperMemo SM-2 spaced repetition algorithm.

Ported from the superseded TypeScript implementation; the formulas are unchanged
because they are the published SM-2 algorithm, not a house invention.

`box` (Leitner) is DERIVED from the interval for UI display and is never used as
scheduling input. The interval and ease factor are the source of truth; if `box`
were authoritative, a UI slider could corrupt the schedule.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

MIN_EASE_FACTOR = 1.3
DEFAULT_EASE_FACTOR = 2.5

#: 0 = complete blackout, 5 = perfect recall.
Quality = int

IST_OFFSET_MINUTES = 330


@dataclass(frozen=True)
class ReviewState:
    ease_factor: float = DEFAULT_EASE_FACTOR
    interval_days: int = 0
    repetitions: int = 0
    lapses: int = 0


@dataclass(frozen=True)
class ReviewResult:
    ease_factor: float
    interval_days: int
    repetitions: int
    lapses: int
    box: int
    lapsed: bool


def initial_review_state() -> ReviewState:
    return ReviewState()


def next_ease_factor(current: float, quality: Quality) -> float:
    """EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)), floored at 1.30."""
    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    return max(MIN_EASE_FACTOR, round(current + delta, 2))


def review(state: ReviewState, quality: Quality) -> ReviewResult:
    """Apply one review.

    Pure function - no clock, no I/O - so it is trivially testable and safe to
    run inside a database transaction.
    """
    if not 0 <= quality <= 5:
        raise ValueError(f"quality must be an integer 0..5, got {quality!r}")

    lapsed = quality < 3
    ease_factor = next_ease_factor(state.ease_factor, quality)

    if lapsed:
        repetitions = 0
        interval_days = 1
    elif state.repetitions == 0:
        repetitions = 1
        interval_days = 1
    elif state.repetitions == 1:
        repetitions = 2
        interval_days = 6
    else:
        repetitions = state.repetitions + 1
        interval_days = max(1, round(state.interval_days * ease_factor))

    return ReviewResult(
        ease_factor=ease_factor,
        interval_days=interval_days,
        repetitions=repetitions,
        lapses=state.lapses + (1 if lapsed else 0),
        box=leitner_box(interval_days),
        lapsed=lapsed,
    )


def apply_review(state: ReviewState, quality: Quality) -> ReviewState:
    """Convenience wrapper returning the next persisted state."""
    r = review(state, quality)
    return replace(
        state,
        ease_factor=r.ease_factor,
        interval_days=r.interval_days,
        repetitions=r.repetitions,
        lapses=r.lapses,
    )


def leitner_box(interval_days: int) -> int:
    """Derived Leitner box for display only (1-5)."""
    if interval_days <= 1:
        return 1
    if interval_days <= 3:
        return 2
    if interval_days <= 7:
        return 3
    if interval_days <= 14:
        return 4
    return 5


def next_review_at(
    reviewed_at: datetime,
    interval_days: int,
    tz_offset_minutes: int = IST_OFFSET_MINUTES,
) -> datetime:
    """Return the next due time, snapped to 04:00 local so "due today" is stable.

    Timezone handling is explicit. The previous TypeScript implementation had a
    real bug in adjacent date arithmetic (see ``day_gap`` in ``gamification.py``)
    caused by mixing 0-based and 1-based month conventions; here everything is
    done in UTC with an explicit offset, and the tests pin the boundaries.
    """
    if reviewed_at.tzinfo is None:
        reviewed_at = reviewed_at.replace(tzinfo=UTC)

    offset = timedelta(minutes=tz_offset_minutes)
    local = reviewed_at + offset
    local = local.replace(hour=4, minute=0, second=0, microsecond=0)
    local = local + timedelta(days=interval_days)
    return local - offset
