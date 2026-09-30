"""The content library: uploaded source material and who may see it.

WHY THIS IS A SEPARATE TABLE FROM ``ingestion_jobs``

``ingestion_jobs`` models an OCR RUN: it is created when an upload starts, it has a
stage history, and it is finished when the pipeline stops. The library needs the
opposite shape - a record that outlives the run, is re-processed (possibly years
later, when the extractor improves), is versioned, is organised into the syllabus,
and carries the access rules that decide whether a student may read it. Folding
those onto the job row would mean a "job" that is not a job, and a re-run that
overwrites the provenance of the first.

RELATIONSHIP TO THE ORIGINAL FILE

The original PDF is NEVER deleted or replaced by its text. ``storage_path`` points at
the immutable object in the private bucket; ``document_pages`` holds what was read
out of it. Replacing a document creates a NEW row whose ``supersedes_id`` points at
the old one, so a question generated last year can still be traced to the exact file
it came from. That is the whole reason the library exists rather than a folder of
PDFs.

THE ACCESS MODEL

``content_documents.access_tier`` is the coarse gate (FREE / PREMIUM /
PREMIUM_PLUS), which is the same three-tier vocabulary the billing service already
uses for entitlements. ``content_access_rules`` is the fine one: an explicit grant or
denial scoped to a role, a user, a course or a plan. Both are evaluated on the
server in ``app/services/content_library.py``; there is no client-side filter whose
removal would expose anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UuidMixin
from app.models.enums import DocumentKind, sql_in_list


class ContentDocument(Base, UuidMixin, TimestampMixin, SoftDeleteMixin):
    """One uploaded source document, as the library sees it.

    ``status`` is the ADMIN-FACING lifecycle and is deliberately a superset of the
    pipeline's stages: ``UPLOADED`` means the bytes are in storage and no run has
    been requested; ``INDEXED`` means the text is searchable even though question
    generation has not happened; ``COMPLETED`` means the whole pipeline including
    indexing finished. The pipeline writes those transitions; this table keeps them
    so the admin screen can show 500 documents by state without walking job history.
    """

    __tablename__ = "content_documents"

    # ---------------------------------------------------------------- identity
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: STUDY_MATERIAL | NOTES | QUESTION_BANK | TEST_SERIES | REFERENCE | OTHER
    kind: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=sa_text("'STUDY_MATERIAL'")
    )

    # ------------------------------------------------------------ placement
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    module: Mapped[str | None] = mapped_column(String(120), nullable=True)
    syllabus_scheme: Mapped[str | None] = mapped_column(String(20), nullable=True)
    difficulty: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=sa_text("'MEDIUM'")
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb")
    )

    # ------------------------------------------------------------- the file
    bucket: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=sa_text("'application/pdf'")
    )
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: SHA-256 of the bytes, computed by the CLIENT before upload.
    #:
    #: Duplicate detection is the reason it exists: uploading 500 PDFs that overlap
    #: with 200 already in the library is the expected case, and comparing a hash
    #: costs nothing while comparing filenames catches almost none of it. It is also
    #: the integrity check - the same value is re-computed server-side after the
    #: bytes land, and a mismatch marks the document FAILED rather than indexing a
    #: truncated file.
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ------------------------------------------------------------- lifecycle
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa_text("'UPLOADED'")
    )
    #: The ingestion job that processed (or is processing) this document.
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_chars: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )
    ocr_pages: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    #: Mean extraction confidence across pages, 0-1. Null until a run finishes.
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Bumped when the file is replaced. The row for the old file keeps its own
    #: number, so "which version produced this question" has an answer.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_documents.id", ondelete="SET NULL"), nullable=True
    )

    # ---------------------------------------------------------------- access
    #: FREE | PREMIUM | PREMIUM_PLUS - the same vocabulary as billing entitlements.
    access_tier: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa_text("'PREMIUM'")
    )
    # Which exam attempts this document is applicable to, e.g. ['May 2027'].
    #
    # An ARRAY rather than a child table because the only question asked of it
    # is containment (`applicable_attempts @> ARRAY[:attempt]`), which the GIN
    # index idx_content_documents_applicable_attempts answers directly. Without that index
    # the filter meant to NARROW the bank widens into a full scan - so the index
    # is part of the column, not an optimisation to add later.
    #
    # Added by migration 0016_applicable_attempts. It defaults to an empty array,
    # so every existing row is already valid and no backfill was required.
    applicable_attempts: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default="{}",
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    #: Students may read but not download. Enforced by never handing out a
    #: download-disposition signed URL when this is false.
    allow_download: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )

    # --------------------------------------------------------------- uploader
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Groups the files of one bulk upload, so progress and retries are per batch.
    #: A UUID rather than a join table: the batch IS its documents, and a table
    #: holding only an id would be a row that says nothing.
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        # GIN, declared in the model and not only in the migration: an index that
        # exists in the database but not in the ORM is invisible to autogenerate,
        # so the next `alembic revision --autogenerate` emits a migration that
        # DROPS it. Verified with `alembic check`.
        Index(
            "idx_content_documents_applicable_attempts",
            "applicable_attempts",
            postgresql_using="gin",
        ),
        CheckConstraint(
            f"kind IN ({sql_in_list(DocumentKind)})",
            name="ck_document_kind",
        ),
        CheckConstraint(
            "status IN ('UPLOADED','QUEUED','PROCESSING','EXTRACTING','OCR_REQUIRED',"
            "'INDEXED','COMPLETED','FAILED','ARCHIVED')",
            name="ck_document_status",
        ),
        CheckConstraint(
            "access_tier IN ('FREE','PREMIUM','PREMIUM_PLUS')", name="ck_document_tier"
        ),
        CheckConstraint("difficulty IN ('EASY','MEDIUM','HARD')", name="ck_document_difficulty"),
        CheckConstraint("version >= 1", name="ck_document_version"),
        # One object path is one document. Without this, a retried bulk upload
        # creates a second row for the same bytes and every count in the admin
        # dashboard is wrong in a way nobody can explain.
        UniqueConstraint("bucket", "storage_path", name="uq_document_object"),
        Index("idx_documents_status", "status", "created_at"),
        Index("idx_documents_placement", "course_id", "subject_id", "chapter_id"),
        Index("idx_documents_batch", "batch_id"),
        # Partial: the overwhelming majority of rows have no checksum collision, so a
        # partial index keeps duplicate detection cheap at a hundred thousand rows.
        Index("idx_documents_checksum", "checksum_sha256"),
    )


class DocumentPage(Base, UuidMixin, TimestampMixin):
    """The text extracted from one page of one document.

    PAGE GRANULARITY, NOT DOCUMENT GRANULARITY. Full-text search has to be able to
    say "page 42 of the Financial Reporting study material" - a hit located in a
    400-page PDF with no page number is useless to a student and worse to an editor
    reviewing generated questions. Storage is cheap; a citation that cannot be
    followed is not.

    ``raw_text`` is what the extractor produced. It is never edited in place: the
    cleaning step writes ``text`` instead, so a bad question can be traced to a bad
    extraction rather than to a bad edit. That distinction has already paid for
    itself once in this codebase (see ``app/ocr/extractor.py``).
    """

    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    #: PYMUPDF | PYPDFPLUMBER | TESSERACT - which tier produced this page's text.
    extraction_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    #: True when the text layer was missing and this page went through OCR.
    used_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    #: GENERATED ALWAYS AS ... STORED. Declared with its expression here, not merely
    #: as a text column: the drift guard compares the model to the database on every
    #: run, and a model that says "Text" while the database says "tsvector" is exactly
    #: the kind of difference that would otherwise be discovered by a search returning
    #: nothing. SQLAlchemy never writes this column - Postgres computes it.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text, ''))", persisted=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_page"),
        CheckConstraint("page_number >= 1", name="ck_document_page_number"),
        Index("idx_document_pages_fts", "search_vector", postgresql_using="gin"),
        Index("idx_document_pages_doc", "document_id", "page_number"),
    )


class ContentAccessRule(Base, UuidMixin, TimestampMixin):
    """An explicit grant or denial of access to one document.

    TWO EFFECTS, ONE SHAPE. ``ALLOW`` is how a specific student is given a document
    their tier would not otherwise reach (a comp, a scholarship, a bundle bought over
    the phone). ``DENY`` is how one is taken away (a leaked file, a rights
    restriction on a publisher's material). Modelling only grants would leave the
    second case with no answer except deleting the document.

    Precedence is decided in ONE place, ``app/services/content_library.py``:
    DENY always beats ALLOW, and a rule's scope is read from ``scope`` rather than
    from which columns happen to be null, so a rule that matches nothing is a bug
    that can be seen in the admin list rather than a silent wildcard.
    """

    __tablename__ = "content_access_rules"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_documents.id", ondelete="CASCADE"), nullable=False
    )
    #: ROLE | USER | COURSE | PLAN | TIER
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    #: ALLOW | DENY
    effect: Mapped[str] = mapped_column(
        String(6), nullable=False, server_default=sa_text("'ALLOW'")
    )

    role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=True
    )
    plan_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(20), nullable=True)

    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "scope IN ('ROLE','USER','COURSE','PLAN','TIER')", name="ck_access_rule_scope"
        ),
        CheckConstraint("effect IN ('ALLOW','DENY')", name="ck_access_rule_effect"),
        # A rule must carry the value its scope names. A ROLE rule with a null role
        # is not "matches everyone by accident" - the CHECK makes it unrepresentable.
        CheckConstraint(
            "(scope = 'ROLE' AND role IS NOT NULL) OR (scope = 'USER' AND user_id IS NOT NULL) "
            "OR (scope = 'COURSE' AND course_id IS NOT NULL) "
            "OR (scope = 'PLAN' AND plan_code IS NOT NULL) "
            "OR (scope = 'TIER' AND tier IS NOT NULL)",
            name="ck_access_rule_target",
        ),
        # One rule per document per target: two ALLOWs for the same student would
        # make the admin list unreadable and the revoke button ambiguous.
        UniqueConstraint(
            "document_id",
            "scope",
            "role",
            "user_id",
            "course_id",
            "plan_code",
            "tier",
            name="uq_access_rule_target",
        ),
        Index("idx_access_rules_document", "document_id"),
        Index("idx_access_rules_user", "user_id"),
    )


def _exactly_one_audience_sql() -> str:
    """The CHECK expression for ``content_grants``: one audience, named by its scope.

    A row with two selectors would be read by one code path and ignored by another; a
    row with none would match EVERY viewer, which presents as "everyone can suddenly
    read this". The expression is kept byte-for-byte identical to the one in migration
    ``0010``; a test builds both and compares them, because a CHECK that exists only in
    Python is not a constraint.
    """
    columns = {"USER": "user_id", "ROLE": "role", "TIER": "tier", "PLAN": "plan_code"}
    clauses = []
    for scope, subject in columns.items():
        parts = [f"who_scope = '{scope}'", f"{subject} IS NOT NULL"]
        parts.extend(f"{other} IS NULL" for name, other in columns.items() if name != scope)
        clauses.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(clauses)


class ContentGrant(Base, UuidMixin, TimestampMixin):
    """A library-wide grant or denial of access. The admin's authority, modelled.

    WHY THIS IS NOT ``ContentAccessRule``

    ``ContentAccessRule`` answers "may this viewer read THIS document". It is a
    per-document override: a comp for one file, a rights restriction on one file. It
    cannot express the thing an owner actually does most often, which is "give this
    student the whole Financial Reporting subject" or "this cohort bought the course,
    so all its material opens for them". Doing that with per-document rules means one
    row per document - twenty rows for a subject, hundreds for a course - and the
    admin screen becomes a list of rows nobody can audit.

    So there are two SOURCES OF FACT and one precedence engine. ``document_filter``
    and ``can_read`` evaluate per-document rules and these grants in the same pass,
    with the same order: an explicit DENY beats an ALLOW, and either beats the tier
    ladder. Nothing else in the codebase decides access.

    WHO AND WHAT, AND WHY THE SECOND AXIS EXISTS

    A per-document rule only needs a "who" (the document is implied). A library-wide
    grant needs both, or "grant the course" is inexpressible:

      * ``who``  exactly one of ``user_id``, ``role``, ``tier``, ``plan_code``.
                 This is the axis the spec's list maps onto: individual (user),
                 role, subscription (tier), purchase/product (plan).
      * ``what`` all of ``course_id``, ``subject_id``, ``kind`` are OPTIONAL and
                 combine with AND. All null means the whole library, which is how a
                 blanket comp or a global deny is written.

    AN ENROLMENT IS A GRANT. "Grant access to a course for one student" is
    ``who=user`` plus ``what=course``. Rather than invent a second table with the same
    columns and a second precedence rule to keep in step, this table IS the enrolment
    record, and the access screen lists it as one. The ``reason`` field is what
    distinguishes a purchase from an enrolment from a comp, and the audit log records
    who wrote each one.
    """

    __tablename__ = "content_grants"

    #: USER | ROLE | TIER | PLAN  - which column below identifies the audience.
    who_scope: Mapped[str] = mapped_column(String(10), nullable=False)
    #: ALLOW | DENY
    effect: Mapped[str] = mapped_column(
        String(6), nullable=False, server_default=sa_text("'ALLOW'")
    )

    # -- who -------------------------------------------------------------------
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plan_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # -- what (all null = the whole library) ------------------------------------
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str | None] = mapped_column(String(30), nullable=True)

    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: A grant with an expiry applies only while it is in the future.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: REVOKED BY SETTING THIS, never by deleting the row: "who had access last month
    #: and why did it stop" is exactly the question an audit asks.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # THE SAME AUDIENCE MUST NOT BE GRANTED THE SAME THING TWICE - and the obvious
        # way to write that does not work.
        #
        # A UNIQUE constraint over these columns does NOT prevent duplicates, because the
        # narrow-scope columns are NULL for most grants and PostgreSQL treats NULL as
        # distinct from NULL in a unique index: "grant this student the whole library"
        # could be inserted repeatedly and the admin list would show several identical
        # rows with no way to tell which one the revoke button means. A test caught this;
        # the constraint was silently permissive rather than failing loudly.
        #
        # NULLS NOT DISTINCT (PostgreSQL 15+) is the fix: two rows are now duplicates when
        # they agree on everything the operator can see. Written as an INDEX rather than a
        # constraint because that is where the option lives.
        Index(
            "uq_grant_target",
            "who_scope",
            "user_id",
            "role",
            "tier",
            "plan_code",
            "course_id",
            "subject_id",
            "kind",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        # DECLARED HERE AS WELL AS IN THE MIGRATION, deliberately. A CHECK that exists
        # only in SQL is invisible to the ORM, so a test that builds the model in memory
        # could not see it and the schema-contract test below could not assert it. The
        # expressions are byte-for-byte the migration's.
        CheckConstraint("who_scope IN ('USER','ROLE','TIER','PLAN')", name="ck_grant_who_scope"),
        CheckConstraint("effect IN ('ALLOW','DENY')", name="ck_grant_effect"),
        # One clause per scope: the scope names the column, that column must be set,
        # and the other three must be null. Written as SQL here rather than as an
        # application check so an INSERT from any path is refused.
        CheckConstraint(
            _exactly_one_audience_sql(),
            name="ck_grant_exactly_one_subject",
        ),
        Index("idx_grants_user", "user_id"),
        Index("idx_grants_course", "course_id"),
        Index("idx_grants_plan", "plan_code"),
    )
