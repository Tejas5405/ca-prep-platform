"""Health endpoints.

Blueprint v3 §17.1 specifies FOUR endpoints: /health, /health/db, /health/redis
and /health/storage. Uptime monitors poll /health.

FAILURE SEMANTICS ARE THE POINT OF THIS MODULE.

A health endpoint that returns HTTP 200 with ``{"status": "ok"}`` while the
database is unreachable is worse than having no health endpoint at all, because
the monitor reports green and nobody investigates. That exact bug existed in the
superseded implementation: the framework's check helper only evaluated top-level
keys, so nested ``{"group": {"status": "down"}}`` payloads never failed the check
and every dependency looked healthy forever.

Rules enforced here:
  * a dependency that is down returns HTTP 503, never 200
  * the body's ``status`` field agrees with the HTTP status line
  * the failing dependency is named, so the alert is actionable
  * a missing optional integration is NOT a failure (see /health/storage)
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.dependencies import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_STARTED_AT = time.time()


def _degraded(checks: dict[str, dict[str, object]], response: Response) -> dict[str, object]:
    """Mark the response unhealthy and return the failing detail.

    Kept as one helper so no endpoint can accidentally report 200 while naming a
    dependency as down.
    """
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "error", "checks": checks}


@router.get("/health", summary="Liveness")
async def health(response: Response) -> dict[str, object]:
    """Process liveness only. Deliberately does NOT check dependencies.

    Render and the uptime monitor use this to decide whether the process needs
    restarting; a database blip must not trigger a restart loop.
    """
    return {
        "status": "ok",
        "checks": {
            "process": {
                "status": "up",
                "uptime_seconds": round(time.time() - _STARTED_AT, 1),
            }
        },
    }


@router.get("/health/db", summary="Database connectivity")
async def health_db(response: Response, settings: Settings = None) -> dict[str, object]:
    from app.core.dependencies import get_engine

    settings = settings or get_settings()
    try:
        engine = get_engine(settings)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "checks": {"postgres": {"status": "up"}}}
    except Exception as exc:  # noqa: BLE001 - health must never raise
        logger.error("health/db failed: %s", exc)
        return _degraded({"postgres": {"status": "down", "error": type(exc).__name__}}, response)


@router.get("/health/redis", summary="Redis connectivity")
async def health_redis(response: Response) -> dict[str, object]:
    try:
        client = get_redis_client()
        pong = await client.ping()
        if not pong:
            raise RuntimeError("ping returned false")
        return {"status": "ok", "checks": {"redis": {"status": "up"}}}
    except Exception as exc:  # noqa: BLE001
        logger.error("health/redis failed: %s", exc)
        return _degraded({"redis": {"status": "down", "error": type(exc).__name__}}, response)


@router.get("/health/storage", summary="Supabase Storage connectivity")
async def health_storage(response: Response) -> dict[str, object]:
    """Storage check with a deliberate distinction.

    Missing credentials mean the integration is simply not configured yet
    (a normal state in local development), so this reports ``not_configured``
    and stays HTTP 200. Credentials that are present but failing are a real
    outage and return 503.

    Collapsing those two cases into one is how teams either ignore a genuine
    outage or get paged for a feature they have not enabled.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        return {
            "status": "ok",
            "checks": {"storage": {"status": "not_configured", "bucket": settings.storage_bucket}},
        }

    try:
        from app.integrations.supabase_storage import SupabaseStorage

        storage = SupabaseStorage(settings)
        exists = await storage.bucket_exists(settings.storage_bucket)
        if not exists:
            return _degraded({"storage": {"status": "down", "error": "bucket_missing"}}, response)
        return {
            "status": "ok",
            "checks": {"storage": {"status": "up", "bucket": settings.storage_bucket}},
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("health/storage failed: %s", exc)
        return _degraded({"storage": {"status": "down", "error": type(exc).__name__}}, response)
