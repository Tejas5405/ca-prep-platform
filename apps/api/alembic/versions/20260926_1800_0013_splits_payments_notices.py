"""Intermediate splits, saved Razorpay keys, and law-notice records.

Revision ID: 0013_splits_payments_notices
Revises: 0012_attempt_question_version

Three tables, none of which rewrite an answer:

* subject_components — student-facing splits of the two combined Intermediate
  papers. The parent papers stay, because attempts and imports already point
  at them.
* payment_gateway_config — owner-entered Razorpay credentials. Not a settings
  row, because settings can be read back.
* law_notices — an administrator's citation and the questions it matched.
  answers_changed is constrained false.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_splits_payments_notices"
down_revision = "0012_attempt_question_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subject_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("parent_subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_filterable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["parent_subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("parent_subject_id", "code", name="uq_subject_component_code"),
        sa.UniqueConstraint("parent_subject_id", "slug", name="uq_subject_component_slug"),
    )
    op.create_index(
        "idx_subject_components_parent",
        "subject_components",
        ["parent_subject_id", "sort_order"],
    )

    op.add_column(
        "chapters",
        sa.Column("subject_component_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chapters_subject_component_id",
        "chapters",
        "subject_components",
        ["subject_component_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_chapters_subject_component", "chapters", ["subject_component_id"])

    op.add_column(
        "questions",
        sa.Column("subject_component_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_questions_subject_component_id",
        "questions",
        "subject_components",
        ["subject_component_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_questions_subject_component", "questions", ["subject_component_id"])

    op.create_table(
        "payment_gateway_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("key_id", sa.String(80), nullable=True),
        sa.Column("key_secret", sa.String(200), nullable=True),
        sa.Column("webhook_secret", sa.String(200), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("provider", name="uq_payment_gateway_provider"),
    )

    op.create_table(
        "law_notices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("citation", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "matched_question_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "affected_mock_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("answers_changed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("answers_changed = false", name="ck_law_notices_do_not_rewrite"),
    )
    op.create_index("idx_law_notices_created", "law_notices", ["created_at"])

    # Seed the four splits when the syllabus is already present. A fresh seed
    # runs the same mapping again; both paths are idempotent.
    op.execute(
        """
        INSERT INTO subject_components (
            id, parent_subject_id, code, display_name, slug, sort_order,
            is_filterable, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), s.id, v.code, v.display_name, v.slug, v.sort_order,
               true, true, now(), now()
        FROM subjects s
        JOIN (VALUES
            ('INT_TAX', 'DIRECT_TAX', 'Direct Tax', 'direct-tax', 1),
            ('INT_TAX', 'INDIRECT_TAX', 'Indirect Tax', 'indirect-tax', 2),
            ('INT_FMSM', 'FINANCIAL_MANAGEMENT', 'Financial Management', 'financial-management', 1),
            ('INT_FMSM', 'STRATEGIC_MANAGEMENT', 'Strategic Management', 'strategic-management', 2)
        ) AS v(parent_code, code, display_name, slug, sort_order)
          ON s.code = v.parent_code
        WHERE NOT EXISTS (
            SELECT 1 FROM subject_components c
            WHERE c.parent_subject_id = s.id AND c.code = v.code
        )
        """
    )
    op.execute(
        """
        UPDATE chapters c
        SET subject_component_id = sc.id
        FROM subjects s, subject_components sc,
             (VALUES
                ('INT_TAX', 'INT_TAX_01', 'DIRECT_TAX'),
                ('INT_TAX', 'INT_TAX_02', 'DIRECT_TAX'),
                ('INT_TAX', 'INT_TAX_03', 'INDIRECT_TAX'),
                ('INT_FMSM', 'INT_FMSM_01', 'FINANCIAL_MANAGEMENT'),
                ('INT_FMSM', 'INT_FMSM_02', 'STRATEGIC_MANAGEMENT')
             ) AS v(parent_code, chapter_code, component_code)
        WHERE c.subject_id = s.id
          AND s.code = v.parent_code
          AND c.code = v.chapter_code
          AND sc.parent_subject_id = s.id
          AND sc.code = v.component_code
        """
    )


def downgrade() -> None:
    op.drop_index("idx_law_notices_created", table_name="law_notices")
    op.drop_table("law_notices")
    op.drop_table("payment_gateway_config")
    op.drop_index("idx_questions_subject_component", table_name="questions")
    op.drop_constraint("fk_questions_subject_component_id", "questions", type_="foreignkey")
    op.drop_column("questions", "subject_component_id")
    op.drop_index("idx_chapters_subject_component", table_name="chapters")
    op.drop_constraint("fk_chapters_subject_component_id", "chapters", type_="foreignkey")
    op.drop_column("chapters", "subject_component_id")
    op.drop_index("idx_subject_components_parent", table_name="subject_components")
    op.drop_table("subject_components")
