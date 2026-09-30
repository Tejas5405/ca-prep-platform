"""Add applicable_attempts (TEXT[]) to questions and content_documents.

Revision ID: 0016_applicable_attempts
Revises: 0015_study_planner

SCOPE NOTE - READ BEFORE REVIEWING

The Phase 3 planning documents described an `applicable_attempt` VARCHAR
column on `study_resources` and `questions`. Neither exists: there is no
`study_resources` table (study material lives in `content_documents`), and no
`applicable_attempt` column exists on any table in this schema. Nothing here
reads, migrates or drops a column that is not present, and no backfill runs,
because there is nothing to backfill from.

What is added is only the new array column, on the two real tables that hold
taggable study material.

WHY AN ARRAY RATHER THAN A CHILD TABLE

The only question ever asked of it is "does this apply to attempt X?", which
is array containment. A child table would turn that one containment test into
a join and give back a second write on every tag change, for no query the
product actually runs.

THE GIN INDEX IS NOT OPTIONAL

Without it, `applicable_attempts @> ARRAY['May 2027']` is a sequential scan
over the whole question bank, and the filter that is supposed to make the
bank smaller makes it slower. GIN is what makes containment an index lookup.

existing rows keep an empty array
-------------------------------
The column defaults to '{}', NOT NULL, so existing rows are valid without a
rewrite. There is deliberately no data migration: no row in this database has
ever had an applicable attempt recorded, because the column to record it does
not exist. Backfilling from `ingestion_drafts.detected_attempt` would be
inventing a relationship the data does not support - that column describes
what the OCR read off a past paper, not which future attempt a question
belongs to.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_applicable_attempts"
down_revision: str | None = "0015_study_planner"
branch_labels = None
depends_on = None

# The two real tables that hold taggable study material. `study_resources`
# from the planning docs does not exist in this schema.
TAGGED_TABLES = ("questions", "content_documents")


def upgrade() -> None:
    for table in TAGGED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "applicable_attempts",
                postgresql.ARRAY(sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::text[]"),
            ),
        )
        op.create_index(
            f"idx_{table}_applicable_attempts",
            table,
            ["applicable_attempts"],
            postgresql_using="gin",
        )


def downgrade() -> None:
    for table in TAGGED_TABLES:
        op.drop_index(f"idx_{table}_applicable_attempts", table_name=table)
        op.drop_column(table, "applicable_attempts")
