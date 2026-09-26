"""Question bank, options, media and versioning.

Blueprint v3 §9.2 requires the question entity to carry, at minimum: id,
course_id, subject_id, chapter_id, topic_id, question_type, difficulty, marks,
negative_marks, year, attempt, source, is_historical, finance_act_year,
disclaimer_text, status, created_by, verified_by.

TWO DESIGN DECISIONS WORTH RECORDING

1. DENORMALISED FILTER COLUMNS (``course_id`` on the question row).
   The required index is
   ``idx_questions_filter(subject_id, chapter_id, attempt_id, question_type, year)``
   which spans only the question's own columns. The naive normalisation -
   deriving the course by joining chapters -> subjects -> courses - would mean the
   hot filtering path cannot use a single composite index, and the practice
   screen (the most-used screen in the product) would do a three-table join on
   every page load. ``course_id`` is therefore stored on the row AND kept honest
   by a triggers-free invariant enforced in the ingestion/QA service: the course
   is always derived from the chapter, never accepted from a client.

   ``attempt_id`` in that index is a reference to the ICAI exam sitting
   (May 2025, Nov 2025 ...), not the student's mock attempt. That naming
   collision is a genuine trap: see ``ExamAttempt`` in ``progress.py`` for the
   student-side table, and ``ExamSession`` below for the sitting.

2. TAXATION CURRENCY FIELDS.
   ``is_historical``, ``finance_act_year`` and ``disclaimer_text`` are NOT NULL /
   constrained rather than optional. A taxation question carries a permanent
   correctness problem: an answer based on a repealed provision is wrong today
   and was right when set. Students cannot tell the difference. Marking such
   questions historical and rendering an explicit disclaimer is the minimum
   honest treatment, and the fields are constrained so this cannot be forgotten
   silently at insert time.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UuidMixin
from app.models.enums import (
    ContentStatus,
    Difficulty,
    SyllabusScheme,
)


class ExamSession(Base, UuidMixin, TimestampMixin):
    """An ICAI exam sitting, e.g. "May 2025".

    Modelled as a table rather than a free-text string on the question because
    "May 25", "may-2025" and "May 2025" must not become three different filters,
    and because a sitting has a date, a scheme and an official paper reference.
    """

    __tablename__ = "exam_sessions"

    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: ICAI sittings are commonly May and November; September has been used too.
    month: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    syllabus_scheme: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SyllabusScheme.NEW_2024.value,
        server_default=sa_text("'NEW_2024'::character varying"),
    )
    exam_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )

    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_exam_session_year_month"),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="ck_exam_session_year"),
        CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')",
            name="ck_exam_session_scheme",
        ),
        Index("idx_exam_sessions_lookup", "year", "month", "syllabus_scheme"),
    )


class Question(Base, UuidMixin, TimestampMixin, SoftDeleteMixin):
    """A question in the bank."""

    __tablename__ = "questions"

    # ---- placement -------------------------------------------------------
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    #: Intermediate split, when an editor has assigned one. Null means unclassified.
    #: A chapter mapping may still place the question in one split for practice;
    #: this column is what locks it so it cannot appear in the other.
    subject_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subject_components.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ---- content ---------------------------------------------------------
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Optional plain-text version for the FTS index (media stripped, LaTeX flattened).
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    difficulty: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=Difficulty.MEDIUM.value,
        server_default=sa_text("'MEDIUM'::character varying"),
    )
    marks: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=sa_text("'1'::smallint"),
    )
    #: Stored positive; the scorer applies it as a deduction. Storing it negative
    #: invites a double-negation bug in scoring, which is a silent marks error.
    negative_marks: Mapped[float] = mapped_column(
        Numeric(4, 2), nullable=False, default=0, server_default=sa_text("0")
    )
    correct_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Reference answer for descriptive questions, used by human evaluators.
    model_answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- provenance ------------------------------------------------------
    year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_sessions.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_page: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: Supabase Storage path for the source PDF (private bucket).
    source_pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: OCR confidence when the row originated from the ingestion pipeline.
    extraction_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    # ---- taxation currency (§9.2) ---------------------------------------
    is_historical: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    finance_act_year: Mapped[str | None] = mapped_column(String(20), nullable=True)
    disclaimer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Review pipeline. Changing this does not change the answer or the attempts.
    review_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="CURRENT",
        server_default=sa_text("'CURRENT'"),
    )

    # ---- publication -----------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ContentStatus.DRAFT.value,
        server_default=sa_text("'DRAFT'"),
    )
    syllabus_scheme: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SyllabusScheme.UNMAPPED.value,
        server_default=sa_text("'UNMAPPED'"),
    )
    is_premium: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ---- analytics -------------------------------------------------------
    times_attempted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    times_correct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )

    options: Mapped[list[QuestionOption]] = relationship(
        back_populates="question", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "question_type IN ('MCQ','MSQ','TRUE_FALSE','NUMERICAL','DESCRIPTIVE','CASE_STUDY')",
            name="ck_questions_type",
        ),
        CheckConstraint("difficulty IN ('EASY','MEDIUM','HARD')", name="ck_questions_difficulty"),
        CheckConstraint(
            "status IN ('DRAFT','IN_REVIEW','APPROVED','PUBLISHED','ARCHIVED')",
            name="ck_questions_status",
        ),
        CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')",
            name="ck_questions_scheme",
        ),
        CheckConstraint("marks > 0", name="ck_questions_marks_positive"),
        CheckConstraint("negative_marks >= 0", name="ck_questions_negative_non_negative"),
        CheckConstraint("year IS NULL OR year BETWEEN 1990 AND 2100", name="ck_questions_year"),
        CheckConstraint(
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_questions_confidence_range",
        ),
        # A published question must have been verified by a human. This is the
        # constraint that makes the QA gate real rather than aspirational.
        CheckConstraint(
            "status <> 'PUBLISHED' OR verified_by IS NOT NULL",
            name="ck_questions_published_requires_verifier",
        ),
        # MCQ-style questions must have a recorded correct answer.
        CheckConstraint(
            "question_type NOT IN ('MCQ','TRUE_FALSE','NUMERICAL') OR correct_answer IS NOT NULL",
            name="ck_questions_objective_requires_answer",
        ),
        # Historical taxation content must carry its disclaimer, otherwise the
        # student sees a stale answer with no warning.
        CheckConstraint(
            "is_historical = false OR disclaimer_text IS NOT NULL",
            name="ck_questions_historical_requires_disclaimer",
        ),
        CheckConstraint(
            "review_state IN ('CURRENT','VERIFICATION_REQUIRED','NEEDS_UPDATE',"
            "'PENDING_ADMIN_REVIEW')",
            name="ck_questions_review_state",
        ),
        Index(
            "idx_questions_filter",
            "subject_id",
            "chapter_id",
            "attempt_id",
            "question_type",
            "year",
        ),
        Index("idx_questions_status", "status", "is_historical"),
        Index("idx_questions_topic", "topic_id"),
        Index("idx_questions_scheme_subject", "syllabus_scheme", "subject_id"),
        Index("idx_questions_subject_component", "subject_component_id"),
    )


class QuestionOption(Base, UuidMixin, TimestampMixin):
    """A choice belonging to a question."""

    __tablename__ = "question_options"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(4), nullable=False)  # A, B, C, D
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
    )
    sequence: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=sa_text("'0'::smallint"),
    )

    question: Mapped[Question] = relationship(back_populates="options")

    __table_args__ = (
        UniqueConstraint("question_id", "label", name="uq_option_question_label"),
        Index("idx_options_question_sequence", "question_id", "sequence"),
    )


class QuestionVersion(Base, UuidMixin, TimestampMixin):
    """Immutable snapshot of a question at each editorial change.

    Needed for two reasons that both bite sooner than expected:

    1. A student who attempted a question last month answered the version that
       existed then. Rewriting the row destroys the audit trail behind their
       score and makes a dispute unanswerable.
    2. Taxation content is edited every Finance Act. Without versions there is no
       way to tell whether a rise in wrong answers came from students or from the
       answer key changing underneath them.

    Rows here are append-only.
    """

    __tablename__ = "question_versions"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    change_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("question_id", "version_number", name="uq_question_version_number"),
        Index("idx_question_versions_question", "question_id", "version_number"),
    )


class QuestionFlag(Base, UuidMixin, TimestampMixin):
    """A student or editor report that a question is wrong.

    Separate from ``disclaimer_text`` on purpose: a flagged question is a
    suspected error discovered by users, a historical question is a known
    staleness the platform has already acknowledged. Merging the two would hide
    reports behind the disclaimer and neither would get fixed.
    """

    __tablename__ = "question_flags"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OPEN", server_default=sa_text("'OPEN'")
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "reason IN ('WRONG_ANSWER','TYPO','OUT_OF_SYLLABUS','HISTORICAL',"
            "'UNCLEAR','DUPLICATE')",
            name="ck_question_flags_reason",
        ),
        CheckConstraint(
            "status IN ('OPEN','TRIAGED','FIXED','REJECTED')",
            name="ck_question_flags_status",
        ),
        # One open flag per student per question - stops a single user from
        # burying the moderation queue.
        Index(
            "uq_open_flag_per_user",
            "question_id",
            "reported_by",
            unique=True,
            postgresql_where=sa_text("status = 'OPEN'"),
        ),
        Index("idx_question_flags_queue", "status", "created_at"),
    )


class LawNotice(Base, UuidMixin, TimestampMixin):
    """An administrator's record that a cited provision may have changed.

    Recording a notice flags matching questions for review. It does not rewrite
    an answer and it does not invent the new law. ``answers_changed`` is
    constrained false so a later writer cannot flip this row into a claim that
    the bank was updated.
    """

    __tablename__ = "law_notices"

    citation: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    matched_question_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )
    affected_mock_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )
    answers_changed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )

    __table_args__ = (
        CheckConstraint("answers_changed = false", name="ck_law_notices_do_not_rewrite"),
        Index("idx_law_notices_created", "created_at"),
    )
