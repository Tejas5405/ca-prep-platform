"""Curriculum hierarchy: Course -> Subject -> Chapter -> Topic.

Blueprint v3 §9.1. The hierarchy is intentionally four levels because CA content
needs it: a subject such as Direct Tax Laws contains chapters (e.g. "Deductions
from Gross Total Income"), which contain topics. Mock generation and progress
analytics both operate at chapter granularity, and the study planner needs topic
granularity for coverage estimation.

SYLLABUS SCHEME SCOPING: scheme lives on ``courses`` (and is copied onto every
question row for filtering). See ``SyllabusScheme`` for why this is load-bearing:
the transition from the older ICAI scheme to the New Scheme changed CA Final from
eight papers to six, so a subject list is only meaningful together with the scheme
it belongs to.
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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UuidMixin
from app.models.enums import SyllabusScheme


class Course(Base, UuidMixin, TimestampMixin):
    """A CA course level, scoped to a syllabus scheme."""

    __tablename__ = "courses"

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    syllabus_scheme: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SyllabusScheme.UNMAPPED.value,
        server_default=sa_text("'UNMAPPED'::character varying"),
    )
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa_text("true"),
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    subjects: Mapped[list[Subject]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("code", "syllabus_scheme", name="uq_course_code_scheme"),
        CheckConstraint("level IN ('FOUNDATION','INTERMEDIATE','FINAL')", name="ck_course_level"),
        CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')",
            name="ck_course_scheme",
        ),
        # A course whose validity window has passed must not be offered to new
        # students; this index backs that filter directly.
        Index("idx_courses_active_scheme", "is_active", "syllabus_scheme"),
    )


class Subject(Base, UuidMixin, TimestampMixin):
    """A paper within a course."""

    __tablename__ = "subjects"

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Group I / Group II. Both nil for Foundation, where papers are not grouped.
    group_name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Paper number within the course (1-6 under the new CA Final scheme).
    paper_number: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: Relative weight for the study planner's time allocation.
    syllabus_weight: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default=sa_text("100"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa_text("true"),
    )

    course: Mapped[Course] = relationship(back_populates="subjects")
    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )
    components: Mapped[list[SubjectComponent]] = relationship(
        "SubjectComponent",
        back_populates="parent_subject",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("course_id", "code", name="uq_subject_course_code"),
        CheckConstraint(
            "group_name IS NULL OR group_name IN ('GROUP_I','GROUP_II')",
            name="ck_subject_group",
        ),
        CheckConstraint("syllabus_weight > 0", name="ck_subject_weight_positive"),
        Index("idx_subjects_course_active", "course_id", "is_active"),
    )


class SubjectComponent(Base, UuidMixin, TimestampMixin):
    """A student-facing split of a combined Intermediate paper.

    The parent subject stays. Historical attempts, mocks and imported questions
    still point at it. Students at Intermediate pick a component instead, and a
    question with no component does not appear in both splits.
    """

    __tablename__ = "subject_components"

    parent_subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    is_filterable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("true")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("true")
    )

    parent_subject: Mapped[Subject] = relationship(back_populates="components")

    __table_args__ = (
        UniqueConstraint("parent_subject_id", "code", name="uq_subject_component_code"),
        UniqueConstraint("parent_subject_id", "slug", name="uq_subject_component_slug"),
        Index("idx_subject_components_parent", "parent_subject_id", "sort_order"),
    )


class Chapter(Base, UuidMixin, TimestampMixin, SoftDeleteMixin):
    """A chapter within a subject. The unit of planning and analytics."""

    __tablename__ = "chapters"

    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    #: Set when this chapter belongs to one Intermediate split. Null on every
    #: other paper, and null on a chapter that has not been classified.
    subject_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subject_components.id", ondelete="SET NULL"),
        nullable=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    #: Exam weightage, 1-10. Drives "focus areas" ordering and planner time.
    weightage: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=5,
        server_default=sa_text("'5'::smallint"),
    )
    #: Realistic study minutes for a first pass. Feeds the capacity check.
    estimated_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=120,
        server_default=sa_text("120"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa_text("true"),
    )

    subject: Mapped[Subject] = relationship(back_populates="chapters")
    topics: Mapped[list[Topic]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("subject_id", "code", name="uq_chapter_subject_code"),
        CheckConstraint("weightage BETWEEN 1 AND 10", name="ck_chapter_weightage_range"),
        CheckConstraint("estimated_minutes > 0", name="ck_chapter_minutes_positive"),
        Index("idx_chapters_subject_sequence", "subject_id", "sequence"),
        Index("idx_chapters_subject_component", "subject_component_id"),
    )


class Topic(Base, UuidMixin, TimestampMixin):
    """The finest granularity of the syllabus. Questions attach here."""

    __tablename__ = "topics"

    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa_text("true"),
    )

    chapter: Mapped[Chapter] = relationship(back_populates="topics")

    __table_args__ = (
        UniqueConstraint("chapter_id", "code", name="uq_topic_chapter_code"),
        Index("idx_topics_chapter_sequence", "chapter_id", "sequence"),
    )
