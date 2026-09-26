"""Remember which question version an attempt was marked against.

Revision ID: 0012_attempt_question_version
Revises: 0011_document_kind_vocabulary

A published question can be corrected. The score already given cannot follow the
new key, or a student who was right under the old Finance Act becomes wrong
because an editor typed a new rate. The version number on the attempt points at
``question_versions``. Null means the attempt predates that column; a correction
pins those rows to the snapshot taken before the edit.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_attempt_question_version"
down_revision = "0011_document_kind_vocabulary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "practice_attempts",
        sa.Column("question_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("practice_attempts", "question_version")
