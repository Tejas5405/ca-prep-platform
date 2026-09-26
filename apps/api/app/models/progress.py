"""Practice progress, mock attempts, spaced repetition and points.

NAMING COLLISION, HANDLED DELIBERATELY

Blueprint v3 §9.3 requires an index:
``idx_questions_filter(subject_id, chapter_id, attempt_id, question_type, year)``

In that index ``attempt_id`` means the ICAI EXAM SITTING ("May 2025"), i.e. the
``exam_sessions`` row. But "attempt" is also the obvious name for a student's
mock attempt. Using the same word for both is how a query ends up filtering
questions by a student's attempt id and returning nothing, or worse, something.

Resolution used throughout this codebase:
  * ``ExamSession`` + ``ExamSession.id``  -> the ICAI sitting
      (column on questions stays named ``attempt_id`` to match the required
       index DDL exactly, and is FK'd to exam_sessions)
  * ``MockAttempt``  -> the student's sitting of a mock test
  * ``PracticeAttempt`` -> a single answered question in practice mode

The API contract uses ``mockAttemptId`` and ``examSessionId`` so the ambiguity
never reaches a client.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidMixin


class PracticeAttempt(Base, UuidMixin, TimestampMixin):
    """One question answered in practice mode."""

    __tablename__ = "practice_attempts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    chosen_option: Mapped[str | None] = mapped_column(String(4), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    time_spent_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    #: True when the student revealed the answer before responding, so the row
    #: does not count toward accuracy. Keeping these rows (rather than discarding
    #: them) preserves an honest denominator.
    used_hint: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    #: Which ``question_versions`` row this answer was marked against. Null only
    #: for attempts taken before that number was stored. A later correction must
    #: not fill this in with the new version.
    question_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("time_spent_seconds >= 0", name="ck_practice_time_non_negative"),
        # Matches §9.3 idx_progress_user(user_id, question_id) for the
        # "have I done this question" lookup.
        Index("idx_progress_user", "user_id", "question_id"),
        Index("idx_progress_user_created", "user_id", "created_at"),
    )


class UserQuestionProgress(Base, UuidMixin, TimestampMixin):
    """Current per-question state for a student.

    Separate from the ``practice_attempts`` event log: attempts are append-only
    history, this is the current summarised state that the question list filters
    on (``user_accuracy`` in a smart collection). Recomputing the summary from
    history on every list request would not scale.
    """

    __tablename__ = "user_question_progress"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    attempts_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    correct_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_marked_for_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    #: Denormalised for sorting and the "focus areas" query.
    accuracy: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_progress_user_question"),
        CheckConstraint("attempts_count >= 0", name="ck_uqp_attempts_non_negative"),
        CheckConstraint(
            "correct_count >= 0 AND correct_count <= attempts_count",
            name="ck_uqp_correct_lte_attempts",
        ),
        Index("idx_uqp_user_accuracy", "user_id", "accuracy"),
        Index("idx_uqp_marked", "user_id", "is_marked_for_review"),
    )


class SpacedRepetitionCard(Base, UuidMixin, TimestampMixin):
    """SM-2 scheduling state for one student/question pair.

    ``next_review_at`` is TIMESTAMPTZ. The superseded implementation computed the
    next review date with a zero-indexed month and a local-time offset, which
    made reviews land on the wrong day at month and year boundaries. Storing a
    real instant and doing the arithmetic in UTC (see
    ``app.services.spaced_repetition``) is what prevents that.
    """

    __tablename__ = "spaced_repetition_cards"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    #: 0-5 quality of the last response, SM-2 scale.
    ease_factor: Mapped[float] = mapped_column(
        Numeric(4, 2), nullable=False, default=2.5, server_default=sa_text("2.5")
    )
    interval_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    repetitions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    next_review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_sr_user_question"),
        # SM-2 clamps ease to 1.3; below that the intervals collapse.
        CheckConstraint("ease_factor >= 1.3 AND ease_factor <= 5.0", name="ck_sr_ease_range"),
        CheckConstraint("interval_days >= 0", name="ck_sr_interval_non_negative"),
        CheckConstraint("repetitions >= 0", name="ck_sr_repetitions_non_negative"),
        # The "what is due for me today" query - the hottest read in the app.
        Index("idx_sr_due", "user_id", "next_review_at"),
    )


class MockAttempt(Base, UuidMixin, TimestampMixin):
    """A student's sitting of a mock test."""

    __tablename__ = "mock_attempts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mock_test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mock_tests.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="IN_PROGRESS",
        server_default=sa_text("'IN_PROGRESS'"),
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Server-computed deadline. The client clock is never trusted for this.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wrong_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unattempted_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_taken_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: True when the server force-submitted at the deadline. Kept so a student's
    #: low score can be explained and audited.
    auto_submitted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )

    #: The submitted answers, as ``[{"q": <question uuid>, "chosen": <index|null>}]``.
    #:
    #: A JSONB column rather than a child table, which is the unusual choice and
    #: therefore the one worth justifying. The blueprint's other one-to-many rows
    #: (``question_options``, ``doubt_replies``) are entities in their own right:
    #: something else references them, or they are queried across parents. An
    #: answer is neither. It is written once, read once, always read WITH its
    #: attempt, and never joined to anything - which is exactly the shape JSONB
    #: exists for. A child table would add a migration, an FK, an index and a join
    #: to every report render, and would make the one query that matters (a
    #: finished paper) slower.
    #:
    #: It is NOT the score. The score lives in the columns above so it can be
    #: aggregated, ranked and compared in SQL without a JSON parse.
    answers: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa_text("'[]'::jsonb"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('IN_PROGRESS','SUBMITTED','AUTO_SUBMITTED','ABANDONED')",
            name="ck_mock_attempts_status",
        ),
        CheckConstraint("score IS NULL OR score >= 0", name="ck_mock_attempts_score"),
        Index("idx_mock_attempts_user", "user_id", "created_at"),
        Index("idx_mock_attempts_mock", "mock_test_id", "status"),
        # One live attempt per user per mock: stops a student opening ten tabs
        # and getting ten attempts at the same paper.
        Index(
            "uq_mock_attempt_in_progress",
            "user_id",
            "mock_test_id",
            unique=True,
            postgresql_where=sa_text("status = 'IN_PROGRESS'"),
        ),
    )


class MockTest(Base, UuidMixin, TimestampMixin):
    """A composed mock paper."""

    __tablename__ = "mock_tests"

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=180,
        server_default=sa_text("180"),
    )
    total_marks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default=sa_text("100"),
    )
    syllabus_scheme: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="NEW_2024",
        server_default=sa_text("'NEW_2024'::character varying"),
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", server_default=sa_text("'DRAFT'")
    )
    is_premium: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    question_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa_text("'[]'::jsonb"),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('CHAPTER','SUBJECT','FULL_LENGTH','PREVIOUS_PAPER','CUSTOM')",
            name="ck_mock_tests_kind",
        ),
        CheckConstraint("duration_min BETWEEN 1 AND 600", name="ck_mock_tests_duration"),
        CheckConstraint("total_marks > 0", name="ck_mock_tests_marks"),
        Index("idx_mock_tests_listing", "course_id", "status", "kind"),
    )


class DailyActivity(Base, UuidMixin, TimestampMixin):
    """One row per user per day of study activity.

    The streak calculation reads this table. The bug in the superseded code was
    building the day key with a zero-indexed month, so streaks reset at every
    month and year boundary; the fix is that the day key is a real ``DATE``
    column interpreted in the USER'S timezone, never a constructed timestamp.
    """

    __tablename__ = "daily_activities"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Local calendar date in the user's timezone, not UTC.
    activity_date: Mapped[date] = mapped_column(Date, nullable=False)
    questions_attempted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    minutes_studied: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    points_earned: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "activity_date", name="uq_activity_user_date"),
        CheckConstraint(
            "questions_attempted >= 0 AND minutes_studied >= 0",
            name="ck_activity_non_negative",
        ),
        Index("idx_activity_user_date", "user_id", "activity_date"),
    )


class PointsLedger(Base, UuidMixin, TimestampMixin):
    """Append-only points ledger.

    Blueprint v3 §10 places quotas and rate limits in Redis but points in
    PostgreSQL. The reason is that losing a cache eviction must never cost a
    student their streak, so the ledger in PostgreSQL is the source of truth and
    the aggregate on ``user_profiles`` is a derived cache.

    ``idempotency_key`` makes awarding safe to retry: a double-award from a
    retried job or a duplicated webhook is rejected by the unique constraint
    rather than silently doubling a balance.
    """

    __tablename__ = "points_ledger"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Optional reference (question id, mock attempt id, badge code).
    reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        CheckConstraint("points <> 0", name="ck_ledger_points_non_zero"),
        CheckConstraint(
            "reason IN ('QUESTION_CORRECT','CHAPTER_COMPLETE','MOCK_COMPLETE',"
            "'MOCK_HIGH_SCORE','STREAK_DAY','STREAK_MILESTONE','DAILY_CHALLENGE',"
            "'BADGE_AWARDED','REFERRAL_SIGNUP','REFERRAL_CONVERSION',"
            "'ADMIN_ADJUSTMENT')",
            name="ck_ledger_reason",
        ),
        Index("idx_ledger_user_created", "user_id", "created_at"),
    )


class UserBadge(Base, UuidMixin, TimestampMixin):
    """A badge earned by a student."""

    __tablename__ = "user_badges"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    badge_code: Mapped[str] = mapped_column(String(40), nullable=False)
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "badge_code", name="uq_badge_user_code"),
        Index("idx_badges_user", "user_id", "earned_at"),
    )


class Referral(Base, UuidMixin, TimestampMixin):
    """A referral relationship between two users."""

    __tablename__ = "referrals"

    referrer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    referred_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # a user is referred at most once
    )
    code_used: Mapped[str] = mapped_column(String(16), nullable=False)
    signed_up_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set when the referred user becomes a paying subscriber; this is what
    #: triggers the reward. Signup alone must not pay out.
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reward_granted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )

    __table_args__ = (
        # Self-referral is the obvious abuse; blocked at the database level so no
        # service-layer oversight can let it through.
        CheckConstraint("referrer_user_id <> referred_user_id", name="ck_referral_not_self"),
        Index("idx_referrals_referrer", "referrer_user_id", "created_at"),
    )
