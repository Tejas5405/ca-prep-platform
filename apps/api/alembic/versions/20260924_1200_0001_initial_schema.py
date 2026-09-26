"""Initial schema: curriculum, questions, users, progress.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-24

Blueprint v3 §9. Creates all 22 core tables plus the required indexes.

Two things in this migration are hand-written and CANNOT be produced by
autogenerate. Both are load-bearing, so do not "regenerate" this file:

1. GIN FULL-TEXT INDEX on questions.
   v3 §9.3 requires a GIN index over ``to_tsvector('english', text)``. It indexes
   an EXPRESSION, not a column, so it has no representation in the SQLAlchemy
   models and autogenerate will not emit it. Without it every search does a
   sequential scan.

2. pg_trgm extension + trigram index.
   Full-text search matches whole words. A student typing a fragment ("deduc")
   gets nothing. Trigram similarity is what makes partial-word search work, and
   the extension must exist before the index is created.

SEARCH STRATEGY NOTE: v3 §2 says "PostgreSQL FTS is sufficient; do not add a
search server yet." That is honoured here - no Elasticsearch, no Meilisearch.
The trigger that would justify revisiting is in the blueprint's scale section.

The raw ``search_text`` column exists so the FTS index does not have to evaluate
a function over the big ``text`` column, which may contain LaTeX and markdown
that would pollute the token stream.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


# Extension creation must run outside a transaction block in some PostgreSQL
# configurations, and needs superuser or an allowlisted role. On Supabase both
# pg_trgm and pgcrypto are already available.
def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ---------------------------------------------------------- curriculum
    op.create_table(
        "courses",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("syllabus_scheme", sa.String(20), nullable=False, server_default="UNMAPPED"),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "level IN ('FOUNDATION','INTERMEDIATE','FINAL')", name="ck_course_level"
        ),
        sa.CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')", name="ck_course_scheme"
        ),
        sa.UniqueConstraint("code", "syllabus_scheme", name="uq_course_code_scheme"),
    )
    op.create_index("idx_courses_active_scheme", "courses", ["is_active", "syllabus_scheme"])

    op.create_table(
        "subjects",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("group_name", sa.String(20), nullable=True),
        sa.Column("paper_number", sa.SmallInteger(), nullable=True),
        sa.Column("syllabus_weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "group_name IS NULL OR group_name IN ('GROUP_I','GROUP_II')", name="ck_subject_group"
        ),
        sa.CheckConstraint("syllabus_weight > 0", name="ck_subject_weight_positive"),
        sa.UniqueConstraint("course_id", "code", name="uq_subject_course_code"),
    )
    op.create_index("idx_subjects_course_active", "subjects", ["course_id", "is_active"])

    op.create_table(
        "chapters",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "subject_id",
            sa.UUID(),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weightage", sa.SmallInteger(), nullable=False, server_default="5"),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("weightage BETWEEN 1 AND 10", name="ck_chapter_weightage_range"),
        sa.CheckConstraint("estimated_minutes > 0", name="ck_chapter_minutes_positive"),
        sa.UniqueConstraint("subject_id", "code", name="uq_chapter_subject_code"),
    )
    op.create_index("idx_chapters_subject_sequence", "chapters", ["subject_id", "sequence"])

    op.create_table(
        "topics",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "chapter_id",
            sa.UUID(),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("chapter_id", "code", name="uq_topic_chapter_code"),
    )
    op.create_index("idx_topics_chapter_sequence", "topics", ["chapter_id", "sequence"])

    op.create_table(
        "exam_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.String(20), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("syllabus_scheme", sa.String(20), nullable=False, server_default="NEW_2024"),
        sa.Column("exam_start_date", sa.Date(), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("year BETWEEN 2000 AND 2100", name="ck_exam_session_year"),
        sa.CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')",
            name="ck_exam_session_scheme",
        ),
        sa.UniqueConstraint("year", "month", name="uq_exam_session_year_month"),
    )
    op.create_index(
        "idx_exam_sessions_lookup", "exam_sessions", ["year", "month", "syllabus_scheme"]
    )

    # ---------------------------------------------------------------- users
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("auth_user_id", sa.String(64), nullable=True, unique=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="STUDENT"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("target_level", sa.String(20), nullable=True),
        sa.Column("target_exam_date", sa.Date(), nullable=True),
        sa.Column("syllabus_scheme", sa.String(20), nullable=False, server_default="NEW_2024"),
        sa.Column("daily_goal_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("locale", sa.String(10), nullable=False, server_default="en-IN"),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("referral_code", sa.String(16), nullable=True, unique=True),
        sa.Column("referred_by_code", sa.String(16), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('STUDENT','EDITOR','CONTENT_MANAGER','MODERATOR','ADMIN','SUPER_ADMIN')",
            name="ck_users_role",
        ),
        sa.CheckConstraint(
            "target_level IS NULL OR target_level IN ('FOUNDATION','INTERMEDIATE','FINAL')",
            name="ck_users_target_level",
        ),
        sa.CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')", name="ck_users_scheme"
        ),
        sa.CheckConstraint("daily_goal_minutes BETWEEN 15 AND 900", name="ck_users_daily_goal"),
        # NOTE: there is deliberately NO password column. Blueprint v3 §8.2:
        # "Do not store or manage student passwords in PostgreSQL."
    )
    op.create_index("idx_users_role_active", "users", ["role", "is_active"])
    op.create_index("idx_users_target_exam", "users", ["target_exam_date"])
    op.create_index("ix_users_auth_user_id", "users", ["auth_user_id"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("college", sa.String(200), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("attempt_number", sa.SmallInteger(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("overall_accuracy", sa.Numeric(4, 3), nullable=True),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("longest_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_level", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("current_streak >= 0", name="ck_profile_streak_non_negative"),
        sa.CheckConstraint("total_points >= 0", name="ck_profile_points_non_negative"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tier", sa.String(20), nullable=False, server_default="FREE"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provider", sa.String(30), nullable=True),
        sa.Column("provider_subscription_id", sa.String(120), nullable=True),
        sa.Column("amount_paise", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "tier IN ('FREE','PREMIUM','PREMIUM_PLUS')", name="ck_subscriptions_tier"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','EXPIRED','CANCELLED','PAST_DUE','TRIAL')",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint(
            "amount_paise IS NULL OR amount_paise >= 0", name="ck_subscriptions_amount"
        ),
    )
    # Partial unique index: at most one live entitlement per user. A concurrent
    # webhook cannot then grant two subscriptions.
    op.create_index(
        "uq_active_subscription_per_user",
        "subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ACTIVE','TRIAL')"),
    )
    op.create_index("idx_subscriptions_expiry", "subscriptions", ["status", "expires_at"])

    op.create_table(
        "payment_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False, unique=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column(
            "signature_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_payment_events_unprocessed", "payment_events", ["processed_at"])
    op.create_index("idx_payment_events_type", "payment_events", ["event_type", "created_at"])

    # ------------------------------------------------------------ questions
    op.create_table(
        "questions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "subject_id",
            sa.UUID(),
            sa.ForeignKey("subjects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.UUID(),
            sa.ForeignKey("chapters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "topic_id", sa.UUID(), sa.ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("question_type", sa.String(20), nullable=False),
        sa.Column("difficulty", sa.String(10), nullable=False, server_default="MEDIUM"),
        sa.Column("marks", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("negative_marks", sa.Numeric(4, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("correct_answer", sa.Text(), nullable=True),
        sa.Column("model_answer", sa.Text(), nullable=True),
        sa.Column("year", sa.SmallInteger(), nullable=True),
        sa.Column(
            "attempt_id",
            sa.UUID(),
            sa.ForeignKey("exam_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(200), nullable=True),
        sa.Column("source_page", sa.SmallInteger(), nullable=True),
        sa.Column("source_pdf_path", sa.String(500), nullable=True),
        sa.Column("extraction_confidence", sa.Numeric(4, 3), nullable=True),
        # -------- taxation compliance, v3 §9.2 --------
        sa.Column("is_historical", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("finance_act_year", sa.String(20), nullable=True),
        sa.Column("disclaimer_text", sa.Text(), nullable=True),
        # ----------------------------------------------
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("syllabus_scheme", sa.String(20), nullable=False, server_default="UNMAPPED"),
        sa.Column("is_premium", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "verified_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("verified_at", sa.Date(), nullable=True),
        sa.Column("times_attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("times_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "question_type IN ('MCQ','MSQ','TRUE_FALSE','NUMERICAL','DESCRIPTIVE','CASE_STUDY')",
            name="ck_questions_type",
        ),
        sa.CheckConstraint(
            "difficulty IN ('EASY','MEDIUM','HARD')", name="ck_questions_difficulty"
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','IN_REVIEW','APPROVED','PUBLISHED','ARCHIVED')",
            name="ck_questions_status",
        ),
        sa.CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')", name="ck_questions_scheme"
        ),
        sa.CheckConstraint("marks > 0", name="ck_questions_marks_positive"),
        sa.CheckConstraint("negative_marks >= 0", name="ck_questions_negative_non_negative"),
        sa.CheckConstraint("year IS NULL OR year BETWEEN 1990 AND 2100", name="ck_questions_year"),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_questions_confidence_range",
        ),
        sa.CheckConstraint(
            "status <> 'PUBLISHED' OR verified_by IS NOT NULL",
            name="ck_questions_published_requires_verifier",
        ),
        sa.CheckConstraint(
            "question_type NOT IN ('MCQ','TRUE_FALSE','NUMERICAL') OR correct_answer IS NOT NULL",
            name="ck_questions_objective_requires_answer",
        ),
        sa.CheckConstraint(
            "is_historical = false OR disclaimer_text IS NOT NULL",
            name="ck_questions_historical_requires_disclaimer",
        ),
    )
    op.create_index(
        "idx_questions_filter",
        "questions",
        ["subject_id", "chapter_id", "attempt_id", "question_type", "year"],
    )
    op.create_index("idx_questions_status", "questions", ["status", "is_historical"])
    op.create_index("idx_questions_topic", "questions", ["topic_id"])
    op.create_index("idx_questions_scheme_subject", "questions", ["syllabus_scheme", "subject_id"])

    # -----------------------------------------------------------------------
    # HAND-WRITTEN. Autogenerate cannot produce either of these.
    # -----------------------------------------------------------------------
    # Expression index over a tsvector. Full-text search on the cleaned text.
    op.execute(
        """
        CREATE INDEX idx_questions_fts
        ON questions
        USING gin (to_tsvector('english', coalesce(search_text, text)))
        """
    )
    # Trigram index so partial-word search works ("deduc" -> "Deductions").
    # Full-text search alone matches whole lexemes and would return nothing here.
    op.execute(
        """
        CREATE INDEX idx_questions_trgm
        ON questions
        USING gin (text gin_trgm_ops)
        """
    )

    op.create_table(
        "question_options",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "question_id",
            sa.UUID(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(4), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sequence", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("question_id", "label", name="uq_option_question_label"),
    )
    op.create_index(
        "idx_options_question_sequence", "question_options", ["question_id", "sequence"]
    )

    op.create_table(
        "question_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "question_id",
            sa.UUID(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "changed_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("change_reason", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("question_id", "version_number", name="uq_question_version_number"),
    )
    op.create_index(
        "idx_question_versions_question", "question_versions", ["question_id", "version_number"]
    )

    op.create_table(
        "question_flags",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "question_id",
            sa.UUID(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reported_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column(
            "resolved_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "reason IN ('WRONG_ANSWER','TYPO','OUT_OF_SYLLABUS','HISTORICAL','UNCLEAR','DUPLICATE')",
            name="ck_question_flags_reason",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','TRIAGED','FIXED','REJECTED')", name="ck_question_flags_status"
        ),
    )
    # One OPEN flag per student per question.
    op.create_index(
        "uq_open_flag_per_user",
        "question_flags",
        ["question_id", "reported_by"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index("idx_question_flags_queue", "question_flags", ["status", "created_at"])

    # ------------------------------------------------------- progress/state
    op.create_table(
        "practice_attempts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "question_id",
            sa.UUID(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chosen_option", sa.String(4), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("time_spent_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_hint", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("time_spent_seconds >= 0", name="ck_practice_time_non_negative"),
    )
    op.create_index("idx_progress_user", "practice_attempts", ["user_id", "question_id"])
    op.create_index("idx_progress_user_created", "practice_attempts", ["user_id", "created_at"])

    op.create_table(
        "user_question_progress",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "question_id",
            sa.UUID(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_is_correct", sa.Boolean(), nullable=True),
        sa.Column(
            "is_marked_for_review", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("accuracy", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "question_id", name="uq_progress_user_question"),
        sa.CheckConstraint("attempts_count >= 0", name="ck_uqp_attempts_non_negative"),
        sa.CheckConstraint(
            "correct_count >= 0 AND correct_count <= attempts_count",
            name="ck_uqp_correct_lte_attempts",
        ),
    )
    op.create_index("idx_uqp_user_accuracy", "user_question_progress", ["user_id", "accuracy"])
    op.create_index("idx_uqp_marked", "user_question_progress", ["user_id", "is_marked_for_review"])

    op.create_table(
        "spaced_repetition_cards",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "question_id",
            sa.UUID(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ease_factor", sa.Numeric(4, 2), nullable=False, server_default="2.5"),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repetitions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "question_id", name="uq_sr_user_question"),
        sa.CheckConstraint("ease_factor >= 1.3 AND ease_factor <= 5.0", name="ck_sr_ease_range"),
        sa.CheckConstraint("interval_days >= 0", name="ck_sr_interval_non_negative"),
        sa.CheckConstraint("repetitions >= 0", name="ck_sr_repetitions_non_negative"),
    )
    op.create_index("idx_sr_due", "spaced_repetition_cards", ["user_id", "next_review_at"])

    op.create_table(
        "mock_tests",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "subject_id",
            sa.UUID(),
            sa.ForeignKey("subjects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("total_marks", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("syllabus_scheme", sa.String(20), nullable=False, server_default="NEW_2024"),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("is_premium", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "question_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('CHAPTER','SUBJECT','FULL_LENGTH','PREVIOUS_PAPER','CUSTOM')",
            name="ck_mock_tests_kind",
        ),
        sa.CheckConstraint("duration_min BETWEEN 1 AND 600", name="ck_mock_tests_duration"),
        sa.CheckConstraint("total_marks > 0", name="ck_mock_tests_marks"),
    )
    op.create_index("idx_mock_tests_listing", "mock_tests", ["course_id", "status", "kind"])

    op.create_table(
        "mock_attempts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "mock_test_id",
            sa.UUID(),
            sa.ForeignKey("mock_tests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("max_score", sa.Integer(), nullable=True),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("wrong_count", sa.Integer(), nullable=True),
        sa.Column("unattempted_count", sa.Integer(), nullable=True),
        sa.Column("pending_review_count", sa.Integer(), nullable=True),
        sa.Column("time_taken_seconds", sa.Integer(), nullable=True),
        sa.Column("auto_submitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS','SUBMITTED','AUTO_SUBMITTED','ABANDONED')",
            name="ck_mock_attempts_status",
        ),
        sa.CheckConstraint("score IS NULL OR score >= 0", name="ck_mock_attempts_score"),
    )
    op.create_index("idx_mock_attempts_user", "mock_attempts", ["user_id", "created_at"])
    op.create_index("idx_mock_attempts_mock", "mock_attempts", ["mock_test_id", "status"])
    op.create_index(
        "uq_mock_attempt_in_progress",
        "mock_attempts",
        ["user_id", "mock_test_id"],
        unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )

    # ------------------------------------------------------- gamification
    op.create_table(
        "daily_activities",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("questions_attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minutes_studied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points_earned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "activity_date", name="uq_activity_user_date"),
        sa.CheckConstraint(
            "questions_attempted >= 0 AND minutes_studied >= 0", name="ck_activity_non_negative"
        ),
    )
    op.create_index("idx_activity_user_date", "daily_activities", ["user_id", "activity_date"])

    op.create_table(
        "points_ledger",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("reference_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=True, unique=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("points <> 0", name="ck_ledger_points_non_zero"),
        sa.CheckConstraint(
            "reason IN ('QUESTION_CORRECT','CHAPTER_COMPLETE','MOCK_COMPLETE','MOCK_HIGH_SCORE',"
            "'STREAK_DAY','STREAK_MILESTONE','DAILY_CHALLENGE','BADGE_AWARDED',"
            "'REFERRAL_SIGNUP','REFERRAL_CONVERSION','ADMIN_ADJUSTMENT')",
            name="ck_ledger_reason",
        ),
    )
    op.create_index("idx_ledger_user_created", "points_ledger", ["user_id", "created_at"])

    op.create_table(
        "user_badges",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("badge_code", sa.String(40), nullable=False),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "badge_code", name="uq_badge_user_code"),
    )
    op.create_index("idx_badges_user", "user_badges", ["user_id", "earned_at"])

    op.create_table(
        "referrals",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "referrer_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "referred_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("code_used", sa.String(16), nullable=False),
        sa.Column("signed_up_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reward_granted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("referrer_user_id <> referred_user_id", name="ck_referral_not_self"),
    )
    op.create_index("idx_referrals_referrer", "referrals", ["referrer_user_id", "created_at"])


def downgrade() -> None:
    # Drop in reverse dependency order. Explicit rather than relying on CASCADE,
    # so a mistake here fails loudly instead of silently destroying a table
    # someone still depends on.
    # The two expression indexes are not attached to a table in the metadata,
    # so they are dropped explicitly before their tables disappear.
    op.execute("DROP INDEX IF EXISTS idx_questions_fts")
    op.execute("DROP INDEX IF EXISTS idx_questions_trgm")

    for table in (
        "referrals",
        "user_badges",
        "points_ledger",
        "daily_activities",
        "mock_attempts",
        "mock_tests",
        "spaced_repetition_cards",
        "user_question_progress",
        "practice_attempts",
        "question_flags",
        "question_versions",
        "question_options",
        "questions",
        "payment_events",
        "subscriptions",
        "user_profiles",
        "users",
        "exam_sessions",
        "topics",
        "chapters",
        "subjects",
        "courses",
    ):
        op.drop_table(table)
