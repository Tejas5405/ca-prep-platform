"""Widen ck_course_level to admit the self-paced SET level.

Revision ID: 0014_widen_course_level_set
Revises: 6c3f7b0a0c13

Self-Paced Online Modules ship as SET A-D: a student who is not ready for a
scheduled attempt studies a self-paced module first. Those modules are real
courses, they appear in the catalogue, and they need a level so the existing
"which level is this course" filters keep working. There was no value for
them, so the CHECK refused the row and the insert failed at the database with
a constraint violation naming three accepted values.

Adding a value to a CHECK constraint is cheap to do and expensive to discover
is missing, because the failure surfaces as a 500 on a write rather than as a
missing course in a list.

WHY THE CONSTRAINT IS REPLACED RATHER THAN ALTERED

Postgres cannot widen a CHECK in place; there is no ALTER CONSTRAINT for
CHECK. The constraint is dropped and re-added, so there is a brief window with
no constraint at all. That window is inside a single transaction, which is why
this is safe: the table is not concurrently written by a different session
between the two statements, so no row can slip through unvalidated. The
downgrade reverses it exactly.
"""

from __future__ import annotations

from alembic import op

revision: str = "0014_widen_course_level_set"
down_revision: str | None = "6c3f7b0a0c13"
branch_labels = None
depends_on = None

OLD_LEVELS = "level IN ('FOUNDATION','INTERMEDIATE','FINAL')"
NEW_LEVELS = "level IN ('FOUNDATION','INTERMEDIATE','FINAL','SET')"


def upgrade() -> None:
    op.drop_constraint("ck_course_level", "courses", type_="check")
    op.create_check_constraint("ck_course_level", "courses", NEW_LEVELS)


def downgrade() -> None:
    # Rows written as SET since the upgrade are removed by the constraint
    # re-appearing. That is the intended, honest downgrade: the old schema
    # cannot represent them, and silently rewriting them to FOUNDATION would
    # put a self-paced module on a level it does not belong to.
    op.drop_constraint("ck_course_level", "courses", type_="check")
    op.create_check_constraint("ck_course_level", "courses", OLD_LEVELS)
