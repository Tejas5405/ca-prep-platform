"""The content library, the operations tables, and the FTS index over extracted text.

Revision ID: 0008_content_library
Revises: 0007_collections
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_content_library"
down_revision = "0007_collections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------- content_documents
    op.create_table(
        "content_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default="STUDY_MATERIAL"),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("module", sa.String(120), nullable=True),
        sa.Column("syllabus_scheme", sa.String(20), nullable=True),
        sa.Column("difficulty", sa.String(10), nullable=False, server_default="MEDIUM"),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("bucket", sa.String(120), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False, server_default="application/pdf"),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="UPLOADED"),
        sa.Column("ingestion_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("extracted_chars", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ocr_pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("access_tier", sa.String(20), nullable=False, server_default="PREMIUM"),
        sa.Column("is_published", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("allow_download", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ingestion_job_id"], ["ingestion_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["content_documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "kind IN ('STUDY_MATERIAL','NOTES','QUESTION_BANK','TEST_SERIES','REFERENCE','OTHER')",
            name="ck_document_kind",
        ),
        sa.CheckConstraint(
            "status IN ('UPLOADED','QUEUED','PROCESSING','EXTRACTING','OCR_REQUIRED',"
            "'INDEXED','COMPLETED','FAILED','ARCHIVED')",
            name="ck_document_status",
        ),
        sa.CheckConstraint(
            "access_tier IN ('FREE','PREMIUM','PREMIUM_PLUS')", name="ck_document_tier"
        ),
        sa.CheckConstraint("difficulty IN ('EASY','MEDIUM','HARD')", name="ck_document_difficulty"),
        sa.CheckConstraint("version >= 1", name="ck_document_version"),
        sa.UniqueConstraint("bucket", "storage_path", name="uq_document_object"),
    )
    op.create_index("idx_documents_status", "content_documents", ["status", "created_at"])
    op.create_index(
        "idx_documents_placement",
        "content_documents",
        ["course_id", "subject_id", "chapter_id"],
    )
    op.create_index("idx_documents_batch", "content_documents", ["batch_id"])
    op.create_index("idx_documents_checksum", "content_documents", ["checksum_sha256"])

    # --------------------------------------------------------- document_pages
    #
    # `search_vector` is a GENERATED column rather than a trigger. A trigger is a
    # second definition of the same expression that can drift from the index, and the
    # failure mode is silently wrong search results. Postgres 12+ computes it in the
    # storage engine, so the value and the index can never disagree.
    op.create_table(
        "document_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("char_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extraction_tier", sa.String(20), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("used_ocr", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["document_id"], ["content_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "page_number", name="uq_document_page"),
        sa.CheckConstraint("page_number >= 1", name="ck_document_page_number"),
    )
    op.execute(
        "ALTER TABLE document_pages ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED"
    )
    op.create_index(
        "idx_document_pages_fts", "document_pages", ["search_vector"], postgresql_using="gin"
    )
    op.create_index("idx_document_pages_doc", "document_pages", ["document_id", "page_number"])

    # ------------------------------------------------------ content_access_rules
    op.create_table(
        "content_access_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("effect", sa.String(6), nullable=False, server_default="ALLOW"),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_code", sa.String(20), nullable=True),
        sa.Column("tier", sa.String(20), nullable=True),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["document_id"], ["content_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "scope IN ('ROLE','USER','COURSE','PLAN','TIER')", name="ck_access_rule_scope"
        ),
        sa.CheckConstraint("effect IN ('ALLOW','DENY')", name="ck_access_rule_effect"),
        sa.CheckConstraint(
            "(scope = 'ROLE' AND role IS NOT NULL) OR (scope = 'USER' AND user_id IS NOT NULL) "
            "OR (scope = 'COURSE' AND course_id IS NOT NULL) "
            "OR (scope = 'PLAN' AND plan_code IS NOT NULL) "
            "OR (scope = 'TIER' AND tier IS NOT NULL)",
            name="ck_access_rule_target",
        ),
        sa.UniqueConstraint(
            "document_id",
            "scope",
            "role",
            "user_id",
            "course_id",
            "plan_code",
            "tier",
            name="uq_access_rule_target",
        ),
    )
    op.create_index("idx_access_rules_document", "content_access_rules", ["document_id"])
    op.create_index("idx_access_rules_user", "content_access_rules", ["user_id"])

    # ------------------------------------------------------------ notifications
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="SYSTEM"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("link_url", sa.String(300), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("audience", sa.String(40), nullable=True),
        sa.Column("broadcast_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "kind IN ('SYSTEM','ANNOUNCEMENT','CONTENT','PAYMENT','ACHIEVEMENT')",
            name="ck_notification_kind",
        ),
    )
    op.create_index("idx_notifications_inbox", "notifications", ["user_id", "created_at"])
    op.create_index(
        "idx_notifications_unread",
        "notifications",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index("idx_notifications_broadcast", "notifications", ["broadcast_id"])

    # ------------------------------------------------------------------ badges
    op.create_table(
        "badges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("icon", sa.String(40), nullable=False, server_default="🏅"),
        sa.Column("criteria_kind", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("criteria_value", sa.Integer, nullable=True),
        sa.Column("points_reward", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="100"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "criteria_kind IN ('MANUAL','POINTS','STREAK','QUESTIONS_ANSWERED','MOCK_SCORE',"
            "'CHAPTERS_COMPLETE')",
            name="ck_badge_criteria",
        ),
    )
    op.create_index("idx_badges_active", "badges", ["is_active", "display_order"])

    # -------------------------------------------------------- analytics_events
    op.create_table(
        "analytics_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("properties", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_analytics_name_time", "analytics_events", ["name", "created_at"])
    op.create_index("idx_analytics_user_time", "analytics_events", ["user_id", "created_at"])

    # --------------------------------------------------------------- audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(320), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("changes", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("idx_audit_time", "audit_logs", ["created_at"])
    op.create_index("idx_audit_actor", "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("idx_audit_action", "audit_logs", ["action", "created_at"])
    op.create_index("idx_audit_target", "audit_logs", ["target_type", "target_id"])

    # -------------------------------------------------------- platform_settings
    op.create_table(
        "platform_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False, unique=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_settings_public", "platform_settings", ["is_public"])

    # ----------------------------------------------------- seeded settings/badges
    #
    # Baseline rows so the admin panels are not empty on first load, and so the
    # feature flags read by the API have a defined default rather than an absence
    # that every caller has to interpret.
    op.execute(
        """
        INSERT INTO platform_settings (id, key, value, description, is_public, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'platform.name', '"CA Prep"'::jsonb, 'Shown in the app header and emails.', true, now(), now()),
          (gen_random_uuid(), 'platform.support_email', '"support@caprep.in"'::jsonb, 'Shown on the privacy and terms pages.', true, now(), now()),
          (gen_random_uuid(), 'features.ai_assistant', 'false'::jsonb, 'Master switch for the AI assistant. Requires a provider key.', true, now(), now()),
          (gen_random_uuid(), 'features.ai_content_generation', 'false'::jsonb, 'Master switch for generating questions from uploaded material.', false, now(), now()),
          (gen_random_uuid(), 'features.video_solutions', 'false'::jsonb, 'Video is not implemented. Left off deliberately.', true, now(), now()),
          (gen_random_uuid(), 'features.leaderboard', 'true'::jsonb, 'Show the points leaderboard to students.', true, now(), now()),
          (gen_random_uuid(), 'features.gamification', 'true'::jsonb, 'Points, badges and streaks.', true, now(), now()),
          (gen_random_uuid(), 'features.registration_open', 'true'::jsonb, 'When false, only existing users can sign in.', true, now(), now()),
          (gen_random_uuid(), 'learning.free_question_quota', '50'::jsonb, 'Questions a free student may attempt per day.', true, now(), now()),
          (gen_random_uuid(), 'learning.default_daily_goal_minutes', '120'::jsonb, 'Default daily study goal for a new account.', true, now(), now()),
          (gen_random_uuid(), 'payments.enabled', 'true'::jsonb, 'Show the upgrade flow. Actual charges need Razorpay keys in the environment.', true, now(), now())
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO badges (id, code, name, description, icon, criteria_kind, criteria_value,
                            points_reward, is_active, display_order, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'FIRST_STEPS', 'First Steps', 'Answer your first question.', '🎯', 'QUESTIONS_ANSWERED', 1, 5, true, 10, now(), now()),
          (gen_random_uuid(), 'CENTURY', 'Century', 'Answer 100 questions.', '💯', 'QUESTIONS_ANSWERED', 100, 50, true, 20, now(), now()),
          (gen_random_uuid(), 'GRINDER', 'Grinder', 'Answer 1000 questions.', '⚙️', 'QUESTIONS_ANSWERED', 1000, 250, true, 30, now(), now()),
          (gen_random_uuid(), 'WEEK_STREAK', 'Seven Days Straight', 'Study seven days in a row.', '🔥', 'STREAK', 7, 40, true, 40, now(), now()),
          (gen_random_uuid(), 'MONTH_STREAK', 'A Month Unbroken', 'Study thirty days in a row.', '🏔️', 'STREAK', 30, 150, true, 50, now(), now()),
          (gen_random_uuid(), 'MOCK_FINISHER', 'Sat The Paper', 'Complete a full mock exam.', '📝', 'MANUAL', NULL, 30, true, 60, now(), now()),
          (gen_random_uuid(), 'DISTINCTION', 'Distinction', 'Score 75% or more in a mock exam.', '🏆', 'MOCK_SCORE', 75, 100, true, 70, now(), now()),
          (gen_random_uuid(), 'POINTS_1000', 'Thousand Club', 'Earn 1000 points.', '💎', 'POINTS', 1000, 0, true, 80, now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
    op.drop_table("audit_logs")
    op.drop_table("analytics_events")
    op.drop_table("badges")
    op.drop_table("notifications")
    op.drop_table("content_access_rules")
    op.drop_table("document_pages")
    op.drop_table("content_documents")
