"""Platform operations: notifications, badges, analytics events, the audit log and
admin-editable settings.

WHY THESE FIVE LIVE TOGETHER

They are the tables that exist so the OWNER can run the platform without a database
client: an inbox to reach students, a badge catalogue to reward them, events to see
what is used, an audit trail of who changed what, and settings that change behaviour
without a deploy. None of them is a learning feature; all of them are operations.

WHAT IS NEW HERE, AND WHAT ALREADY EXISTED

``user_badges``, ``points_ledger`` and ``referrals`` already existed and are written
by the practice loop. Nothing in this module replaces them. What was missing was a
BADGE CATALOGUE (the existing table stores a ``badge_code`` string with no table
saying what codes exist or what they mean), a notifications inbox, events and an
audit trail. Those are added here.
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
    text,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidMixin


class Notification(Base, UuidMixin, TimestampMixin):
    """An in-app notification for one student.

    ONE ROW PER RECIPIENT, not one row per broadcast. Fan-out at write time is the
    choice that makes "mark as read" a single-row update that cannot be affected by
    another student, and it keeps the query for a student's inbox trivially
    indexable. The cost is N rows for a broadcast to N students, which is the right
    trade at this scale: an announcement to 20,000 students is 20,000 rows of a few
    hundred bytes, and the alternative - a recipient join table - makes every read
    a two-table join on the hot path.

    ``link_url`` is an in-app path, never an absolute URL. A notification is written
    by an admin, and an admin-supplied absolute URL in an email-like surface is a
    phishing vector. The route that creates one validates the leading slash.
    """

    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: SYSTEM | ANNOUNCEMENT | CONTENT | PAYMENT | ACHIEVEMENT
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa_text("'SYSTEM'")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    link_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Which audience rule produced this row, for the admin's history view.
    audience: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Groups the rows of one send so the admin can see "sent to 412 students".
    broadcast_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('SYSTEM','ANNOUNCEMENT','CONTENT','PAYMENT','ACHIEVEMENT')",
            name="ck_notification_kind",
        ),
        # The inbox query is "my unread, newest first" - this index is that query.
        Index("idx_notifications_inbox", "user_id", "created_at"),
        Index("idx_notifications_unread", "user_id", postgresql_where=sa_text("read_at IS NULL")),
        Index("idx_notifications_broadcast", "broadcast_id"),
    )


class Badge(Base, UuidMixin, TimestampMixin):
    """The catalogue of badges. ``user_badges.badge_code`` is the foreign key.

    A CODE, NOT AN ID, and that is deliberate: ``user_badges`` already stores a
    ``badge_code`` string and has rows in the wild. Making this table's primary key
    the code would break on a rename; adding an id here and matching on code means a
    badge can be renamed without rewriting earned history, and the award path in the
    practice loop keeps working unchanged.

    ``criteria_kind`` describes HOW it is earned so the platform can award it
    automatically: POINTS (total points at least ``criteria_value``), STREAK (days),
    QUESTIONS_ANSWERED, MOCK_SCORE (percent), MANUAL (an admin grants it).
    """

    __tablename__ = "badges"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    #: A short emoji or icon name. No image upload: a badge is a label, and adding a
    #: media pipeline for it would be a storage feature nobody asked for.
    icon: Mapped[str] = mapped_column(String(40), nullable=False, server_default=sa_text("'🏅'"))
    criteria_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa_text("'MANUAL'")
    )
    criteria_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    points_reward: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    #: Sort order for the achievements screen; low first.
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("100")
    )

    __table_args__ = (
        CheckConstraint(
            "criteria_kind IN ('MANUAL','POINTS','STREAK','QUESTIONS_ANSWERED','MOCK_SCORE',"
            "'CHAPTERS_COMPLETE')",
            name="ck_badge_criteria",
        ),
        Index("idx_badges_active", "is_active", "display_order"),
    )


class AnalyticsEvent(Base, UuidMixin, TimestampMixin):
    """One product event. The table the blueprint calls ``analytics_events``.

    APPEND-ONLY AND NEVER IN A CRITICAL PATH. Writes go through
    ``app/services/analytics.py::record_event``, which is deliberately synchronous
    (so a test can assert the row exists after the request) but wrapped so that a
    failure to record can never fail the request that triggered it. Losing an event
    is acceptable; losing a student's answer is not.

    ``user_id`` is nullable because some events happen before sign-in (a landing page
    visit that reaches the API). ``properties`` is JSONB because event shapes should
    not need a migration each time; the trade is that nothing here is queryable
    without casting, which is fine for aggregate dashboards and the wrong choice for
    anything that needs a constraint.
    """

    __tablename__ = "analytics_events"

    #: Dotted snake_case, e.g. ``practice.question_attempted``. Kept as a string
    #: rather than an enum: an event name is a product decision, and a migration per
    #: new event is how instrumentation stops happening.
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Denormalised from the users row at write time. Reporting "how many students
    #: used this" must keep working after a student deletes their account, and a
    #: join to a soft-deleted row cannot answer it.
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    properties: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    __table_args__ = (
        # Keyset pagination: the composite the tuple comparison
        # `(created_at, id) < (t, i)` is a range scan over. See migration
        # 0017. Declared here as well as in the migration so autogenerate does
        # not propose dropping it - an index that exists only in the database is
        # invisible to `alembic check`.
        Index("idx_analytics_events_created_id_desc", text("created_at DESC"), text("id DESC")),
        Index("idx_analytics_name_time", "name", "created_at"),
        Index("idx_analytics_user_time", "user_id", "created_at"),
    )


class AuditLog(Base, UuidMixin, TimestampMixin):
    """Who changed what, and when. Separate from analytics, on purpose.

    These are SECURITY records, not product telemetry: they answer "who deleted the
    Financial Reporting material" and they must not be droppable on a retention
    policy because the marketing dashboard stopped using them. Keeping them in
    ``analytics_events`` would tie the audit trail's lifetime to the analytics
    table's, which is the wrong coupling in a platform where one admin account
    controls every student's content.

    No foreign key to ``users`` on ``actor_user_id``: an audit row must survive the
    deletion of the actor. The actor's id and role are copied in as facts.
    """

    __tablename__ = "audit_logs"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)

    #: Verb_noun, past tense: ``document.uploaded``, ``user.role_changed``.
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: A sentence for the log view, written at the call site where the context is.
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    #: Before/after values for the fields that changed, where that is meaningful.
    changes: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # Keyset pagination: the composite the tuple comparison
        # `(created_at, id) < (t, i)` is a range scan over. See migration
        # 0017. Declared here as well as in the migration so autogenerate does
        # not propose dropping it - an index that exists only in the database is
        # invisible to `alembic check`.
        Index("idx_audit_logs_created_id_desc", text("created_at DESC"), text("id DESC")),
        Index("idx_audit_time", "created_at"),
        Index("idx_audit_actor", "actor_user_id", "created_at"),
        Index("idx_audit_action", "action", "created_at"),
        Index("idx_audit_target", "target_type", "target_id"),
    )


class PlatformSetting(Base, UuidMixin, TimestampMixin):
    """A key/value setting an admin can change without a deploy.

    PUBLIC BY CONSTRUCTION. Nothing secret belongs here: no API key, no connection
    string. The API that writes it is admin-only, and the API that reads the public
    subset is unauthenticated, so a secret written here would be a secret on a public
    endpoint. Payment and AI credentials are read from the environment
    (``app/core/config.py``) and this table cannot reach them.

    Values are JSONB so a flag can be a boolean, a number or a small object, and the
    shape is validated per key by the writer rather than by a column type.
    """

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: Free text for the settings screen: what this does, what values are allowed.
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("idx_settings_public", "is_public"),)
