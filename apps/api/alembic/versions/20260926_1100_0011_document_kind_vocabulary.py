"""Align the document-kind vocabulary with the kinds the console actually offers.

Revision ID: 0011_document_kind_vocabulary
Revises: 0010_content_grants
Create Date: 2026-09-26

The bug this fixes was reported by the database, not by a test.

``ck_document_kind`` accepted ``STUDY_MATERIAL, NOTES, QUESTION_BANK, TEST_SERIES,
REFERENCE, OTHER``. The bulk-upload and metadata screens in the web console offered
``STUDY_MATERIAL, NOTES, PAST_PAPER, QUESTION_BANK, SYLLABUS, OTHER``. The API accepted
any string up to 30 characters, so nothing objected before the insert: choosing "Past
paper" for a past paper - the second most obvious thing to upload to a CA preparation
library - produced an IntegrityError and a 500 whose body was a PostgreSQL constraint
message.

The two vocabularies are now one list (``app.models.enums.DocumentKind``), used by the
model's CHECK, by the request schemas' validation, and by this migration. Two kinds are
ADDED (``PAST_PAPER``, ``SYLLABUS``), none are removed, so no existing row is affected
and the downgrade has nothing to migrate back - a row written under the old constraint
is still valid under the new one.

Adding a value to a CHECK constraint is a constraint swap, which is transactional and
takes a lock only long enough to scan the table. On the size this table reaches in
production (a 500-document upload is one batch) that is milliseconds, so no online
rewrite dance is warranted here.
"""

from __future__ import annotations

from alembic import op

from app.models.enums import DocumentKind, sql_in_list

revision = "0011_document_kind_vocabulary"
down_revision = "0010_content_grants"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_document_kind"


def _swap(kinds: str) -> None:
    # ALTER TABLE ... DROP CONSTRAINT then ADD CONSTRAINT in one transaction: a
    # concurrent writer either sees the old constraint or the new one, never neither.
    op.execute(f"ALTER TABLE content_documents DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE content_documents ADD CONSTRAINT {CONSTRAINT} CHECK (kind IN ({kinds}))"
    )


def upgrade() -> None:
    _swap(sql_in_list(DocumentKind))


def downgrade() -> None:
    # The previous vocabulary, written out rather than derived, because a migration must
    # mean the same thing after the enum changes again. The guard makes the downgrade
    # fail loudly instead of silently: if a PAST_PAPER or SYLLABUS row exists, that row
    # cannot satisfy the old constraint and PostgreSQL would refuse the ADD anyway. This
    # states why.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM content_documents WHERE kind IN ('PAST_PAPER', 'SYLLABUS')
            ) THEN
                RAISE EXCEPTION
                    'content_documents holds PAST_PAPER or SYLLABUS rows; reclassify them '
                    'before downgrading to the 0010 vocabulary';
            END IF;
        END
        $$;
        """
    )
    _swap("'STUDY_MATERIAL','NOTES','QUESTION_BANK','TEST_SERIES','REFERENCE','OTHER'")
