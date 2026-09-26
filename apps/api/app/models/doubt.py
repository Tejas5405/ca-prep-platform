"""Doubt threads - blueprint v3 §18.1 (Doubts are P0) and §11.4.

WHY THREADS AND NOT A SINGLE ANSWER COLUMN

The first version of this feature in any codebase is "a doubt has an answer".
It stops working the moment a second teacher wants to add something, or the
student replies "I still don't follow step 3" - which is the reply that actually
resolves the doubt. So a doubt has many replies, one of which can be marked as the
accepted answer, and the thread carries its own status.

STATUS IS DERIVED FROM THE THREAD, NOT SET BY THE STUDENT ALONE. A student can
close their own doubt, but only staff can mark it RESOLVED after answering -
otherwise "resolved" becomes a button that makes a queue look clean.
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
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidMixin


class Doubt(Base, UuidMixin, TimestampMixin):
    """A student's question about a specific piece of content."""

    __tablename__ = "doubts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Any of these may be null: a doubt can be about a chapter, about one
    #: question, or about the syllabus in general. Placeholder ids would be worse
    #: than nulls, because they silently join to nothing.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OPEN", server_default="'OPEN'"
    )
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default="NORMAL", server_default="'NORMAL'"
    )

    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: No FK on purpose: the accepted reply belongs to this doubt, and the FK pair
    #: (doubts.accepted_reply_id -> doubt_replies.id) and
    #: (doubt_replies.doubt_id -> doubts.id) would be a cycle that makes inserts
    #: order-dependent. Existence is enforced in the service layer.
    accepted_reply_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Denormalised so "most recent activity" sorts without touching replies. The
    #: moderator queue is sorted by it, and that query runs on every page load.
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','ANSWERED','RESOLVED','CLOSED')", name="ck_doubts_status"
        ),
        CheckConstraint("priority IN ('LOW','NORMAL','HIGH')", name="ck_doubts_priority"),
        # A one-word title ("doubt") is not answerable, and a queue of them is
        # unanswerable in bulk. The constraint is on the TITLE only: the body is
        # allowed to be short, because "why is 3 wrong?" is a legitimate doubt.
        CheckConstraint("char_length(title) >= 5", name="ck_doubts_title_length"),
        CheckConstraint("reply_count >= 0", name="ck_doubts_reply_count"),
        Index("idx_doubts_user_created", "user_id", "created_at"),
        Index("idx_doubts_status_activity", "status", "last_activity_at"),
        Index("idx_doubts_subject", "subject_id"),
    )


class DoubtReply(Base, UuidMixin, TimestampMixin):
    """One message in a doubt thread."""

    __tablename__ = "doubt_replies"

    doubt_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("doubts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: Copied from the author's role AT THE TIME OF WRITING. Roles change, and a
    #: historical thread that suddenly shows every reply as staff-authored is
    #: misleading when someone reviews how a doubt was handled.
    is_staff_answer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    upvotes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        CheckConstraint("char_length(body) >= 2", name="ck_doubt_replies_body_length"),
        CheckConstraint("upvotes >= 0", name="ck_doubt_replies_upvotes"),
        Index("idx_doubt_replies_doubt_created", "doubt_id", "created_at"),
    )
