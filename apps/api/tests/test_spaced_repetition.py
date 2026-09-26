"""SM-2 spaced repetition tests.

Ported from the superseded TypeScript suite - the behaviour is identical, so the
suite was ported with it rather than rewritten from scratch. Losing this coverage
in a stack migration is how regressions ship.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.spaced_repetition import (
    DEFAULT_EASE_FACTOR,
    MIN_EASE_FACTOR,
    ReviewState,
    initial_review_state,
    leitner_box,
    next_ease_factor,
    next_review_at,
    review,
)


class TestEaseFactor:
    def test_perfect_answer_raises_ease(self):
        # delta = 0.1 - 0 * (0.08 + 0) = 0.1
        assert next_ease_factor(2.5, 5) == 2.6

    def test_quality_4_leaves_ease_unchanged(self):
        # delta = 0.1 - 1 * (0.08 + 0.02) = 0
        assert next_ease_factor(2.5, 4) == 2.5

    def test_failed_answer_drops_ease(self):
        # delta = 0.1 - 5 * (0.08 + 0.1) = -0.8
        assert next_ease_factor(2.5, 0) == 1.7

    def test_never_falls_below_floor(self):
        ef = DEFAULT_EASE_FACTOR
        for _ in range(10):
            ef = next_ease_factor(ef, 0)
        assert ef == MIN_EASE_FACTOR

    def test_every_quality_is_defined(self):
        for q in range(6):
            assert next_ease_factor(2.5, q) >= MIN_EASE_FACTOR


class TestIntervalProgression:
    def test_progresses_1_then_6_then_interval_times_ease(self):
        s = initial_review_state()

        first = review(s, 4)
        assert first.interval_days == 1
        assert first.repetitions == 1

        second = review(ReviewState(first.ease_factor, first.interval_days, first.repetitions), 4)
        assert second.interval_days == 6
        assert second.repetitions == 2

        third = review(ReviewState(second.ease_factor, second.interval_days, second.repetitions), 4)
        # EF stayed at 2.5 for q=4, so 6 * 2.5 = 15
        assert third.interval_days == 15
        assert third.repetitions == 3

    def test_lapse_resets_interval_and_counts_the_lapse(self):
        s = ReviewState(ease_factor=2.7, interval_days=6, repetitions=2)
        failed = review(s, 2)
        assert failed.interval_days == 1
        assert failed.repetitions == 0
        assert failed.lapses == 1
        assert failed.lapsed is True

    def test_quality_3_passes_and_quality_2_lapses(self):
        s = ReviewState(ease_factor=2.5, interval_days=6, repetitions=2)
        assert review(s, 3).lapsed is False
        assert review(s, 2).lapsed is True

    def test_interval_never_zero_even_at_ease_floor(self):
        s = ReviewState(ease_factor=MIN_EASE_FACTOR, interval_days=1, repetitions=3)
        assert review(s, 5).interval_days >= 1

    def test_is_pure_and_does_not_mutate_input(self):
        s = initial_review_state()
        before = (s.ease_factor, s.interval_days, s.repetitions, s.lapses)
        review(s, 5)
        assert (s.ease_factor, s.interval_days, s.repetitions, s.lapses) == before

    def test_rejects_out_of_range_quality(self):
        for bad in (-1, 6, 99):
            with pytest.raises(ValueError):
                review(initial_review_state(), bad)


class TestLeitnerBoxIsDerived:
    @pytest.mark.parametrize(
        "interval,expected",
        [(1, 1), (3, 2), (7, 3), (14, 4), (15, 5), (365, 5)],
    )
    def test_maps_intervals_to_boxes(self, interval, expected):
        assert leitner_box(interval) == expected


IST = timedelta(minutes=330)


def as_ist(moment: datetime) -> datetime:
    """Shift to IST wall-clock so assertions read in the user's timezone.

    The function under test returns a UTC instant, but the *product* promise is
    "due at 04:00 IST, N days later". Asserting on raw UTC dates silently
    compares the wrong day, because 04:00 IST is 22:30 UTC on the previous
    calendar date.
    """
    return moment + IST


class TestNextReviewAt:
    def test_snaps_to_four_am_ist(self):
        reviewed = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
        local = as_ist(next_review_at(reviewed, 6))
        assert (local.hour, local.minute) == (4, 0)

    def test_advances_by_the_requested_number_of_days(self):
        # Compare in IST: the promise is "due N days later at 04:00 IST".
        # 24 Sep 15:30 IST + 6 days = 30 Sep 04:00 IST.
        reviewed = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
        nxt = next_review_at(reviewed, 6)
        assert (as_ist(nxt).date() - as_ist(reviewed).date()).days == 6

    def test_is_stable_across_a_month_boundary(self):
        # 28 Jan (IST) + 6 days = 3 Feb (IST), NOT 2 Feb.
        reviewed = datetime(2026, 1, 28, 6, 0, tzinfo=UTC)
        local = as_ist(next_review_at(reviewed, 6))
        assert (local.month, local.day) == (2, 3)

    def test_is_stable_across_a_year_boundary(self):
        reviewed = datetime(2026, 12, 30, 6, 0, tzinfo=UTC)
        local = as_ist(next_review_at(reviewed, 6))
        assert (local.year, local.month, local.day) == (2027, 1, 5)

    def test_is_stable_across_a_leap_day(self):
        reviewed = datetime(2028, 2, 26, 6, 0, tzinfo=UTC)
        local = as_ist(next_review_at(reviewed, 3))
        assert (local.month, local.day) == (2, 29)

    def test_is_stable_across_a_non_leap_february(self):
        reviewed = datetime(2026, 2, 26, 6, 0, tzinfo=UTC)
        local = as_ist(next_review_at(reviewed, 3))
        assert (local.month, local.day) == (3, 1)

    def test_naive_datetime_is_treated_as_utc(self):
        reviewed = datetime(2026, 9, 24, 10, 0)
        assert next_review_at(reviewed, 1).tzinfo is not None
