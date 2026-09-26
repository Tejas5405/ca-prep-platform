"""Request-scoped dependencies: database session, Redis and request id.

ASYNC / SYNC DECISION - deliberately made once, here.

SQLAlchemy's default ``Session`` is synchronous. Calling it from an ``async def``
endpoint blocks the event loop, which under load serialises every request behind
the slowest query and makes the whole service appear slow for no visible reason.
There are only two correct options:

  (a) ``sync`` engine + plain ``def`` endpoints - FastAPI runs those in a
      threadpool, so blocking is contained and safe.
  (b) ``async`` engine (asyncpg) + ``AsyncSession`` + ``async def`` everywhere.

Mixing them is the bug. This service uses (b): an async engine with
``AsyncSession``, so handlers must be ``async def`` and every database call must
be awaited. Redis is used through the async client for the same reason.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextvars import ContextVar

import redis.asyncio as aioredis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.db_urls import engine_kwargs

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_ctx.get()


# ----------------------------------------------------------------- database

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None):
    global _engine, _session_factory
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_async_engine(
            settings.database_url,
            **engine_kwargs(settings.database_url),
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory.

    Used by background workers and repositories that need to open their OWN
    short transactions rather than sharing a request-scoped session. A worker
    processing a multi-minute OCR job must not hold one transaction open for its
    whole duration.
    """
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession.

    The session is always closed. Callers decide when to commit, so a handler can
    compose several repository calls into one transaction.
    """
    get_engine()
    assert _session_factory is not None
    async with _session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# -------------------------------------------------------------------- redis

_redis_client: aioredis.Redis | None = None


def get_redis_client(settings: Settings | None = None) -> aioredis.Redis:
    """Return the shared async Redis client.

    Redis holds cache, quotas, rate limits, leaderboard indexes and RQ queues.
    It is never the source of truth for entitlements or points - see
    ``app.services.gamification``.
    """
    global _redis_client
    if _redis_client is None:
        settings = settings or get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url or "redis://localhost:6379",
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
        )
    return _redis_client


async def close_clients() -> None:
    """Release pooled connections on shutdown."""
    global _engine, _redis_client, _session_factory
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


# ------------------------------------------------------------------- request


async def request_context(request: Request) -> AsyncGenerator[None, None]:
    """Attach a request id for structured logging and error correlation.

    Blueprint v3 §17: structured logs should carry ``request_id``.
    """
    incoming = request.headers.get("x-request-id")
    rid = incoming or uuid.uuid4().hex[:16]
    token = request_id_ctx.set(rid)
    request.state.request_id = rid
    try:
        yield
    finally:
        request_id_ctx.reset(token)


DbSession = Depends(get_db)
