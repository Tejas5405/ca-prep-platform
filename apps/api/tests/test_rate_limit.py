"""Rate limiting: the counter, the key, the bucket table and the middleware.

=============================================================================
WHY THESE RUN AGAINST REAL REDIS AND NOT fakeredis
=============================================================================
``fakeredis`` does not implement ``EVALSHA`` - it answers
``unknown command 'evalsha'`` - and this module's whole reason for existing is
that the counter is one Lua script rather than two round-trips. A fake that
cannot execute the script would test a different program than the one that
ships, and the atomicity claim would be untested precisely because it is the
thing worth testing.

So the counter tests need a live Redis and skip themselves when there is none,
reporting that they skipped rather than passing vacuously. The middleware,
routing and header tests use a stub limiter instead, because what they are
about is the decision, not the counting.
"""

from __future__ import annotations

import asyncio
import socket
import uuid

import pytest
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import Settings
from app.core.rate_limit import (
    RateLimitMiddleware,
    RateLimitResult,
    RedisRateLimiter,
    client_address,
    rate_limit_key,
    resolve_limit,
)
from app.main import app

pytestmark = pytest.mark.rate_limiter

LIVE_REDIS = "redis://127.0.0.1:6379"


def _redis_up(port: int = 6379) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


needs_redis = pytest.mark.skipif(
    not _redis_up(), reason="needs a live Redis; fakeredis cannot run EVALSHA"
)


@pytest.fixture
async def redis_client():
    # max_connections is raised deliberately: the concurrency test opens 1000
    # simultaneous calls, and at the redis-py default pool size it fails with
    # MaxConnectionsError - which says nothing about the script.
    client = aioredis.from_url(
        LIVE_REDIS, encoding="utf-8", decode_responses=True, max_connections=2000
    )
    try:
        yield client
    finally:
        await client.aclose()


def _key() -> str:
    """A key no other test can collide with, since Redis is shared and real."""
    return f"rl:test:{uuid.uuid4().hex}"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "test",
        "rate_limit_enabled": True,
        "rate_limit_window_seconds": 60,
        "rate_limit_per_minute": 3,
        "rate_limit_assistant_per_minute": 2,
        "rate_limit_upload_per_minute": 2,
        "rate_limit_payment_per_minute": 2,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class _StubLimiter:
    """A limiter whose answer the test chooses, for the decision tests."""

    def __init__(self, result: RateLimitResult | None = None, error: Exception | None = None):
        self._result = result or RateLimitResult(
            allowed=True, limit=3, remaining=2, reset_at=1_800_000_000, retry_after=0
        )
        self._error = error
        self.keys: list[str] = []

    async def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
        self.keys.append(key)
        if self._error is not None:
            raise self._error
        return self._result


class _Stubbed(RateLimitMiddleware):
    """The real middleware, with only the counter replaced.

    Subclassing rather than reaching into a built stack: Starlette rebuilds the
    middleware stack when a TestClient starts, so an instance patched before
    startup is discarded and the test silently runs against real Redis - which
    is what the first version of this file did, and why four tests failed while
    looking like product bugs.
    """

    stub: _StubLimiter

    def limiter(self, settings: Settings) -> RedisRateLimiter:
        return self.stub  # type: ignore[return-value]


def _stub_app(limiter: _StubLimiter, **overrides: object) -> FastAPI:
    """A minimal app carrying the real middleware over a stubbed counter.

    Built fresh per test rather than reusing ``app.main.app`` so the paths under
    test are ones that exist in a test app, and so a test cannot be affected by
    an unrelated route someone adds later.
    """
    built = FastAPI()
    built.add_middleware(_Stubbed, settings=_settings(**overrides))
    _Stubbed.stub = limiter

    @built.get("/api/v1/thing")
    async def _thing() -> dict[str, str]:
        return {"ok": "yes"}

    @built.get("/api/v1/assistant/ask")
    async def _ask() -> dict[str, str]:
        return {"ok": "yes"}

    @built.get("/api/v1/ingestion/uploads")
    async def _upload() -> dict[str, str]:
        return {"ok": "yes"}

    @built.post("/api/v1/webhooks/razorpay")
    async def _hook() -> dict[str, str]:
        return {"ok": "yes"}

    @built.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    return built


def _scope(auth: str | None = None, xff: str | None = None, client_host: str = "10.0.0.9") -> dict:
    """A minimal ASGI scope; enough for a Request to answer headers and client."""
    raw: list[tuple[bytes, bytes]] = []
    if auth:
        raw.append((b"authorization", auth.encode()))
    if xff:
        raw.append((b"x-forwarded-for", xff.encode()))
    return {
        "type": "http",
        "headers": raw,
        "client": (client_host, 1234),
        "method": "GET",
        "path": "/api/v1/thing",
        "query_string": b"",
    }


def _request(**kwargs: object) -> Request:
    from starlette.requests import Request as _Request

    return _Request(_scope(**kwargs))  # type: ignore[arg-type]


# ======================================================================
# 1. The counter, against real Redis
# ======================================================================


@needs_redis
async def test_allows_up_to_the_limit_then_refuses(redis_client):
    """The boundary itself: N allowed, N+1 refused."""
    limiter = RedisRateLimiter(redis_client)
    key = _key()
    try:
        for i in range(3):
            result = await limiter.hit(key, limit=3, window=60)
            assert result.allowed is True
            assert result.remaining == 2 - i

        blocked = await limiter.hit(key, limit=3, window=60)
        assert blocked.allowed is False
        assert blocked.remaining == 0
        assert blocked.retry_after > 0
    finally:
        await redis_client.delete(key)


@needs_redis
async def test_the_window_actually_expires(redis_client):
    """The EXPIRE half of the script.

    Without it the key counts up forever and the caller is locked out
    permanently, which is the failure the Lua script exists to prevent.
    """
    limiter = RedisRateLimiter(redis_client)
    key = _key()
    try:
        for _ in range(2):
            await limiter.hit(key, limit=2, window=1)
        assert (await limiter.hit(key, limit=2, window=1)).allowed is False

        await asyncio.sleep(1.3)

        fresh = await limiter.hit(key, limit=2, window=1)
        assert fresh.allowed is True
        assert fresh.remaining == 1
    finally:
        await redis_client.delete(key)


@needs_redis
async def test_the_counter_is_atomic_under_concurrency(redis_client):
    """1000 simultaneous requests must produce a count of exactly 1000.

    Split into INCR-then-EXPIRE, two clients can interleave so a key is
    incremented without ever being given a TTL, or a window closes early.
    Asserted against the stored value rather than the returned one, because the
    returned value is what would hide the race.
    """
    limiter = RedisRateLimiter(redis_client)
    key = _key()
    try:
        await asyncio.gather(*(limiter.hit(key, limit=10_000, window=60) for _ in range(1000)))
        assert int(await redis_client.get(key)) == 1000
    finally:
        await redis_client.delete(key)


@needs_redis

# ======================================================================
# 2. The key - the part that decides whether this protects anything
# ======================================================================


class TestTheKey:
    def test_a_bearer_token_gets_its_own_bucket(self):
        settings = _settings()
        one = rate_limit_key(_request(auth="Bearer token-a"), settings)
        two = rate_limit_key(_request(auth="Bearer token-b"), settings)
        assert one != two
        assert one.startswith("rl:tok:")

    def test_the_same_token_gets_the_same_bucket(self):
        settings = _settings()
        one = rate_limit_key(_request(auth="Bearer same"), settings)
        two = rate_limit_key(_request(auth="Bearer same"), settings)
        assert one == two

    def test_the_token_is_never_plaintext_in_the_key(self):
        """The key reaches Redis, where it is logged by whoever debugs."""
        key = rate_limit_key(_request(auth="Bearer super-secret-value"), _settings())
        assert "super-secret-value" not in key

    def test_no_token_falls_back_to_the_address(self):
        assert rate_limit_key(_request(), _settings()) == "rl:ip:10.0.0.9"

    def test_a_forged_leftmost_forwarded_for_does_not_buy_a_new_bucket(self):
        """The bypass the first draft of this module had.

        `split(',')[0]` is the value the CLIENT wrote, so rotating it defeats the
        limiter entirely. With one trusted proxy the address that counts is the
        one our own infrastructure appended.
        """
        settings = _settings(rate_limit_trusted_proxies=1)
        first = rate_limit_key(_request(xff="1.1.1.1, 203.0.113.9"), settings)
        second = rate_limit_key(_request(xff="2.2.2.2, 203.0.113.9"), settings)
        assert first == second == "rl:ip:203.0.113.9"

    def test_a_different_client_behind_the_proxy_is_a_different_bucket(self):
        settings = _settings(rate_limit_trusted_proxies=1)
        one = rate_limit_key(_request(xff="203.0.113.9"), settings)
        two = rate_limit_key(_request(xff="203.0.113.10"), settings)
        assert one != two

    def test_the_header_is_ignored_when_no_proxy_is_trusted(self):
        """At 0 hops a forwarded header is just a claim by the caller."""
        settings = _settings(rate_limit_trusted_proxies=0)
        assert rate_limit_key(_request(xff="9.9.9.9"), settings) == "rl:ip:10.0.0.9"

    def test_a_missing_header_falls_back_to_the_socket_address(self):
        """Not a crash, and not a widened bucket."""
        assert client_address(_request(), 1) == "10.0.0.9"


# ======================================================================
# 3. The bucket table
# ======================================================================


class TestTheBucketTable:
    def test_the_default_applies_to_ordinary_api_traffic(self):
        assert resolve_limit("/api/v1/thing", _settings()) == 3

    def test_the_expensive_routes_get_their_own_tighter_budget(self):
        settings = _settings()
        assert resolve_limit("/api/v1/assistant/ask", settings) == 2
        assert resolve_limit("/api/v1/ingestion/uploads", settings) == 2
        assert resolve_limit("/api/v1/payments/order", settings) == 2

    def test_infrastructure_and_public_pages_are_not_metered(self):
        settings = _settings()
        for path in ("/health", "/health/db", "/api/v1/payments/plans", "/"):
            assert resolve_limit(path, settings) is None, path

    def test_exempt_prefixes_cover_their_subtree(self):
        """`/health` exempting only itself would leave /health/db throttled."""
        assert resolve_limit("/health/redis/storage", _settings()) is None

    def test_a_similarly_named_path_is_not_exempt_by_accident(self):
        """Exact match, not a bare `startswith` on the string.

        Exempting `/health` must not exempt `/healthcheck` or, worse, a real
        route whose name merely begins the same way.
        """
        settings = _settings()
        assert resolve_limit("/api/v1/things", settings) == 3
        assert resolve_limit("/api/v1/paymentsx", settings) == 3


# ======================================================================
# 4. The middleware: headers, 429 shape, fail-open, fail-closed
# ======================================================================


def test_a_served_request_advertises_its_budget():
    """Not only on refusal.

    A client that can learn its budget only by being refused cannot back off
    before the refusal, so the three headers ride on every metered response.
    """
    with TestClient(_stub_app(_StubLimiter())) as client:
        response = client.get("/api/v1/thing")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "3"
    assert response.headers["X-RateLimit-Remaining"] == "2"
    assert int(response.headers["X-RateLimit-Reset"]) > 0


def test_a_refusal_is_a_429_with_a_retry_after_and_a_problem_body():
    """RFC 7807 shape, the same envelope every other error in this API uses."""
    refused = RateLimitResult(
        allowed=False, limit=3, remaining=0, reset_at=1_800_000_000, retry_after=42
    )
    with TestClient(_stub_app(_StubLimiter(refused))) as client:
        response = client.get("/api/v1/thing")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    body = response.json()
    assert body["status"] == 429
    assert body["title"] == "Rate limit exceeded"
    assert body["retry_after"] == 42
    # The media type is part of the contract, not a detail.
    assert response.headers["content-type"].startswith("application/problem+json")


def test_an_exempt_route_is_never_counted_and_never_refused():
    """Uptime monitors poll /health; a throttle there is an outage."""
    limiter = _StubLimiter(
        RateLimitResult(allowed=False, limit=1, remaining=0, reset_at=1_800_000_000, retry_after=9)
    )
    with TestClient(_stub_app(limiter)) as client:
        for _ in range(50):
            response = client.get("/health")
            assert response.status_code == 200
            assert "X-RateLimit-Limit" not in response.headers
    assert limiter.keys == [], "an exempt route must not reach the counter at all"


def test_redis_down_serves_the_request_unmetered():
    """Fail open. A limiter that takes the site down with its dependency has
    moved the outage rather than prevented it."""
    with TestClient(_stub_app(_StubLimiter(error=RedisError("refused")))) as client:
        response = client.get("/api/v1/thing")
    assert response.status_code == 200
    # No budget is advertised, because none is being enforced.
    assert "X-RateLimit-Limit" not in response.headers


def test_redis_down_refuses_the_money_webhook():
    """The one fail-closed route.

    It is the only public route that mutates money, and a 503 is an answer
    Razorpay retries, with the idempotency key making that retry safe.
    """
    with TestClient(_stub_app(_StubLimiter(error=RedisError("refused")))) as client:
        response = client.post("/api/v1/webhooks/razorpay")
    assert response.status_code == 503
    assert response.json()["title"] == "Rate limiting unavailable"
    assert "Retry-After" in response.headers


def test_redis_down_does_not_refuse_an_ordinary_route():
    """The two behaviours must not bleed into each other.

    Fail-closed on /api/v1/thing would take down every logged-in student
    because the cache is unreachable.
    """
    with TestClient(_stub_app(_StubLimiter(error=RedisError("refused")))) as client:
        assert client.post("/api/v1/webhooks/razorpay").status_code == 503
        assert client.get("/api/v1/thing").status_code == 200


def test_the_switch_off_serves_everything():
    """`rate_limit_enabled: false` is a real, tested state, not a dead field."""
    with TestClient(
        _stub_app(_StubLimiter(error=RedisError("refused")), rate_limit_enabled=False)
    ) as client:
        assert client.get("/api/v1/thing").status_code == 200
        assert client.post("/api/v1/webhooks/razorpay").status_code == 200


def test_the_bucket_table_decides_which_limit_is_sent():
    """The table, not a constant, must reach the counter."""
    seen: list[tuple[str, int]] = []

    class _Recorder(_StubLimiter):
        async def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
            seen.append((key, limit))
            return await super().hit(key, limit, window)

    with TestClient(_stub_app(_Recorder())) as client:
        client.get("/api/v1/thing")  # default bucket: 3
        client.get("/api/v1/assistant/ask")  # assistant bucket: 2
        client.get("/api/v1/ingestion/uploads")  # upload bucket: 2

    limits = [limit for _, limit in seen]
    assert limits == [3, 2, 2]


# ======================================================================
# 5. The wiring - the parts a grep would not catch
# ======================================================================


def test_middleware_sits_inside_cors():
    """The order the comment in app/main.py claims, asserted.

    `add_middleware` prepends, so reading the file top-to-bottom tells you
    nothing about the running order. Getting this wrong means every 429 reaches
    the browser as an opaque network error with no readable X-RateLimit-* and
    no X-Request-Id - invisible in review, obvious in production.
    """
    order = [m.cls.__name__ for m in app.user_middleware]
    assert "RateLimitMiddleware" in order and "CORSMiddleware" in order
    assert order.index("RateLimitMiddleware") > order.index("CORSMiddleware"), (
        f"rate limiting must be INSIDE CORS so a 429 gets CORS headers: {order}"
    )
    assert order[0] == "BaseHTTPMiddleware", (
        f"the request-id middleware must stay outermost so a 429 has an id: {order}"
    )


def test_cors_exposes_the_rate_limit_headers():
    """Sent-but-not-exposed is the same as not sent.

    A browser only surfaces response headers named in
    Access-Control-Expose-Headers, so a limiter whose headers the client cannot
    read is a limiter the client can only discover by failing.

    A probe app is used rather than ``app.main`` because that app's
    ``cors_origins`` is empty in the test environment, and Starlette correctly
    omits every CORS header for a non-allow-listed origin - which would make
    this assertion pass for the wrong reason. The shipped configuration is
    asserted separately, below.
    """
    probe = FastAPI()
    probe.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization"],
        expose_headers=[
            "X-Request-Id",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )

    @probe.get("/api/v1/thing")
    async def _thing() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(probe) as client:
        # The ACTUAL response, not the preflight. Starlette attaches
        # Access-Control-Expose-Headers to the real response and leaves the
        # preflight carrying only Allow-Origin/Methods/Headers/Max-Age - so an
        # OPTIONS assertion, which is what the original specification asked
        # for, would have failed for a reason that has nothing to do with the
        # limiter. This is the header a browser's fetch() actually reads.
        response = client.get("/api/v1/thing", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    exposed = response.headers.get("access-control-expose-headers", "")
    for header in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"):
        assert header in exposed, f"{header} sent but invisible to the browser"


def test_a_preflight_does_not_consume_the_callers_budget():
    """OPTIONS must not be counted as a metered request.

    A browser sends a preflight before the real request on every non-simple
    cross-origin call. If the limiter counted it, a client would spend two
    units of budget per operation and halve its effective limit without anyone
    noticing. The 405 is FastAPI saying the route has no OPTIONS handler; the
    point is that answering it never reaches the counter, which is what the
    empty ``keys`` asserts.
    """
    limiter = _StubLimiter()
    with TestClient(_stub_app(limiter)) as client:
        client.options(
            "/api/v1/thing",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert limiter.keys == [], "a preflight must not reach the counter"


def test_the_real_app_names_the_rate_limit_headers_in_its_cors_config():
    """The shipped configuration, not a probe.

    Reading ``expose_headers`` off the live CORSMiddleware instance is the only
    way to catch someone reconfiguring the app and losing the three names while
    every behavioural test above still passes against a hand-built app.
    """
    expose = next(
        m.kwargs["expose_headers"] for m in app.user_middleware if m.cls is CORSMiddleware
    )
    for header in (
        "X-Request-Id",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
    ):
        assert header in expose, f"{header} missing from the shipped CORS config"


def test_a_real_429_carries_both_the_id_and_the_cors_headers():
    """The end-to-end version of the two ordering claims at once.

    Rate limiting is stubbed to refuse, so this is the real production stack -
    real middleware, real CORS, real request-id - with only the counter faked.
    """
    refused = RateLimitResult(
        allowed=False, limit=3, remaining=0, reset_at=1_800_000_000, retry_after=7
    )
    original = RateLimitMiddleware.limiter
    RateLimitMiddleware.limiter = lambda self, settings: _StubLimiter(refused)  # type: ignore[method-assign]
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/me", headers={"Origin": "http://localhost:5173"})
    finally:
        RateLimitMiddleware.limiter = original  # type: ignore[method-assign]

    assert response.status_code == 429
    assert response.headers["X-Request-Id"]
    assert response.headers["Retry-After"] == "7"
    assert "X-RateLimit-Limit" in response.headers.get("access-control-expose-headers", "")


def test_one_users_traffic_does_not_spend_anothers_budget():
    """The isolation property, end to end through the middleware.

    ``TestTheKey`` covers the key function directly; this one drives two
    different bearer tokens through the real middleware and asserts the second
    is unaffected by the first exhausting its budget. That is the property a
    student actually experiences, and it is what fails if the key ever collapses
    to something shared.
    """
    seen: list[str] = []

    class _PerToken(_StubLimiter):
        async def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
            seen.append(key)
            # First token is over budget; every other key is untouched.
            over = len([k for k in seen if k == key]) > 1
            return RateLimitResult(
                allowed=not over,
                limit=3,
                remaining=0 if over else 2,
                reset_at=1_800_000_000,
                retry_after=30 if over else 0,
            )

    with TestClient(_stub_app(_PerToken())) as client:
        first = {"Authorization": "Bearer alice-token"}
        second = {"Authorization": "Bearer bob-token"}

        assert client.get("/api/v1/thing", headers=first).status_code == 200
        assert client.get("/api/v1/thing", headers=first).status_code == 429

        bob = client.get("/api/v1/thing", headers=second)
        assert bob.status_code == 200, "alice's exhausted budget must not refuse bob"

    assert len(set(seen)) == 2, f"two callers shared one bucket: {seen}"


def test_the_dead_config_field_is_now_live():
    """`rate_limit_per_minute` sat unused for the life of the project.

    Asserted through the table rather than by reading the model, so this fails
    if the field is renamed, dropped, or bypassed.
    """
    assert resolve_limit("/api/v1/anything", _settings(rate_limit_per_minute=37)) == 37

    def test_every_limit_is_configurable(self):
        settings = _settings(rate_limit_per_minute=11, rate_limit_assistant_per_minute=7)
        assert resolve_limit("/api/v1/thing", settings) == 11
        assert resolve_limit("/api/v1/assistant/ask", settings) == 7


async def test_two_limiters_sharing_a_key_see_one_counter(redis_client):
    """A key is a bucket, not a client session.

    The middleware builds its limiter once per process, but a deploy can run
    several. Two instances on one key must not each allow the full limit.
    """
    key = _key()
    try:
        first = RedisRateLimiter(redis_client)
        second = RedisRateLimiter(redis_client)
        for _ in range(2):
            await first.hit(key, limit=2, window=60)
        assert (await second.hit(key, limit=2, window=60)).allowed is False
    finally:
        await redis_client.delete(key)
