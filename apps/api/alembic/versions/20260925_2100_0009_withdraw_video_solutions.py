"""Withdraw video solutions from the product.

Revision ID: 20260925_2100_0009
Revises: 20260925_1900_0008
Create Date: 2026-09-25

WHY A MIGRATION FOR A FEATURE THAT NEVER EXISTED

Video solutions were never implemented - there was no player, no provider, no
recording. What existed was a set of STRINGS that implied it: a storage prefix, an
entitlement named ``video_solutions`` on the Premium Plus tier, and a row in
``platform_settings``. The owner has directed that the feature be removed entirely
rather than re-labelled as planned, so the strings go too.

Leaving the settings row behind would be worse than leaving the code: the admin
settings screen lists every row it finds, so an operator would keep seeing
``features.video_solutions`` as a switch they could turn on for a feature that no
longer has anything behind it. A switch that does nothing is a lie in a settings
panel.

Downgrade restores the row so the migration is reversible, but not the entitlement:
code that no longer reads a flag cannot be resurrected by a database row.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_withdraw_video_solutions"
down_revision = "0008_content_library"
branch_labels = None
depends_on = None

KEY = "features.video_solutions"


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM platform_settings WHERE key = :key").bindparams(key=KEY))


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO platform_settings (id, key, value, description, is_public, created_at, updated_at)
            VALUES (gen_random_uuid(), :key, '{"value": false}'::jsonb,
                    'Video is not implemented. Left off deliberately.', true, now(), now())
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(key=KEY)
    )
