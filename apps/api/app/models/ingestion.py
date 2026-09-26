"""Ingestion job tracking, raw extractions and draft questions.

Blueprint v3 §11.1 defines the pipeline:

    upload -> signed URL -> private bucket -> POST /ingestion/jobs -> RQ job ->
    worker downloads -> PyMuPDF -> quality gate -> pdf2image + Tesseract
    fallback -> segment -> detect metadata -> raw + draft stored ->
    admin QA -> publish

Three tables support that, plus one rule from §11.4 that shapes all of them:

    "Never overwrite raw extracted text; store cleaned text separately."

WHY DRAFTS ARE NOT ROWS IN ``questions``

At extraction time we know the text, a source page, possibly a year and a marks
value, and an OCR confidence. We do NOT know the course, subject, chapter or
question type - those are editorial decisions made during QA.

But ``questions.course_id`` and ``questions.subject_id`` are NOT NULL, and
deliberately so: a question with no subject cannot be filtered, reported or
counted, and relaxing that to accommodate the pipeline would let subjectless
rows reach students. Guessing a subject during extraction is worse - it puts
confidently-wrong rows into a filter students rely on.

So drafts live in their own table. The benefit beyond the constraint problem is
that **nothing student-facing reads this table**, so an unreviewed OCR artifact
cannot leak into a student query through a future missing status filter. The
promotion from draft to question is an explicit, audited, human action.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidMixin
from app.models.enums import IngestionStage


class IngestionJob(Base, UuidMixin, TimestampMixin):
    """One PDF ingestion run.

    The job row is the unit of observability: the admin dashboard lists these,
    and every failure has to be explainable from this row alone. A failed job
    with no ``error_reason`` is an incident nobody can triage, which is why the
    pipeline records a reason on every failure path rather than only logging it.
    """

    __tablename__ = "ingestion_jobs"

    #: Who uploaded the source PDF.
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    #: Private bucket and object path. The raw file is never mutated or replaced,
    #: so a question's provenance stays checkable after the fact.
    bucket: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)

    #: Optional editorial intent, used as a default during metadata detection.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )

    stage: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=IngestionStage.QUEUED.value,
        server_default=sa_text("'QUEUED'"),
    )
    #: Append-only stage log: [{"stage": ..., "at": ..., "detail": ...}].
    #: A single mutable `stage` column cannot show that a job sat in
    #: OCR_FALLBACK for nine minutes, which is exactly what you need to know
    #: when OCR is slow.
    stage_history: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )

    #: Extraction tier actually used, recorded for cost analysis: tier 1 is
    #: cheap, tier 3 costs Tesseract time on every page.
    extraction_tier: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    page_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    mean_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    #: Set when the quality gate was not satisfied, so QA knows to look closely
    #: rather than treating every draft as equally trustworthy.
    needs_manual_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )

    drafts_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )

    #: EVERY failure path writes here. See the class docstring.
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "stage IN ('QUEUED','DOWNLOADING','EXTRACTING','QUALITY_GATE',"
            "'OCR_FALLBACK','SEGMENTING','DETECTING_METADATA','AWAITING_QA',"
            "'PUBLISHED','FAILED','REJECTED')",
            name="ck_ingestion_jobs_stage",
        ),
        CheckConstraint(
            "extraction_tier IS NULL OR extraction_tier BETWEEN 1 AND 3",
            name="ck_ingestion_jobs_tier",
        ),
        CheckConstraint(
            "mean_confidence IS NULL OR (mean_confidence >= 0 AND mean_confidence <= 1)",
            name="ck_ingestion_jobs_confidence",
        ),
        # The admin dashboard's primary query: what is waiting on me.
        Index("idx_ingestion_jobs_queue", "stage", "created_at"),
        Index("idx_ingestion_jobs_uploader", "uploaded_by", "created_at"),
        # Idempotency: re-queueing the same object must not create a second job.
        # Without this, a retried request produces two independent extraction
        # runs over the same PDF and duplicate drafts.
        UniqueConstraint("bucket", "storage_path", name="uq_ingestion_job_object"),
    )


class RawExtraction(Base, UuidMixin, TimestampMixin):
    """IMMUTABLE raw text as it came out of the extractor.

    Blueprint v3 §11.4: "Never overwrite raw extracted text; store cleaned text
    separately."

    This is the rule teams skip and later regret. When a question turns out to be
    wrong there are exactly two possibilities - the extraction mangled it, or an
    editor did - and without an untouched copy there is no way to tell which.
    That difference decides whether the fix is an OCR tuning problem or a
    training problem, and they are not the same work.

    ``cleaned_text`` lives in a separate column that the pipeline never writes to;
    any cleaning is a later, recorded transformation. Nothing in this table is
    ever updated after insert.
    """

    __tablename__ = "raw_extractions"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    #: Exactly what the extractor returned. Never mutated.
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Populated only by an explicit, recorded cleaning step.
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)

    #: sha256 of raw_text. Makes "has this content changed" a comparison rather
    #: than an assumption, and detects a mutation that bypassed the ORM.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "page_number", name="uq_raw_extraction_page"),
        CheckConstraint("tier BETWEEN 1 AND 3", name="ck_raw_extractions_tier"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_raw_extractions_confidence"
        ),
        Index("idx_raw_extractions_job", "job_id", "page_number"),
    )


class IngestionDraft(Base, UuidMixin, TimestampMixin):
    """A candidate question awaiting editorial review.

    Deliberately NOT a row in ``questions``. See the module docstring.

    ``promoted_question_id`` records the outcome of review, so the provenance
    chain runs the whole way: a published question can be traced back to the
    draft, the raw extraction page and the source PDF.
    """

    __tablename__ = "ingestion_drafts"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    raw_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_extractions.id", ondelete="SET NULL"),
        nullable=True,
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Page in the source PDF, for traceability back to the original paper.
    source_page: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    #: Machine-detected, advisory only. An editor confirms or corrects these.
    detected_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    detected_attempt: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detected_marks: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    detected_question_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detection_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    #: Placement, filled in BY A HUMAN during QA. Nullable here precisely because
    #: extraction cannot know it.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )

    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default=sa_text("'PENDING'")
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Set when the draft becomes a real question.
    promoted_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "review_status IN ('PENDING','APPROVED','REJECTED','DUPLICATE','MERGED')",
            name="ck_ingestion_drafts_review_status",
        ),
        CheckConstraint(
            "detected_question_type IS NULL OR detected_question_type IN "
            "('MCQ','MSQ','TRUE_FALSE','NUMERICAL','DESCRIPTIVE','CASE_STUDY')",
            name="ck_ingestion_drafts_detected_type",
        ),
        Index("idx_ingestion_drafts_job", "job_id", "source_page"),
        # The QA worklist.
        Index("idx_ingestion_drafts_review_queue", "review_status", "created_at"),
    )
