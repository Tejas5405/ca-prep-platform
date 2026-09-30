"""Carry a verbatim source quote with every ingested question.

Revision ID: 0018_source_quote
Revises: 0017_cursor_pagination_indexes

WHY THIS COLUMN EXISTS

The product's core claim is that CA content can be TRUSTED, and the specific
failure it guards against is a question that reads plausibly and is wrong
because Indian tax law changed and nobody noticed.

A `source_quote` is a verbatim substring of the extracted page the question was
derived from. An editor reviewing a draft can hold the PDF and the question side
by side and see whether the question is actually supported by the text. Without
it, review is a judgement call; with it, review is a comparison.

It is NULLABLE ON PURPOSE. Manually authored questions have no PDF behind them
and must not be forced to invent one. The invariant is conditional, not global:

    a question that came from ingestion MUST carry a quote

which is enforced in `app/services/draft_review.py`, not by a NOT NULL that
would reject every hand-written question.

The column is on `ingestion_drafts` rather than only on `questions` because the
quote is a property of the EXTRACTION. The editor sees and edits it while the
draft is still under review, and the value is copied onto the question at
promotion time. Storing it only on `questions` would lose it exactly when it is
most needed - before anyone has decided to trust the question.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018_source_quote"
down_revision: str | None = "0017_cursor_pagination_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_drafts", sa.Column("source_quote", sa.Text(), nullable=True))
    op.add_column("questions", sa.Column("source_quote", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("questions", "source_quote")
    op.drop_column("ingestion_drafts", "source_quote")
