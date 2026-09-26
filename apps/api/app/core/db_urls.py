"""Where the database lives, and how to talk to it.

ONE MODULE FOR A DECISION THAT WAS SPREAD ACROSS THREE

The URL scheme, the pooler rules and the migration target were each handled in a
different place: the scheme in ``alembic/env.py``, the engine options in
``core/dependencies.py``, and the choice of URL nowhere at all. That is how a
project ends up with migrations running through a transaction pooler - each piece
looks correct on its own.

WHY MIGRATIONS AND THE APP MAY NEED DIFFERENT URLS

A managed PostgreSQL pooler in TRANSACTION mode cannot hold a session across
statements. ``alembic upgrade`` takes an advisory lock, runs a multi-statement
migration inside one transaction, and expects the same server connection
throughout. Through the pooler it can be handed a different connection between
statements, which leaves a half-applied schema - the worst possible outcome, since
the version table and the actual schema then disagree.

So: the API uses the pooled URL (it does not care about session affinity), and
Alembic uses the direct one. ``DIRECT_DATABASE_URL`` is optional, and absent means
"same as the app", which is correct for a local server and for any deployment
without a pooler.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings

#: Ports on which a PostgreSQL pooler listens in TRANSACTION mode.
#:
#: 6543 is Supabase's transaction pooler (their direct connection and session
#: pooler are both 5432), and 6432 is PgBouncer's usual port - the same constraint
#: applies to any of them.
POOLER_PORTS = frozenset({6543, 6432})


def is_pooled_database(url: str) -> bool:
    """Is this URL a transaction-mode pooler?

    Detected rather than configured. A flag would be forgotten exactly once - at
    the moment the deployment URL changes - and the failure it causes is
    intermittent (``prepared statement "_pg3_3" does not exist``), which is the
    worst kind to debug months later.
    """
    if "pooler" in url.lower():
        return True
    try:
        return urlsplit(url).port in POOLER_PORTS
    except ValueError:
        return False


def engine_kwargs(url: str) -> dict[str, Any]:
    """Engine options that depend on WHERE the database lives.

    DISABLED PREPARED STATEMENTS BEHIND A POOLER

    A transaction-mode pooler hands a client a different server connection between
    statements. Prepared statements are per-connection, so a name prepared on one
    and executed on another does not exist - and the error only appears under
    concurrency, in production.

    Both drivers are covered because this project uses both: the API runs psycopg
    (``prepare_threshold=None``) while the test harness builds an asyncpg engine
    (``statement_cache_size=0``).

    Correctness is unaffected either way: these only stop the drivers from caching
    prepared statements, at a small cost in round trips.
    """
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,  # avoids serving requests on a dead connection
        "pool_size": 5,
        "max_overflow": 10,
        "echo": False,
    }
    if not is_pooled_database(url):
        return kwargs

    kwargs["connect_args"] = (
        {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
        if "+asyncpg" in url
        # psycopg 3: `None` disables the prepared-statement threshold entirely.
        else {"prepare_threshold": None}
    )
    return kwargs


def with_sync_driver(url: str) -> str:
    """Add the driver SQLAlchemy 2 needs to a bare PostgreSQL URL.

    Render, Supabase and Heroku all hand out ``postgresql://`` or the old
    ``postgres://``. Both are silently assumed to be psycopg2 by SQLAlchemy, which
    is not installed - so the failure is an import error at connect time rather
    than anything that names the URL.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql+psycopg://", 1)
    return url


def resolve_migration_url(settings: Settings | None = None) -> str:
    """The URL Alembic should use, in precedence order.

    ``DIRECT_DATABASE_URL`` (environment) beats the settings object beats
    ``DATABASE_URL`` beats the default, so an operator can override a deployment
    without editing the app's configuration - and CI can point migrations at a
    throwaway container.
    """
    settings = settings or get_settings()
    url = (
        os.getenv("DIRECT_DATABASE_URL")
        or settings.direct_database_url
        or os.getenv("DATABASE_URL")
        or settings.database_url
    )
    return with_sync_driver(url)
