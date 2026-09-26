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


@asynccontextmanager
async def open_database(url: str) -> AsyncIterator[AsyncSession]:
    """A session on a database emptied of every row except the migration history."""
    engine = create_async_engine(url, future=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
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
