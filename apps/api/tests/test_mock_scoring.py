"""Mock exam scoring, ranking, percentile and focus-area derivation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

from app.services.mock_scoring import (
    AnswerInput,
    ChapterPerformance,
    compute_rank,
    derive_focus_areas,
    derive_strong_areas,
    is_expired,
    score_attempt,
)


def mcq(qid: str, correct: int | None, chosen: int | None, marks: int = 2) -> AnswerInput:
    return AnswerInput(qid, correct, chosen, marks)


class TestScoreAttempt:
    def test_awards_marks_for_correct_and_nothing_for_wrong(self):
        r = score_attempt([mcq("q1", 0, 0), mcq("q2", 1, 3), mcq("q3", 2, 2)])
        assert r.score == 4
        assert r.max_score == 6
        assert r.correct == 2
        assert r.wrong == 1
        assert r.unattempted == 0

    def test_counts_none_as_unattempted(self):
        r = score_attempt([mcq("q1", 0, None), mcq("q2", 1, None)])
        assert r.unattempted == 2
        assert r.score == 0

    def test_routes_descriptive_answers_to_human_review(self):
        r = score_attempt(
            [
                AnswerInput("d1", None, 1, 8),
                AnswerInput("d2", None, None, 8),
            ]
        )
        assert r.pending_review == 1
        assert r.unattempted == 1
        assert r.score == 0
        assert r.max_score == 16

    def test_empty_attempt(self):
        r = score_attempt([])
        assert (r.score, r.max_score, r.correct, r.wrong) == (0, 0, 0, 0)

    def test_respects_per_question_marks(self):
        r = score_attempt([mcq("q1", 0, 0, 10), mcq("q2", 0, 0, 4)])
        assert r.score == 14
        assert r.max_score == 14


class TestComputeRank:
    def test_ranks_a_clear_winner_first(self):
        r = compute_rank(100, [80, 60, 40])
        assert r.rank == 1
        assert r.total_attempts == 4
        assert r.percentile == 75.0

    def test_ties_share_the_better_rank(self):
        # scores 100, 90, 90, 80 -> ranks 1, 2, 2, 4
        r = compute_rank(90, [100, 90, 80])
        assert r.rank == 2
        assert r.total_attempts == 4
        assert r.percentile == 25.0

    def test_single_attempt_is_rank_one_without_inflated_percentile(self):
        r = compute_rank(50, [])
        assert r.rank == 1
        assert r.total_attempts == 1
        assert r.percentile == 0.0

    def test_lowest_score_ranks_last(self):
        r = compute_rank(10, [90, 80, 70])
        assert r.rank == 4
        assert r.percentile == 0.0

    def test_never_reports_the_top_scorer_as_100th_percentile(self):
        # Deliberate: telling a student they beat 100% of candidates overstates
        # their position. See the note in mock_scoring.compute_rank.
        r = compute_rank(100, [0, 0, 0, 0])
        assert r.rank == 1
        assert r.percentile < 100
        assert r.percentile == 80.0

    def test_a_higher_score_never_ranks_worse(self):
        others = [55, 70, 70, 82, 91]
        previous = float("inf")
        for score in [0, 55, 60, 70, 80, 91, 100]:
            rank = compute_rank(score, others).rank
            assert rank <= previous
            previous = rank


class TestTimeoutEnforcement:
    START = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)

    def test_not_expired_one_second_before_the_deadline(self):
        now = self.START + timedelta(minutes=180) - timedelta(seconds=1)
        assert is_expired(self.START, 180, now) is False

    def test_expired_exactly_at_the_deadline(self):
        now = self.START + timedelta(minutes=180)
        assert is_expired(self.START, 180, now) is True

    def test_expired_after_the_deadline(self):
        now = self.START + timedelta(minutes=181)
        assert is_expired(self.START, 180, now) is True


class TestFocusAndStrongAreas:
    CHAPTERS: ClassVar[list] = [
        ChapterPerformance("weak-high", 0.3, 12, 20),
        ChapterPerformance("weak-low", 0.4, 9, 5),
        ChapterPerformance("solid", 0.9, 15, 20),
        ChapterPerformance("thin-sample", 0.1, 2, 50),
    ]

    def test_ranks_weak_areas_by_weightage_not_raw_accuracy(self):
        assert derive_focus_areas(self.CHAPTERS) == ["weak-high", "weak-low"]

    def test_ignores_chapters_without_enough_attempts(self):
        # thin-sample has terrible accuracy but only two attempts.
        assert "thin-sample" not in derive_focus_areas(self.CHAPTERS)

    def test_identifies_strong_areas(self):
        assert derive_strong_areas(self.CHAPTERS) == ["solid"]

    def test_is_deterministic(self):
        assert derive_focus_areas(self.CHAPTERS) == derive_focus_areas(self.CHAPTERS)
