"""Redis-backed fixed-window rate limiting.

=============================================================================
WHY A MIDDLEWARE, AND WHY THE BUCKETS LOOK LIKE THIS
=============================================================================
The tempting shape is a ``@rate_limit(...)`` decorator on each route. It is
rejected here for the reason the rest of this codebase rejects per-route
permission strings: a limit that lives on a route is a limit someone forgets.
A new endpoint with no decorator is unmetered, silently, and nothing fails. One
middleware with a single table means an unmetered route is a missing table row,
which is visible in one file.

=============================================================================
THE KEY, AND THE PROBLEM THAT FORCED IT
=============================================================================
``request.client.host`` is the right answer locally and the WRONG answer in
production. Behind Render's proxy every socket has the proxy's address, so
keying on it puts all anonymous traffic in one shared bucket: at 100/min that
is a self-inflicted outage, triggered by one scraper, and it looks like the
limiter is working because requests really are being refused.

So the key is:

  * a bearer token is present -> ``rl:tok:<sha256(token)[:32]>``
  * otherwise                -> ``rl:ip:<resolved address>``

Token-hash, not ``user.id``, and the reason is cost. ``user.id`` is not
available here: middleware runs BEFORE dependency resolution, so obtaining it
would mean verifying the JWT signature AND hitting the database on every single
request, duplicating work ``get_current_user`` is about to do anyway. The token
hash is a stable stand-in for the session and costs one SHA-256.

Two honest limits of that choice, recorded rather than hidden:

  1. A user signed in on two devices holds two buckets. Making the limit
     per-USER rather than per-session needs the user row, so it belongs in the

=============================================================================
X-FORWARDED-FOR IS NOT TRUSTED
=============================================================================
``X-Forwarded-For`` is a list, and its LEFTMOST entry is written by the client.
Reading ``split(",")[0]`` is what the first draft of this module proposed, and
it is a bypass: a caller sending a random leftmost value on every request gets
a fresh bucket every request and is never limited at all.

The usable value is the one appended by our own infrastructure, so the hop count
has to be configured (``rate_limit_trusted_proxies``) rather than guessed. With
one trusted proxy the client address is the RIGHTMOST entry. With no proxy
(``0``, which is what local development wants) the header is ignored entirely
and ``request.client.host`` is used, because on a direct connection a forged
header is just a forged header.

=============================================================================
FAIL-OPEN, AND THE ONE PLACE IT IS NOT
=============================================================================
Availability wins by default: if Redis is down, requests are served. A limiter
that takes the site down when its own dependency fails has moved the outage,
not prevented it.

The exception is the unauthenticated webhook. It fails CLOSED, because it is the
one public route that mutates money, and a 503 is the correct answer there:
Razorpay retries a non-2xx webhook, and the idempotency key in
``app.api.v1.payments`` is what makes that retry safe. Refusing it while the
limiter is blind is strictly better than accepting unbounded payment events.

There is no ``/api/v1/auth/*`` branch here, and that is deliberate. No such
route exists: sign-in and sign-up are Supabase calls made from the browser, so
this API never sees a password. A fail-closed branch keyed on a prefix matching
nothing would be dead code that reads like a control. The mechanism is the
configurable ``rate_limit_fail_closed_paths`` list instead, so the day a route is
added the fix is one config line.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import status
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings
from app.core.envelope import problem

logger = logging.getLogger(__name__)

#: INCR and EXPIRE must be one operation. As two round-trips there is a window
#: where a crash between them leaves a key with no TTL, and that key counts up
#: forever - the caller is permanently locked out at a number that only goes up,
#: with no ``Retry-After`` that means anything.
#:
#: The TTL is also returned so the caller can compute a truthful ``Reset``; a
#: window that advertises a reset time it does not honour is worse than none.
_LUA_FIXED_WINDOW = """
local key = KEYS[1]
local window = tonumber(ARGV[2])

local current = redis.call('INCR', key)

if current == 1 then
    redis.call('EXPIRE', key, window)
end

local ttl = redis.call('TTL', key)

return {current, ttl}
"""


@dataclass(frozen=True)
class RateLimitResult:
    """The outcome of one window for one key."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after: int


class RedisRateLimiter:
    """A fixed-window counter held in Redis.

    Deliberately not a sliding window: that needs either sorted sets or a second
    timestamp field, and its benefit (smoothing the boundary) does not pay for
    the complexity at these limits. The cost of a fixed window is that up to 2x
    the limit can pass across a boundary, a fair trade against a counter that
    can silently stop expiring.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        # register_script handles NOSCRIPT: if Redis restarts and flushes its
        # script cache, redis-py re-uploads on the next call. The manual
        # script_load/evalsha in the original draft would raise NoScriptError on
        # the first request after every restart - a self-inflicted outage from
        # the component meant to prevent outages.
        self._script = redis.register_script(_LUA_FIXED_WINDOW)

    async def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
        """Count this request and report whether it is over the limit."""
        now = int(time.time())
        current, ttl = await self._script(keys=[key], args=[limit, window])
        current = int(current)
        # TTL is -1 for a key with no expiry and -2 for a key that vanished.
        # Both mean "this window is not actually bounded", which must not be
        # reported to a client as "reset in -2 seconds".
        ttl = int(ttl) if int(ttl) > 0 else window
        return RateLimitResult(
            allowed=current <= limit,
            limit=limit,
            remaining=max(0, limit - current),
            reset_at=now + ttl,
            retry_after=ttl if current > limit else 0,
        )


def client_address(request: Request, trusted_proxies: int) -> str:
    """The address to bucket anonymous traffic by.

    ``trusted_proxies`` is how many proxies in front of this app we operate. At
    0 the header is not read at all, because there is nothing vouching for it.
    """
    host = request.client.host if request.client else "unknown"
    if trusted_proxies < 1:
        return host

    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if not hops:
        # A trusted proxy that omits the header is a misconfiguration, and
        # falling back to the socket address collapses every anonymous caller
        # into one bucket - the exact failure this function exists to prevent.
        # Logged rather than silently absorbed.
        logger.warning(
            "rate limit: trusted_proxies=%d but no X-Forwarded-For on %s; all "
            "anonymous traffic shares one bucket",
            trusted_proxies,
            request.url.path,
        )
        return host
    return hops[-1]


def rate_limit_key(request: Request, settings: Settings) -> str:
    """Bucket key: the session when there is one, the address when there is not."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            # Hashed, and never logged: the bearer token is a credential.
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
            return f"rl:tok:{digest}"
    return f"rl:ip:{client_address(request, settings.rate_limit_trusted_proxies)}"


def _under(path: str, prefix: str) -> bool:
    """True when ``path`` is ``prefix`` itself or something beneath it.

    A bare ``startswith`` is wrong here and was the second bug these tests
    caught: ``/api/v1/paymentsx`` starts with ``/api/v1/payments``, so a route
    one character away from the payments group was silently given the payment
    bucket instead of the default.
    """
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def resolve_limit(path: str, settings: Settings) -> int | None:
    """The limit for this path, or ``None`` when the path is not metered.

    Ordered most-specific first. Every entry names a route that exists: the
    ``auth`` buckets in the original specification were for paths this API does
    not have, and are left out rather than shipped as rules that can never match.
    """
    for exempt in settings.rate_limit_exempt_paths:
        # ``/`` must be matched exactly. Prefix-matching on "/" would match every
        # path in the application and silently exempt the whole API, which is
        # what the first version of this function did - the third bug these
        # tests caught.
        if exempt == "/":
            if path == "/":
                return None
            continue
        if _under(path, exempt):
            return None
    if path == settings.rate_limit_upload_path:
        return settings.rate_limit_upload_per_minute
    if _under(path, settings.rate_limit_assistant_prefix):
        return settings.rate_limit_assistant_per_minute
    if _under(path, settings.rate_limit_payment_prefix):
        return settings.rate_limit_payment_per_minute
    if _under(path, settings.api_v1_prefix):
        return settings.rate_limit_per_minute
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Count every metered request and refuse the ones over the limit.

    Registered INSIDE the request-id middleware and OUTSIDE the routers, which
    is the opposite of the order first proposed for this module and is
    deliberate on both sides:

    * Outside CORSMiddleware would be wrong - CORS is what attaches
      ``Access-Control-Expose-Headers``, so a 429 built outside it would carry
      no CORS headers and the browser would report an opaque network error
      instead of a rate limit.
    * Outside the request-id middleware would strip ``X-Request-Id`` from every
      429, breaking the rule that every response carries one.

    Because ``add_middleware`` PREPENDS, "added first in the file" means
    "innermost". ``app.main`` therefore adds this above the CORS call, which is
    the opposite of the order it reads in. ``test_middleware_sits_inside_cors``
    asserts the resulting stack rather than trusting this comment.
    """

    def __init__(self, app: Any, settings: Settings | None = None) -> None:
        super().__init__(app)
        self._settings = settings
        # Not cached on the instance, and that is deliberate rather than an
        # oversight.
        #
        # `app.core.dependencies` keeps a module-global Redis client, and the
        # conftest resets it around every test. Caching a client here as well
        # would outlive that reset: the integration suite drives the app through
        # `run_in_database`, which runs each body on a FRESH event loop, so a
        # client bound to an earlier loop fails on the next one with
        # "got Future attached to a different loop" - 229 unrelated tests
        # failing with a Redis error, which is what the first version of this
        # middleware did.
        #
        # redis-py's own pool is what makes building per request cheap; the
        # expensive part (the connection) is still reused.
        self._limiter: RedisRateLimiter | None = None

    def settings(self) -> Settings:
        if self._settings is not None:
            return self._settings
        from app.core.config import get_settings

        return get_settings()

    def limiter(self, settings: Settings) -> RedisRateLimiter:
        from app.core.dependencies import get_redis_client

        return RedisRateLimiter(get_redis_client(settings))

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = self.settings()
        if not settings.rate_limit_enabled or request.method == "OPTIONS":
            # OPTIONS is skipped explicitly rather than relying on CORSMiddleware
            # being layered outside this one. A browser sends a preflight before
            # the real request on every non-simple cross-origin call, so counting
            # it would silently halve a caller's effective budget. Today the CORS
            # layer happens to answer preflights first, but a limiter whose
            # correctness depends on another middleware's position is one config
            # change away from charging everyone double.
            return await call_next(request)

        limit = resolve_limit(request.url.path, settings)
        if limit is None:
            return await call_next(request)

        try:
            result = await self.limiter(settings).hit(
                rate_limit_key(request, settings),
                limit,
                settings.rate_limit_window_seconds,
            )
        except RedisError as exc:
            if self._fails_closed(request, settings):
                return self._closed(request, settings)
            # Fail open. No headers: there is no honest budget to report, and
            # advertising one we are not enforcing is the same class of lie as
            # the `return 0` in the points ledger this module sits beside.
            logger.error(
                "rate limit unavailable (%s); serving %s unmetered",
                type(exc).__name__,
                request.url.path,
            )
            return await call_next(request)

        if not result.allowed:
            return self._too_many(request, result, settings)

        response = await call_next(request)
        self._stamp(response, result)
        return response

    def _fails_closed(self, request: Request, settings: Settings) -> bool:
        path = request.url.path
        return any(
            path == closed or path.startswith(closed.rstrip("/") + "/")
            for closed in settings.rate_limit_fail_closed_paths
        )

    def _stamp(self, response: Response, result: RateLimitResult) -> None:
        """Advertise the budget on success, not only on refusal.

        A client that can only learn its budget by being refused cannot back off
        before the refusal. These are also why the CORS expose list has to name
        them - otherwise the browser hides all three.
        """
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = str(result.reset_at)

    def _too_many(self, request: Request, result: RateLimitResult, settings: Settings) -> Response:
        logger.warning(
            "rate limit exceeded: %s %s limit=%d retry_after=%d request_id=%s",
            request.method,
            request.url.path,
            result.limit,
            result.retry_after,
            getattr(request.state, "request_id", "-"),
        )
        response = problem(
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            title="Rate limit exceeded",
            detail=(
                f"Too many requests. This endpoint allows {result.limit} requests "
                f"per {settings.rate_limit_window_seconds}s; retry in "
                f"{result.retry_after}s."
            ),
            type_slug="rate-limit",
            extra={"retry_after": result.retry_after},
        )
        response.headers["Retry-After"] = str(result.retry_after)
        self._stamp(response, result)
        return response

    def _closed(self, request: Request, settings: Settings) -> Response:
        logger.error(
            "rate limit unavailable; refusing %s (fail-closed route)",
            request.url.path,
        )
        response = problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Rate limiting unavailable",
            detail=(
                "This endpoint is temporarily disabled because the rate limiter "
                "cannot be reached. Retry shortly."
            ),
            type_slug="rate-limit",
        )
        response.headers["Retry-After"] = str(settings.rate_limit_window_seconds)
        return response
