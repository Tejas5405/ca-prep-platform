"""Mock exam scoring, ranking and percentile.

Rank and percentile are computed ONCE at submission and stored on the attempt.
Recomputing per view would put an ORDER BY over the full attempt table on the hot
path of every report render, and the blueprint asks for "rank snapshots" (§4).

No live in-test leaderboard: N concurrent writers per answer is a different load
profile from the ``leaderboard:{period}`` Redis key with a 5-15 minute TTL that
the blueprint specifies (§10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MIN_ATTEMPTS_FOR_FOCUS = 5
FOCUS_ACCURACY_THRESHOLD = 0.6
STRONG_ACCURACY_THRESHOLD = 0.8


@dataclass(frozen=True)
class AnswerInput:
    question_id: str
    #: Correct option index, or None for a descriptive question.
    correct_option: int | None
    #: What the student submitted. None means not attempted.
    chosen_option: int | None
    marks: int


@dataclass(frozen=True)
class ScoreResult:
    score: int
    max_score: int
    correct: int
    wrong: int
    unattempted: int
    #: Descriptive answers awaiting human review - the score is provisional.
    pending_review: int


def score_attempt(answers: list[AnswerInput]) -> ScoreResult:
    score = 0
    max_score = 0
    correct = 0
    wrong = 0
    unattempted = 0
    pending_review = 0

    for a in answers:
        max_score += a.marks

        if a.correct_option is None:
            # Descriptive: cannot be auto-scored, so it is routed to human QA
            # rather than guessed at.
            if a.chosen_option is None:
                unattempted += 1
            else:
                pending_review += 1
            continue

        if a.chosen_option is None:
            unattempted += 1
        elif a.chosen_option == a.correct_option:
            correct += 1
            score += a.marks
        else:
            # No negative marking. CONFIRM against the current ICAI pattern
            # before launch - see the open item in the PRD.
            wrong += 1

    return ScoreResult(score, max_score, correct, wrong, unattempted, pending_review)


@dataclass(frozen=True)
class RankResult:
    rank: int
    total_attempts: int
    percentile: float


def compute_rank(score: int, other_scores: list[int]) -> RankResult:
    """Competition ranking: 100, 90, 90, 80 -> ranks 1, 2, 2, 4.

    PERCENTILE DEFINITION - deliberate and user-visible.

    We use the proportion of attempts scored STRICTLY BELOW this one, which is
    the convention ICAI and most Indian competitive exams publish.

    Accepted consequence: the top scorer never reads "100th percentile". On a
    four-person cohort the leader sees 50th. That is a feature. Telling a student
    they beat 100% of candidates overstates their position, and it is exactly the
    kind of number a paying customer will quote back at you.

    Do not change this without changing the tests in both the API and web suites.
    """
    total_attempts = len(other_scores) + 1
    beaten = sum(1 for s in other_scores if s < score)
    above = sum(1 for s in other_scores if s > score)
    return RankResult(
        rank=above + 1,
        total_attempts=total_attempts,
        percentile=round((beaten / total_attempts) * 100, 2),
    )


@dataclass(frozen=True)
class ChapterPerformance:
    chapter_id: str
    accuracy: float
    attempted: int
    weightage: int = 0


def derive_focus_areas(
    chapters: list[ChapterPerformance],
    min_attempts: int = MIN_ATTEMPTS_FOR_FOCUS,
    accuracy_threshold: float = FOCUS_ACCURACY_THRESHOLD,
) -> list[str]:
    """Deterministic focus areas - no ML, fully explainable in a report card.

    Ranked by weightage first, so a student with limited time is pointed at the
    chapters where improvement actually moves the score.
    """
    candidates = [
        c for c in chapters if c.attempted >= min_attempts and c.accuracy < accuracy_threshold
    ]
    candidates.sort(key=lambda c: (-c.weightage, c.accuracy))
    return [c.chapter_id for c in candidates]


def derive_strong_areas(
    chapters: list[ChapterPerformance],
    min_attempts: int = MIN_ATTEMPTS_FOR_FOCUS,
    accuracy_threshold: float = STRONG_ACCURACY_THRESHOLD,
) -> list[str]:
    candidates = [
        c for c in chapters if c.attempted >= min_attempts and c.accuracy >= accuracy_threshold
    ]
    candidates.sort(key=lambda c: -c.accuracy)
    return [c.chapter_id for c in candidates]


def is_expired(started_at: datetime, duration_min: int, now: datetime | None = None) -> bool:
    """Server-side timeout enforcement. The client clock is never trusted.

    ``started_at`` is a ``datetime``, NOT the ISO string that arrives in the
    request body. The route used to call this with ``payload.started_at`` - which
    is a ``TimestampRef``, so a ``str`` - and every call would have raised
    ``TypeError`` the moment a deadline was reached. It was unreachable only
    because the route never persisted an attempt to enforce a deadline on.

    Submissions are now judged against the STORED ``expires_at`` on the attempt,
    which is the window the student was actually shown; this function stays as the
    single definition of the rule and is asserted to agree with it.
    """
    now = now or datetime.now(UTC)
    return now >= started_at + timedelta(minutes=duration_min)
