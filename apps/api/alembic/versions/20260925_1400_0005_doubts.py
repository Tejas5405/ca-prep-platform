"""Doubt threads: doubts and doubt_replies.

Revision ID: 0005_doubts
Revises: 0004_auth_user_id

WHY TWO TABLES RATHER THAN AN `answer` COLUMN

Blueprint v3 §18.1 puts Doubts in P0. The naive shape - one doubt, one answer -
cannot represent a teacher adding to a colleague's answer, or the student
replying "I still don't follow step 3", which is the message that actually
resolves the doubt. A thread costs one more table and does not need re-modelling
the first week it is used.

The `accepted_reply_id` column carries NO foreign key, deliberately: pointing at
`doubt_replies.id` while `doubt_replies.doubt_id` points back at `doubts.id`
makes the pair a cycle whose inserts become order-dependent. Existence is
enforced in the service layer, and the column is only ever written from a reply
this transaction already loaded.

`doubts.status` is a VARCHAR with a CHECK rather than a native ENUM, matching the
rest of this schema: PostgreSQL ENUMs cannot have a value added inside a
transaction that also uses it, which makes them hostile to migrations and to the
test suite that rolls everything back.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_doubts"
down_revision = "0004_auth_user_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doubts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chapter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chapters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("priority", sa.String(10), nullable=False, server_default=sa.text("'NORMAL'")),
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("accepted_reply_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','ANSWERED','RESOLVED','CLOSED')", name="ck_doubts_status"
        ),
        sa.CheckConstraint("priority IN ('LOW','NORMAL','HIGH')", name="ck_doubts_priority"),
        # A one-word title is not answerable, and a queue of them is unanswerable
        # in bulk. The BODY is allowed to be short on purpose: "why is 3 wrong?"
        # is a legitimate doubt.
        sa.CheckConstraint("char_length(title) >= 5", name="ck_doubts_title_length"),
        sa.CheckConstraint("reply_count >= 0", name="ck_doubts_reply_count"),
    )
    op.create_index("idx_doubts_user_created", "doubts", ["user_id", "created_at"])
    # The moderator queue is sorted by recency within status, so this is the index
    # the queue actually uses.
    op.create_index("idx_doubts_status_activity", "doubts", ["status", "last_activity_at"])
    op.create_index("idx_doubts_subject", "doubts", ["subject_id"])

    op.create_table(
        "doubt_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "doubt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("doubts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_staff_answer", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("upvotes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("char_length(body) >= 2", name="ck_doubt_replies_body_length"),
        sa.CheckConstraint("upvotes >= 0", name="ck_doubt_replies_upvotes"),
    )
    op.create_index("idx_doubt_replies_doubt_created", "doubt_replies", ["doubt_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_doubt_replies_doubt_created", table_name="doubt_replies")
    op.drop_table("doubt_replies")
    op.drop_index("idx_doubts_subject", table_name="doubts")
    op.drop_index("idx_doubts_status_activity", table_name="doubts")
    op.drop_index("idx_doubts_user_created", table_name="doubts")
    op.drop_table("doubts")
