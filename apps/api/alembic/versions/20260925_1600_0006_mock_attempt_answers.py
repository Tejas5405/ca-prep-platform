"""Store the submitted answers on the attempt.

Revision ID: 0006_mock_answers
Revises: 0005_doubts

WHY A COLUMN AND NOT A TABLE

The mocks feature could score a paper but could not reconstruct it afterwards:
`submit` received the answer key from the CLIENT, scored it, and threw the result
away, and the report route returned 404 unconditionally. So a student could take a
paper and then never see which questions they got wrong - the single most useful
thing a mock exam produces.

Storing the answers is what makes the report possible. A JSONB column on
`mock_attempts` is the right shape rather than a child table: an answer is written
once, read once, and always read together with its attempt. It is never queried
across attempts, never joined, and nothing references it. The score is deliberately
NOT stored here - it stays in the typed columns so it can be aggregated and ranked
in SQL.

The default is an empty array, not NULL: "submitted with nothing answered" and
"never submitted" are different states, and a NULL would collapse them into one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_mock_answers"
down_revision = "0005_doubts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mock_attempts",
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("mock_attempts", "answers")
