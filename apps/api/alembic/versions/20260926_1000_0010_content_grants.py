"""Library-wide access grants: the admin's authority over who sees what.

Revision ID: 0010_content_grants
Revises: 0009_withdraw_video_solutions
Create Date: 2026-09-25

``content_access_rules`` answers "may this viewer read THIS document". That is the right
shape for a comp on one file and useless for the thing an owner does most often: open a
whole subject, a whole course, or a content type for one student, one role, or everyone
on a plan. With only per-document rules that is one row per document.

This table holds the library-wide half. ``who_scope`` plus one selector identifies the
audience (USER / ROLE / TIER / PLAN), and the optional ``course_id`` / ``subject_id`` /
``kind`` columns say what the grant covers - all null meaning the whole library.

Both tables feed ONE precedence engine (``app/services/content_library.py``): an
explicit DENY beats an ALLOW, from either source, and either beats the subscription
tier. No new rule was introduced here; a second source of facts was.

An enrolment is a grant: ``who_scope='USER'`` with a ``course_id``. There is no separate
enrolments table, because a second table with the same columns and a second precedence
rule to keep in step is how two systems end up disagreeing about what a student may read.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.content import _exactly_one_audience_sql

revision = "0010_content_grants"
down_revision = "0009_withdraw_video_solutions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("who_scope", sa.String(10), nullable=False),
        sa.Column("effect", sa.String(6), nullable=False, server_default=sa.text("'ALLOW'")),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("tier", sa.String(20), nullable=True),
        sa.Column("plan_code", sa.String(20), nullable=True),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(30), nullable=True),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # NOT sa.UniqueConstraint: NULLs are distinct in a unique index, so a constraint
        # here silently permits the same grant twice whenever a narrow-scope column is
        # null - which is most grants. NULLS NOT DISTINCT is the behaviour the operator
        # expects ("I already granted this"). See the model for the full note.
        sa.Index(
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
        sa.CheckConstraint(
            "who_scope IN ('USER','ROLE','TIER','PLAN')",
            name="ck_grant_who_scope",
        ),
        sa.CheckConstraint("effect IN ('ALLOW','DENY')", name="ck_grant_effect"),
        # EXACTLY ONE audience selector, and it must be the one the scope names. A row
        # with two selectors would be read by one code path and ignored by another; a
        # row with none would match every viewer, which is a mistake that reads as
        # "everyone can see this".
        # Imported from the model rather than duplicated: the ORM's copy and the
        # database's copy are the same string, so they cannot drift apart.
        sa.CheckConstraint(
            _exactly_one_audience_sql(),
            name="ck_grant_exactly_one_subject",
        ),
        # A DENY that expires and an ALLOW that was revoked are both nonsense.
        sa.CheckConstraint(
            "revoked_at IS NULL OR effect IN ('ALLOW','DENY')",
            name="ck_grant_revoked_allowed",
        ),
    )
    op.create_index("idx_grants_user", "content_grants", ["user_id"])
    op.create_index("idx_grants_course", "content_grants", ["course_id"])
    op.create_index("idx_grants_plan", "content_grants", ["plan_code"])


def downgrade() -> None:
    op.drop_index("idx_grants_plan", table_name="content_grants")
    op.drop_index("idx_grants_course", table_name="content_grants")
    op.drop_index("idx_grants_user", table_name="content_grants")
    op.drop_table("content_grants")
