"""Align users.auth_user_id with the ORM declaration.

Revision ID: 0004_auth_user_id
Revises: 0003_payments
Create Date: 2026-09-25

FOUND BY RUNNING THE MIGRATIONS AGAINST A REAL POSTGRESQL

``alembic check`` compares the ORM metadata against a live database. Until this
session no PostgreSQL had ever been reachable from the development environment, so
this comparison had never run - the schema had only ever been *asserted* by tests
that read ``Base.metadata`` and by rendering migrations to SQL.

It reported that ``users.auth_user_id`` carried BOTH:

  * a unique constraint ``users_auth_user_id_key`` (created by 0001), and
  * a non-unique index ``ix_users_auth_user_id`` (also created by 0001),

while the ORM declares a single **unique index**: ``unique=True, index=True``
expresses "indexed and unique" as one object.

WHY THIS MATTERS RATHER THAN BEING COSMETIC

``auth_user_id`` is the identity provider's ``sub`` claim (Firebase when this
migration was written, Supabase Auth now) - the single column that maps an
authenticated token to a row. Two indexes on it means every sign-in writes both,
for no benefit, on the hottest lookup in the system. And the drift is a trap: the
next person to run ``--autogenerate`` would get a migration that drops a unique
constraint and *replaces* it with an index, with no explanation of why.

The ORM's shape is kept (one unique index) and the database is moved to match it,
because a unique index enforces exactly what the constraint did - PostgreSQL
implements a unique constraint as a unique index in any case.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_auth_user_id"
down_revision = "0003_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The constraint is redundant with the unique index created below: both are
    # backed by a btree over the same column.
    op.drop_constraint("users_auth_user_id_key", "users", type_="unique")
    op.drop_index("ix_users_auth_user_id", table_name="users")
    op.create_index("ix_users_auth_user_id", "users", ["auth_user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_auth_user_id", table_name="users")
    op.create_index("ix_users_auth_user_id", "users", ["auth_user_id"])
    op.create_unique_constraint("users_auth_user_id_key", "users", ["auth_user_id"])
