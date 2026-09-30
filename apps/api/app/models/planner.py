"""Study planner: a dated plan per student per target attempt.

Blueprint v3 §12. A student working towards 'May 2027' needs to know WHICH
chapter on WHICH day, not a list of weak topics; the second question is the
one a planner answers and a progress dashboard does not.

TWO TABLES, BECAUSE A PLAN IS NOT A ROW

``StudyPlan`` is the container: the target attempt, the target paper set and
the daily time budget. ``StudyPlanItem`` is one scheduled chapter. Putting
the items in a JSONB column on the plan would make "what is due this week"
- the only query this feature exists to answer - a scan of one wide document
instead of an indexed lookup.

original_date IS NOT A DUPLICATE OF planned_date
------------------------------------------------
A reschedule is an ordinary edit: planned_date moves, original_date does not.
It is written once, when the chapter is first placed, and changed only by an
explicit reset. That is what lets "what did the plan look like on day one"
stay answerable after a hundred drags, and it is the reason a student who
reschedules everything is not left with a plan that has no history at all.

TERMINAL STATES ARE EXCLUSIVE
-----------------------------
An item is done, skipped, or pending - never both. The database enforces it
because an item that is simultaneously complete and skipped renders as two
contradictory rows in the same list, and whichever the UI prefers will be the
one that disagrees with the other.

CASCADE, NOT RESTRICT
---------------------
Deleting a plan deletes its items. That is what a student means by "delete
my plan". Deleting a chapter cascades too: an item pointing at a chapter that
no longer exists is a row nothing can render.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidMixin


class StudyPlan(Base, UuidMixin, TimestampMixin):
    """One student's plan for one target attempt, e.g. 'May 2027'."""

    __tablename__ = "study_plans"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Free text rather than an enum: ICAI attempt names are a published set,
    # but hard-coding them turns every schedule change into a migration. Only
    # the empty string is rejected - that is the value that would silently
    # match nothing.
    target_attempt: Mapped[str] = mapped_column(String(20), nullable=False)
    # Ordered list of mock-test ids forming the target paper set. Read and
    # written whole, never queried by element - see the module docstring.
    papers: Mapped[list] = mapped_column(
        JSONB(astext_type=Text()),
        nullable=False,
        default=list,
        server_default="[]",
    )
    hours_per_day: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False)

    items: Mapped[list[StudyPlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # One plan per (student, attempt). Two plans for the same target is the
        # state that makes a planner untrustworthy - which of them is real?
        # Refused here rather than warned about in the UI and ignored.
        UniqueConstraint("user_id", "target_attempt", name="uq_study_plan_user_attempt"),
        CheckConstraint("btrim(target_attempt) <> ''", name="ck_study_plan_target_attempt"),
        Index("idx_study_plans_user", "user_id"),
    )


class StudyPlanItem(Base, UuidMixin, TimestampMixin):
    """One chapter scheduled on one date inside a plan."""

    __tablename__ = "study_plan_items"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    # The date this item was FIRST placed. Survives every reschedule.
    original_date: Mapped[date] = mapped_column(Date, nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped[StudyPlan] = relationship(back_populates="items")

    __table_args__ = (
        # A chapter can be scheduled once per plan. Re-adding one already on
        # the plan would create two rows that both render, and marking one done
        # would leave the other still claiming to be outstanding.
        UniqueConstraint("plan_id", "chapter_id", name="uq_study_plan_item_chapter"),
        CheckConstraint(
            "NOT (done_at IS NOT NULL AND skipped_at IS NOT NULL)",
            name="ck_study_plan_item_terminal",
        ),
        Index("idx_study_plan_items_plan", "plan_id"),
        Index("idx_study_plan_items_date", "planned_date"),
    )
