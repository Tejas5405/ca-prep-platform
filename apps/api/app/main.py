"""FastAPI application entrypoint.

Blueprint v3 §3: React talks to FastAPI over HTTPS; FastAPI validates
Supabase-issued access tokens, applies business rules, reads/writes PostgreSQL,
uses Redis for cache/quotas/jobs, and stores files in Supabase Storage.

Deployed on Render as the API service. The RQ worker is a SEPARATE Render
service running ``python -m app.workers.rq_worker`` - see infra/render.yaml.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    access,
    admin,
    assistant,
    campus,
    collections,
    content,
    curriculum,
    doubts,
    gamification,
    health,
    ingestion,
    mocks,
    notifications,
    payments,
    planner,
    practice,
    progress,
    revision,
    search,
    studio,
    users,
)
from app.core.config import get_settings
from app.core.dependencies import close_clients, request_id_ctx
from app.core.envelope import problem
from app.core.permissions import PermissionDenied, forbidden
from app.integrations.supabase_storage import StorageError

settings = get_settings()


def configure_logging() -> None:
    """Structured JSON logging for Render (v3 §17).

    Render captures stdout, so logs go there rather than to a file. ``request_id``
    is carried through a contextvar so every line for a request can be
    correlated without threading it through every function signature.
    """
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
        '"request_id":"%(request_id)s","msg":"%(message)s"}',
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    class _RequestIdFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.request_id = request_id_ctx.get()
            return True

    for handler in logging.getLogger().handlers:
        handler.addFilter(_RequestIdFilter())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger = logging.getLogger("app.startup")

    missing = settings.missing_critical_secrets()
    if missing:
        # Warn rather than crash: a missing optional integration must not take
        # the API down, and /health reports the true state per dependency.
        logger.warning("Missing configuration: %s", ", ".join(missing))

    if settings.is_production:
        # A permissive CORS policy in production is a real vulnerability, so it
        # is refused outright rather than logged and ignored.
        permissive = not settings.cors_origins or "*" in settings.cors_origins
        if permissive:
            raise RuntimeError("CORS_ORIGINS must be an explicit allow-list in production")

    logger.info("API starting (env=%s)", settings.environment)
    yield
    await close_clients()
    logger.info("API shutdown complete")


app = FastAPI(
    title="CA Prep Platform API",
    version="3.0.0",
    description="Question bank, practice, progress and ingestion for CA exam preparation.",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# CORS. Vercel preview deployments use generated subdomains, so the allow-list is
# configured explicitly per environment rather than pattern-matched in code.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Attach a request id and emit one structured access log line per request.

    THE ID MUST SURVIVE BOTH THE SUCCESS AND THE FAILURE PATH. Two defects lived
    here:

      * the contextvar was reset BEFORE the access line was written, so every
        access log entry recorded ``request_id: "-"`` - the response header was
        the only place the id appeared;
      * an unhandled exception escaped this middleware entirely (the catch-all
        handler runs in Starlette's ServerErrorMiddleware, OUTSIDE user
        middleware), so a 500 had no access line at all and no id could be
        attached here.

    So the access line is emitted while the contextvar is still bound, and the
    exception branch logs the 500 access line before re-raising. The id itself is
    added to the 500 response by the catch-all handler, which reads
    ``request.state.request_id`` set below.
    """
    import uuid

    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_ctx.set(rid)
    request.state.request_id = rid

    logger = logging.getLogger("app.access")
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Re-raised, never swallowed: the catch-all handler builds the response.
        logger.error(
            "%s %s -> 500 in %sms",
            request.method,
            request.url.path,
            round((time.perf_counter() - started) * 1000, 1),
        )
        raise
    else:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-Id"] = rid

        logger.info(
            "%s %s -> %s in %sms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
    finally:
        request_id_ctx.reset(token)


@app.exception_handler(PermissionDenied)
async def permission_denied_handler(request: Request, exc: PermissionDenied):
    """Turn a missing permission into a 403 in the documented error shape.

    WITHOUT THIS HANDLER A PERMISSION FAILURE IS A 500. ``require_permission`` raises
    so the route body never runs - which is the right design, because a route that
    checks its own permission can forget to - but an unhandled exception falls through
    to the catch-all and the client sees "something went wrong" instead of "your role
    does not include MANAGE_USERS". The first is a bug report; the second is an
    instruction.
    """
    return forbidden(str(exc))


@app.exception_handler(StorageError)
async def storage_error_handler(request: Request, exc: StorageError):
    """A storage outage is a 503 that says so, EVERYWHERE, from one place.

    THE SAME REASONING AS THE PERMISSION HANDLER ABOVE, and the same failure it prevents.
    ``StorageError`` carries a message written for a person - which host answered what,
    with which status - and three routes let it reach the catch-all, so a storage host
    that could not sign a URL produced "an unexpected error occurred" with a 500. The
    operator's next move was to debug the application, when the actual next move was to
    look at storage.

    Registering it HERE rather than adding a ``try/except`` to each of the call sites is
    deliberate:

      * there are seven and a half storage-backed routes and more coming; the eighth
        would be written without the guard, and the symptom would be another 500;
      * the routes that already catch it need different behaviour in some cases (the
        upload-manifest reports per-file failures rather than failing the batch), so
        their local handlers stay and simply win, because a route-level ``except`` runs
        before an app-level handler;
      * 503 is the truthful status: the request was valid, this service is up, and the
        dependency it needs is not answering.

    The detail is the message the storage client wrote, so it names the host and the
    status code instead of hiding them.
    """
    import logging as _logging

    # Logged as a warning, not an exception: an unreachable dependency is an operational
    # event, and a stack trace for it trains people to ignore stack traces.
    _logging.getLogger("app.storage").warning(
        "storage unavailable on %s %s: %s", request.method, request.url.path, exc
    )
    return problem(
        status=503,
        title="Storage unavailable",
        detail=str(exc),
        type_slug="storage",
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Convert Pydantic errors to the documented §7.2 error shape.

    Field paths are flattened to dot notation so the React form layer can map an
    error directly onto the offending input.
    """
    errors = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        errors.append(
            {
                "field": location or "(body)",
                "message": err.get("msg", "Invalid value"),
                # "extra_forbidden" is what fires when a client sends an unknown
                # field - surfaced explicitly so it is obvious the request was
                # rejected rather than silently trimmed.
                "type": err.get("type", "value_error"),
            }
        )
    return problem(
        status=422,
        title="Validation Failed",
        detail="One or more fields were rejected",
        type_slug="validation",
        errors=errors,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all handler: the response must carry the SAME id the logs cite.

    Starlette runs this handler in ServerErrorMiddleware, OUTSIDE the user
    middleware that normally stamps ``X-Request-Id``. Without the id below, the
    body promised "the request id is in the response headers" while the response
    had no such header and the log line recorded ``request_id: "-"`` - so the one
    case where correlation matters most (a 500) was exactly the case where it was
    impossible.

    The id is restored into the contextvar only for the duration of the log call,
    so the structured formatter records the same value that goes on the wire.
    """
    import logging as _logging

    rid = getattr(request.state, "request_id", None)
    error_logger = _logging.getLogger("app.error")
    token = request_id_ctx.set(rid) if rid else None
    try:
        error_logger.exception("Unhandled error: %s", exc, extra={"request_id": rid or "-"})
    finally:
        if token is not None:
            request_id_ctx.reset(token)

    response = problem(
        status=500,
        title="Internal Server Error",
        detail="An unexpected error occurred. The request id is in the response headers.",
        type_slug="internal",
    )
    if rid:
        response.headers["X-Request-Id"] = rid
    return response


# Health is mounted at the root, NOT under /api/v1, so infrastructure probes do
# not depend on the API version.
app.include_router(health.router)
app.include_router(mocks.router, prefix=settings.api_v1_prefix)
app.include_router(planner.router, prefix=settings.api_v1_prefix)
app.include_router(ingestion.router, prefix=settings.api_v1_prefix)
app.include_router(payments.router, prefix=settings.api_v1_prefix)
app.include_router(curriculum.router, prefix=settings.api_v1_prefix)
app.include_router(practice.router, prefix=settings.api_v1_prefix)
app.include_router(progress.router, prefix=settings.api_v1_prefix)
app.include_router(revision.router, prefix=settings.api_v1_prefix)
app.include_router(doubts.router, prefix=settings.api_v1_prefix)
app.include_router(users.router, prefix=settings.api_v1_prefix)
app.include_router(search.router, prefix=settings.api_v1_prefix)
app.include_router(collections.router, prefix=settings.api_v1_prefix)
app.include_router(content.router, prefix=settings.api_v1_prefix)
app.include_router(gamification.router, prefix=settings.api_v1_prefix)
app.include_router(notifications.router, prefix=settings.api_v1_prefix)
app.include_router(admin.router, prefix=settings.api_v1_prefix)
app.include_router(access.router, prefix=settings.api_v1_prefix)
app.include_router(studio.router, prefix=settings.api_v1_prefix)
app.include_router(assistant.router, prefix=settings.api_v1_prefix)
app.include_router(campus.router, prefix=settings.api_v1_prefix)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "ca-prep-api",
        "version": "3.0.0",
        "docs": "/docs",
        "health": "/health",
    }
