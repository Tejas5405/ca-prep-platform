"""Study tools that are records, not claims.

A forum post is a student writing to other students. A formula row is an editor's
note. A support ticket does not send email. A marketplace listing does not take
payment. A proctor event is a browser signal, not a camera. The check constraints
below exist so a later writer cannot flip a boolean into a claim these tables
cannot support.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidMixin


class StudyGroup(Base, UuidMixin, TimestampMixin):
    __tablename__ = "study_groups"

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class StudyGroupMember(Base, UuidMixin, TimestampMixin):
    __tablename__ = "study_group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study_groups.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MEMBER", server_default=sa.text("'MEMBER'")
    )

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_study_group_member"),
        CheckConstraint("role IN ('OWNER','MEMBER')", name="ck_study_group_member_role"),
        Index("idx_study_group_members_user", "user_id"),
    )


class MentorshipRequest(Base, UuidMixin, TimestampMixin):
    __tablename__ = "mentorship_requests"

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="OPEN", server_default=sa.text("'OPEN'")
    )
    mentor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','MATCHED','CLOSED')",
            name="ck_mentorship_status",
        ),
        Index("idx_mentorship_student", "student_id", "created_at"),
    )


class ForumThread(Base, UuidMixin, TimestampMixin):
    __tablename__ = "forum_threads"

    title: Mapped[str] = mapped_column(String(140), nullable=False)
    subject_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (Index("idx_forum_threads_created", "created_at"),)


class ForumPost(Base, UuidMixin, TimestampMixin):
    __tablename__ = "forum_posts"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forum_threads.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_forum_posts_thread", "thread_id", "created_at"),)


class FormulaEntry(Base, UuidMixin, TimestampMixin):
    """An editor's study note. Publishing it does not make it an ICAI extract."""

    __tablename__ = "formula_entries"

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    subject_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("idx_formula_entries_published", "published"),)


class GlossaryEntry(Base, UuidMixin, TimestampMixin):
    __tablename__ = "glossary_entries"

    term: Mapped[str] = mapped_column(String(120), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(40), nullable=False, default="editor", server_default=sa.text("'editor'")
    )
    published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )

    __table_args__ = (
        UniqueConstraint("term", name="uq_glossary_term"),
        CheckConstraint("source IN ('platform','editor')", name="ck_glossary_source"),
        Index("idx_glossary_published", "published"),
    )


class SupportTicket(Base, UuidMixin, TimestampMixin):
    __tablename__ = "support_tickets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(140), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OPEN", server_default=sa.text("'OPEN'")
    )
    email_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )

    __table_args__ = (
        CheckConstraint("status IN ('OPEN','ACKNOWLEDGED','CLOSED')", name="ck_support_status"),
        CheckConstraint("email_sent = false", name="ck_support_does_not_send_email"),
        Index("idx_support_tickets_status", "status", "created_at"),
    )


class Experiment(Base, UuidMixin, TimestampMixin):
    __tablename__ = "experiments"

    key: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    variants: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text('\'["A", "B"]\'::jsonb')
    )

    __table_args__ = (UniqueConstraint("key", name="uq_experiment_key"),)


class ExperimentAssignment(Base, UuidMixin, TimestampMixin):
    __tablename__ = "experiment_assignments"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    variant: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint("experiment_id", "user_id", name="uq_experiment_assignment"),
    )


class MarketplaceListing(Base, UuidMixin, TimestampMixin):
    __tablename__ = "marketplace_listings"

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    plan_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    price_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    takes_payment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("takes_payment = false", name="ck_marketplace_does_not_charge"),
        CheckConstraint(
            "price_paise IS NULL OR price_paise >= 0",
            name="ck_marketplace_price_non_negative",
        ),
        Index("idx_marketplace_active", "active"),
    )


class ProctorEvent(Base, UuidMixin, TimestampMixin):
    __tablename__ = "proctor_events"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mock_attempts.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('TAB_HIDDEN','TAB_VISIBLE','WINDOW_BLUR',"
            "'COPY_ATTEMPT','PASTE_ATTEMPT')",
            name="ck_proctor_event_type",
        ),
        Index("idx_proctor_events_attempt", "attempt_id", "created_at"),
    )


class CalendarEvent(Base, UuidMixin, TimestampMixin):
    __tablename__ = "calendar_events"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="student", server_default=sa.text("'student'")
    )

    __table_args__ = (
        CheckConstraint("source = 'student'", name="ck_calendar_source_is_student"),
        Index("idx_calendar_events_user", "user_id", "starts_at"),
    )


class PomodoroSession(Base, UuidMixin, TimestampMixin):
    __tablename__ = "pomodoro_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    reported_by_client: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("true")
    )

    __table_args__ = (
        CheckConstraint("minutes BETWEEN 1 AND 120", name="ck_pomodoro_minutes"),
        CheckConstraint("reported_by_client = true", name="ck_pomodoro_is_self_reported"),
        Index("idx_pomodoro_user", "user_id", "created_at"),
    )


class ExamModeSitting(Base, UuidMixin, TimestampMixin):
    __tablename__ = "exam_mode_sittings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mock_attempts.id", ondelete="CASCADE"), nullable=False
    )
    hides_answers_until_submit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("true")
    )
    camera_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )

    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_exam_mode_attempt"),
        CheckConstraint(
            "hides_answers_until_submit = true",
            name="ck_exam_mode_hides_answers",
        ),
        CheckConstraint("camera_used = false", name="ck_exam_mode_no_camera"),
    )
