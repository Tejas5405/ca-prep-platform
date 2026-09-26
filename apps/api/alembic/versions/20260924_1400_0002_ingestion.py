"""Ingestion pipeline tables.

Revision ID: 0002_ingestion
Revises: 0001_initial
Create Date: 2026-09-24

Adds the three tables from blueprint v3 §11.1: job tracking, immutable raw
extractions, and draft questions awaiting QA.

The raw_extractions table is the one that carries the §11.4 rule - raw text is
never overwritten, cleaned text is stored separately - so nothing in the
application ever issues an UPDATE against raw_text. The content_hash column
exists to make a violation of that rule detectable rather than merely intended.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_ingestion"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "uploaded_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("bucket", sa.String(200), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column(
            "course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "subject_id",
            sa.UUID(),
            sa.ForeignKey("subjects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stage", sa.String(30), nullable=False, server_default="QUEUED"),
        # Append-only stage log. A single mutable stage column cannot show that a
        # job sat in OCR_FALLBACK for nine minutes, which is what you need to
        # know when OCR is slow.
        sa.Column(
            "stage_history",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("extraction_tier", sa.SmallInteger(), nullable=True),
        sa.Column("page_count", sa.SmallInteger(), nullable=True),
        sa.Column("mean_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "needs_manual_review", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("drafts_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "stage IN ('QUEUED','DOWNLOADING','EXTRACTING','QUALITY_GATE',"
            "'OCR_FALLBACK','SEGMENTING','DETECTING_METADATA','AWAITING_QA',"
            "'PUBLISHED','FAILED','REJECTED')",
            name="ck_ingestion_jobs_stage",
        ),
        sa.CheckConstraint(
            "extraction_tier IS NULL OR extraction_tier BETWEEN 1 AND 3",
            name="ck_ingestion_jobs_tier",
        ),
        sa.CheckConstraint(
            "mean_confidence IS NULL OR (mean_confidence >= 0 AND mean_confidence <= 1)",
            name="ck_ingestion_jobs_confidence",
        ),
        # Idempotency at the database level: the same object cannot be enqueued
        # twice, which is what stops a retried upload request producing two
        # independent extraction runs and duplicate drafts.
        sa.UniqueConstraint("bucket", "storage_path", name="uq_ingestion_job_object"),
    )
    op.create_index("idx_ingestion_jobs_queue", "ingestion_jobs", ["stage", "created_at"])
    op.create_index("idx_ingestion_jobs_uploader", "ingestion_jobs", ["uploaded_by", "created_at"])

    op.create_table(
        "raw_extractions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.SmallInteger(), nullable=False),
        # IMMUTABLE after insert. See the module docstring in app/models/ingestion.py.
        sa.Column("raw_text", sa.Text(), nullable=False),
        # Populated only by an explicit, recorded cleaning step - never by the
        # pipeline, which is what keeps the two comparable.
        sa.Column("cleaned_text", sa.Text(), nullable=True),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
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
        sa.UniqueConstraint("job_id", "page_number", name="uq_raw_extraction_page"),
        sa.CheckConstraint("tier BETWEEN 1 AND 3", name="ck_raw_extractions_tier"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_raw_extractions_confidence"
        ),
    )
    op.create_index("idx_raw_extractions_job", "raw_extractions", ["job_id", "page_number"])

    op.create_table(
        "ingestion_drafts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_extraction_id",
            sa.UUID(),
            sa.ForeignKey("raw_extractions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_page", sa.SmallInteger(), nullable=True),
        # Advisory, machine-detected. An editor confirms or corrects each one.
        sa.Column("detected_year", sa.SmallInteger(), nullable=True),
        sa.Column("detected_attempt", sa.String(40), nullable=True),
        sa.Column("detected_marks", sa.SmallInteger(), nullable=True),
        sa.Column("detected_question_type", sa.String(20), nullable=True),
        sa.Column("detection_confidence", sa.Numeric(4, 3), nullable=True),
        # Placement: filled in BY A HUMAN during QA. Nullable here precisely
        # because extraction cannot know it - see the note in
        # app/models/ingestion.py on why drafts are not rows in `questions`.
        sa.Column(
            "course_id", sa.UUID(), sa.ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "subject_id",
            sa.UUID(),
            sa.ForeignKey("subjects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chapter_id",
            sa.UUID(),
            sa.ForeignKey("chapters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column(
            "reviewed_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column(
            "promoted_question_id",
            sa.UUID(),
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            nullable=True,
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
            "review_status IN ('PENDING','APPROVED','REJECTED','DUPLICATE','MERGED')",
            name="ck_ingestion_drafts_review_status",
        ),
        sa.CheckConstraint(
            "detected_question_type IS NULL OR detected_question_type IN "
            "('MCQ','MSQ','TRUE_FALSE','NUMERICAL','DESCRIPTIVE','CASE_STUDY')",
            name="ck_ingestion_drafts_detected_type",
        ),
    )
    op.create_index("idx_ingestion_drafts_job", "ingestion_drafts", ["job_id", "source_page"])
    op.create_index(
        "idx_ingestion_drafts_review_queue", "ingestion_drafts", ["review_status", "created_at"]
    )


def downgrade() -> None:
    # Reverse dependency order: drafts reference raw extractions, which reference
    # jobs. Dropping in the wrong order fails on the foreign keys.
    op.drop_index("idx_ingestion_drafts_review_queue", table_name="ingestion_drafts")
    op.drop_index("idx_ingestion_drafts_job", table_name="ingestion_drafts")
    op.drop_table("ingestion_drafts")

    op.drop_index("idx_raw_extractions_job", table_name="raw_extractions")
    op.drop_table("raw_extractions")

    op.drop_index("idx_ingestion_jobs_uploader", table_name="ingestion_jobs")
    op.drop_index("idx_ingestion_jobs_queue", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
