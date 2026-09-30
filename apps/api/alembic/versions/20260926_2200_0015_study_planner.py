"""Study planner: a dated plan per student per target attempt.

Revision ID: 0015_study_planner
Revises: 0014_widen_course_level_set

Two tables, because a plan is a container and a scheduled item is a row:

  * ``study_plans`` - one per (student, target attempt). Holds the paper list
    the student is working towards and the daily time budget.
  * ``study_plan_items`` - one row per chapter on a date.

WHY original_date IS NOT THE SAME COLUMN AS planned_date

Dragging an item must be an ordinary edit, and a student who reschedules three
chapters and then wants the original dates back must be able to ask. Keeping
the date the item was first placed on means "what did the plan look like on
day one" stays answerable after a hundred reschedules. It is written once and
moved only by an explicit reset.

WHY THE PLAN IS SCOPED TO AN ATTEMPT

`uq_study_plan_user_attempt` makes one plan per (user, target_attempt). Two
plans for the same target is the state that makes a planner untrustworthy -
which of the two is the real one? The database refuses it rather than the UI
warning about it and being ignored.

`target_attempt` is free text ('May 2027', 'Nov 2026'). ICAI attempt names are
a published set, but hard-coding them here would turn every schedule change
into a migration. The CHECK only rejects the empty string, which is the one
value that would silently match nothing.

PAPERS IS JSONB, NOT A JOIN TABLE

`papers` is an ordered list of mock-test ids forming the target paper set. It
is read as a whole and written as a whole, never queried by element, so a join
table would buy nothing except a second write on every reorder.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_study_planner"
down_revision: str | None = "0014_widen_course_level_set"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_attempt", sa.String(20), nullable=False),
        sa.Column(
            "papers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("hours_per_day", sa.Numeric(3, 1), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "target_attempt", name="uq_study_plan_user_attempt"),
        sa.CheckConstraint("btrim(target_attempt) <> ''", name="ck_study_plan_target_attempt"),
    )
    op.create_index("idx_study_plans_user", "study_plans", ["user_id"])

    op.create_table(
        "study_plan_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("original_date", sa.Date(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # One row per chapter per plan. Re-adding a chapter already scheduled
        # would otherwise produce two rows that both render, and marking one
        # done would leave the other claiming it is still outstanding.
        sa.UniqueConstraint("plan_id", "chapter_id", name="uq_study_plan_item_chapter"),
        # An item is either done, skipped, or neither - never both. A row that
        # is both would render as complete and skipped at the same time.
        sa.CheckConstraint(
            "NOT (done_at IS NOT NULL AND skipped_at IS NOT NULL)",
            name="ck_study_plan_item_terminal",
        ),
    )
    op.create_index("idx_study_plan_items_plan", "study_plan_items", ["plan_id"])
    op.create_index("idx_study_plan_items_date", "study_plan_items", ["planned_date"])


def downgrade() -> None:
    # Items first: they hold the FK to plans, and dropping a referenced table
    # first leaves a constraint pointing at nothing.
    op.drop_index("idx_study_plan_items_date", table_name="study_plan_items")
    op.drop_index("idx_study_plan_items_plan", table_name="study_plan_items")
    op.drop_table("study_plan_items")
    op.drop_index("idx_study_plans_user", table_name="study_plans")
    op.drop_table("study_plans")
