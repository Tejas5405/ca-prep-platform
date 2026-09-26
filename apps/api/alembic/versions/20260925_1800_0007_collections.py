"""Collections and their membership rows.

Revision ID: 0007_collections
Revises: 0006_mock_answers

THE LAST NAMED ROUTE IN BLUEPRINT §7.3.

`POST /collections` was the only endpoint in the inventory with nothing behind it:
`app/services/collections.py` validated smart-collection filters and seeded the LDR
list, and there was no table, no route and no screen. The build audit recorded that
as a genuine gap rather than a deviation.

Two tables, because a collection is a container and a membership is a row:

  * ``collections`` - name, kind (MANUAL or SMART), a validated JSONB filter
    predicate for smart ones, and a denormalised ``item_count`` so a list of
    twenty collections does not run twenty COUNT queries.
  * ``collection_questions`` - one row per (collection, question), carrying the
    student's note and a position.

WHY THE UNIQUE NAME IS PER STUDENT

`uq_collection_user_name` makes "Costing weak spots" impossible to create twice for
the same account. Two collections with one name is the state the feature exists to
avoid, so the database refuses it rather than the UI warning about it.

WHY THE CHECK CONSTRAINT

`ck_collection_filters_match_kind` ties the shape to the kind: a SMART collection
must have filters (without them it silently means "the whole bank"), and a MANUAL
one must not (filters on a manual collection would be read by nothing, which is the
quiet kind of dead data that misleads the next person to open the file).

The LDR list is NOT a table here. "Marked for later" is already a boolean on
`user_question_progress` (`is_marked_for_review`), written by the practice loop
itself. Duplicating it would create two answers to "is this question bookmarked",
and the wrong one would win whichever write path ran last.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_collections"
down_revision = "0006_mock_answers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "kind",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'MANUAL'::character varying"),
        ),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_collection_user_name"),
        sa.CheckConstraint("kind IN ('MANUAL','SMART')", name="ck_collection_kind"),
        sa.CheckConstraint(
            "(kind = 'SMART' AND filters IS NOT NULL) OR (kind = 'MANUAL' AND filters IS NULL)",
            name="ck_collection_filters_match_kind",
        ),
        sa.CheckConstraint("item_count >= 0", name="ck_collection_item_count"),
    )
    op.create_index("idx_collections_user", "collections", ["user_id", "created_at"])

    op.create_table(
        "collection_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("collection_id", "question_id", name="uq_collection_question"),
    )
    op.create_index(
        "idx_collection_questions_list",
        "collection_questions",
        ["collection_id", "position", "created_at"],
    )
    op.create_index("idx_collection_questions_question", "collection_questions", ["question_id"])


def downgrade() -> None:
    op.drop_index("idx_collection_questions_question", table_name="collection_questions")
    op.drop_index("idx_collection_questions_list", table_name="collection_questions")
    op.drop_table("collection_questions")
    op.drop_index("idx_collections_user", table_name="collections")
    op.drop_table("collections")
