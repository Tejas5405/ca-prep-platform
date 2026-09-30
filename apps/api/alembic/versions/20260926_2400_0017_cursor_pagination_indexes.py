"""Composite (created_at DESC, id DESC) indexes for keyset pagination.

Revision ID: 0017_cursor_pagination_indexes
Revises: 0016_applicable_attempts

WHY THESE EXIST, AND WHY THE OLD ONES WERE NOT ENOUGH

Cursor pagination filters on the tuple `(created_at, id) < (t, id)` and orders
by the same pair descending. For that to be an index range scan rather than a
sort, there must be an index whose leading columns are exactly that pair, in
that order.

The indexes already on these tables cannot serve it:

  * ``idx_audit_time (created_at)`` has no tiebreaker, so a keyset query still
    has to sort within each group of rows sharing a timestamp.
  * ``idx_analytics_user_time (user_id, created_at)`` and
    ``idx_progress_user_created (user_id, created_at)`` are correct for a
    per-user list but useless for an unscoped admin view, and again stop at
    ``created_at``.

The old indexes are NOT dropped. They still answer the per-user and
per-actor queries they were built for; these are additional, for the
unscoped newest-first walk.

DESC IS EXPLICIT BECAUSE THE WALK IS DESCENDING

Postgres can scan a btree backwards, so a plain ASC index would work for a DESC
query. It is written DESC anyway so the planner's cost estimate matches the
real access pattern without relying on that inference - and because the
index is named for the direction it serves, so a future reader does not have to
re-derive it.

CONCURRENTLY IS DELIBERATELY NOT USED

CREATE INDEX CONCURRENTLY avoids taking a write lock, but it cannot run inside
a transaction and Alembic runs each migration in one. On these tables at their
current size the build is short, and Render runs `alembic upgrade head` as a
preDeployCommand before the new version takes traffic. If these tables ever
grow large enough for the lock to matter, the fix is a separate
CONCURRENTLY migration outside the normal chain, not an in-transaction
approximation of it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0017_cursor_pagination_indexes"
down_revision: str | None = "0016_applicable_attempts"
branch_labels = None
depends_on = None

# (index name, table). Declared in the same order in every model, so
# `alembic check` sees no drift.
TARGETS = (
    ("idx_audit_logs_created_id_desc", "audit_logs"),
    ("idx_analytics_events_created_id_desc", "analytics_events"),
    ("idx_practice_attempts_created_id_desc", "practice_attempts"),
)


def upgrade() -> None:
    for name, table in TARGETS:
        op.create_index(
            name,
            table,
            [sa.text("created_at DESC"), sa.text("id DESC")],
            unique=False,
        )


def downgrade() -> None:
    for name, table in reversed(TARGETS):
        op.drop_index(name, table_name=table)
