"""Database helpers for the integration tests.

WHY THE ENGINE IS NOT A FIXTURE

These tests are plain synchronous functions that drive one coroutine each, so
there is no async test plugin in the dependency list and no shared event loop
between tests. An engine created once per session would therefore be bound to an
event loop that has already closed by the time the second test runs - which is
exactly the failure this module removes ("RuntimeError: Event loop is closed" on
connection teardown, surfacing as a mystery error in whichever test happened to
run second).

So the engine's lifetime is the test's lifetime: created inside the coroutine that
uses it, disposed before that coroutine returns. Slightly more connection setup per
test, and no cross-loop state at all.

WHY THIS MODULE REFUSES A GENERIC DATABASE NAME
================================================

``open_database`` issues ``TRUNCATE ... CASCADE`` over every table. That is a
destructive operation, and it is aimed at whatever ``TEST_DATABASE_URL`` names.

A name like ``caprep_test`` is not owned by this repository. This machine hosts
more than one CA checkout against one PostgreSQL server, and that was not
theoretical: a concurrent agent in a sibling checkout recreated ``caprep_test``
from a DIFFERENT migration chain while this suite was running, and 163 tests
failed with ``relation "users" does not exist``. The reverse accident is worse -
two suites sharing one database would truncate each other's fixtures mid-run,
producing failures that look like product bugs and are not.

So :func:`assert_disposable_database` enforces the convention in
``docs/ENVIRONMENT_ISOLATION.md`` at the only moment it can still prevent damage:
before the first TRUNCATE. A name that is not recognisably this project's own
disposable database is refused with a message naming the convention, rather than
being truncated in the hope that it was the right one.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

T = TypeVar("T")

#: Tables that must survive a truncation. ``alembic_version`` is the migration
#: history: dropping it would make every later ``alembic`` command believe the
#: database is empty and try to re-apply 0001.
KEEP = frozenset({"alembic_version"})

#: This repository's database-name prefix. Every disposable database it is
#: allowed to truncate starts with this, so no other checkout's data can be
#: reached by a name collision.
OWNED_PREFIX = "caprep_v2_"

#: Names that must never be truncated by this suite even though they are the
#: obvious "test database" spelling, because they are shared by convention rather
#: than owned. Refused explicitly so the error can explain WHY, rather than
#: simply reporting an unexpected name.
SHARED_NAMES = frozenset({"caprep", "caprep_test", "postgres", "template0", "template1"})


class UnsafeDatabaseError(RuntimeError):
    """Raised before any TRUNCATE when the target database is not ours to empty."""


def database_name(url: str) -> str:
    """The database name from a PostgreSQL URL, without the driver suffix."""
    without_query = url.split("?", 1)[0]
    return without_query.rstrip("/").rsplit("/", 1)[-1]


def assert_disposable_database(url: str) -> str:
    """Refuse to empty a database this repository does not own.

    Returns the database name when it is safe, so callers can log it. Raises
    :class:`UnsafeDatabaseError` otherwise - deliberately a hard failure rather
    than a skip, because a skip here would report green while silently running
    no database tests at all, which is the failure mode this project has already
    been bitten by once (a missing driver used to skip the whole suite).
    """
    name = database_name(url)
    if os.getenv("CAPREP_ALLOW_SHARED_TEST_DB") == "1":
        return name
    if name in SHARED_NAMES:
        raise UnsafeDatabaseError(
            f"Refusing to TRUNCATE the shared database {name!r}. This repository "
            f"owns only databases named '{OWNED_PREFIX}<purpose>' "
            f"(e.g. {OWNED_PREFIX}test). A generic name like {name!r} is shared "
            f"with other checkouts on this machine: during the P1 milestone a "
            f"concurrent agent in a sibling checkout recreated it from a different "
            f"migration chain and 163 tests failed spuriously. Set "
            f"TEST_DATABASE_URL to a '{OWNED_PREFIX}*' database, or set "
            f"CAPREP_ALLOW_SHARED_TEST_DB=1 if you have confirmed this database is "
            f"exclusively yours. See docs/ENVIRONMENT_ISOLATION.md."
        )
    if not name.startswith(OWNED_PREFIX):
        raise UnsafeDatabaseError(
            f"Refusing to TRUNCATE {name!r}: this repository only truncates "
            f"databases named '{OWNED_PREFIX}<purpose>'. Got {name!r} from the "
            f"configured test URL. See docs/ENVIRONMENT_ISOLATION.md."
        )
    return name


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run one coroutine on its own loop.

    Never wrap two of these - a second call inside the first is an error, and
    sharing a loop across tests is what caused the teardown failure above.
    """
    return asyncio.run(coro)


def async_url() -> str | None:
    """The test database URL in the driver form SQLAlchemy's async engine wants."""
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql+asyncpg://", 1)
    return url


def sync_url() -> str | None:
    """The same URL for tooling that connects synchronously (Alembic)."""
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    for prefix in ("postgres://", "postgresql+asyncpg://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql://", 1)
    return url


async def _truncate_all(session: AsyncSession) -> None:
    rows = await session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    tables = [name for (name,) in rows.all() if name not in KEEP]
    if tables:
        # CASCADE rather than ordering the deletes by hand: the test should not
        # have to know the foreign-key graph to get a clean database, and a new
        # table added later must not silently break isolation.
        await session.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await session.commit()


async def _current_database(session: AsyncSession) -> str:
    """The connected database's name, read from the server rather than parsed."""
    row = await session.execute(text("SELECT current_database()"))
    return str(row.scalar_one())


@asynccontextmanager
async def open_database(url: str) -> AsyncIterator[AsyncSession]:
    """A session on a database emptied of every row except the migration history."""
    engine = create_async_engine(url, future=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            # Checked against the SERVER's idea of the current database, not just
            # the URL we were handed: a URL can resolve to a different database
            # through a search_path, a proxy or a rewritten DSN, and the only
            # authority on what is about to be emptied is the server itself.
            live = await _current_database(session)
            assert_disposable_database(f"postgresql:///{live}")
            await _truncate_all(session)
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


@asynccontextmanager
async def observe(url: str) -> AsyncIterator[AsyncSession]:
    """A SECOND connection, for reading what a request wrote. Does not truncate.

    This exists so an assertion about durability can come from a connection that
    did not write the row. Reading through the writing session can be answered from
    its identity map or from an uncommitted transaction, which means the assertion
    would still pass with no commit at all - the failure mode that already hid a
    missing commit once in this repository.
    """
    engine = create_async_engine(url, future=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _with_database(url: str, body) -> Any:
    async with open_database(url) as session:
        return await body(session)


def run_in_database(url: str, body) -> Any:
    """Run ``body(session)`` against a clean database, on a fresh event loop.

    The session is passed in rather than opened by the test, so a test body needs
    no re-indentation and cannot accidentally capture a session from another loop.
    """
    return run(_with_database(url, body))


async def ping(url: str) -> None:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
