"""Alembic environment.

Blueprint v3 §2 chooses Alembic explicitly ("Python-native ... Alembic
migrations"), replacing the schema-push workflow of the superseded ORM.

MIGRATIONS MUST BE REVIEWED, NOT AUTO-APPLIED BLINDLY. ``--autogenerate`` does
not detect everything that matters here, and specifically:
  * it will not create the expression-based GIN full-text index (added by hand)
  * it does not see changes to CHECK constraints reliably
  * it may propose dropping a column when a rename was intended, which loses data

So the workflow is: autogenerate -> READ THE DIFF -> correct it -> apply.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``app`` importable when alembic runs from apps/api.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import every model module so Base.metadata is fully populated. Without these
# imports autogenerate produces an empty migration - a classic and confusing
# failure that looks like "alembic is broken".
# Imports every model module, registering all tables on Base.metadata.
# autogenerate only sees what has been imported, so this single line is what
# keeps the migration set in step with app/models/.
import app.models  # noqa: F401
from app.core.db_urls import resolve_migration_url
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Resolve the database URL.

    Delegates to ``app.core.db_urls`` so that this file - which Alembic EXECUTES
    rather than imports, and which therefore cannot be unit tested - holds no
    logic at all. The precedence rule (DIRECT_DATABASE_URL wins, because
    migrations must not run through a transaction pooler) is tested directly in
    tests/test_db_urls.py.
    """
    return resolve_migration_url()


#: Indexes that exist ONLY as raw SQL in migration 0001, and therefore cannot be
#: seen in ``Base.metadata``:
#:
#:   idx_questions_fts   GIN over to_tsvector('english', text)
#:   idx_questions_trgm  GIN trigram over text
#:
#: A SQLAlchemy ``Index`` cannot express the first (its expression is not a column
#: reference), which is why both were created with ``op.execute``. The consequence
#: is that autogenerate sees them in the database, finds nothing matching in the
#: models, and proposes DROPPING THEM - a migration that would silently delete
#: full-text search from the platform, and which would look like routine cleanup in
#: a diff. They are ignored here instead.
RAW_SQL_INDEXES = frozenset({"idx_questions_fts", "idx_questions_trgm"})


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Keep raw-SQL-managed objects out of autogenerate comparisons.

    Scoped precisely: only for indexes that exist in the database (``reflected``)
    with nothing to compare against in the models, and only for the two names
    above. A model-declared index of the same name would still be compared, so
    this cannot hide a genuine rename or removal.
    """
    if type_ == "index" and reflected and compare_to is None and name in RAW_SQL_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting. Used to review a migration."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
