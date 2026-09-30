# Security

## Rate limiting

Redis fixed-window counters, applied by `RateLimitMiddleware` (`app/core/rate_limit.py`).

The increment and the expiry are **one operation**, executed by a Lua script
rather than two round-trips:

```lua
local current = redis.call('INCR', key)
if current == 1 then redis.call('EXPIRE', key, window) end
return current
```

As two commands there is a window in which the counter exists with no TTL: a
crash in between leaves a key that never expires and a client permanently
throttled. `register_script` is used rather than `evalsha` so Redis restarting
and losing its script cache is handled (NOSCRIPT) instead of raising.

Every limited response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
`X-RateLimit-Reset`, and those names are listed in
`CORSMiddleware(expose_headers=...)` — without that, `fetch` and XHR hide them
and a client learns it is being throttled only by failing.

Limits are per-route, because cost is not uniform: an AI call costs real money
and a PDF upload costs real disk, so neither shares a bucket with reading a plan
list. `RATE_LIMIT_*_PREFIX` selects the bucket by longest matching prefix; a
prefix that does not match the real route leaves the override **inert** and
silently falls back to the generic limit.

`/health` is deliberately exempt (`RATE_LIMIT_EXEMPT_PATHS`). Render probes it
every few seconds; a probe that consumes a user's quota makes the application
look broken while nothing is wrong.

## X-Forwarded-For

The client IP is read from `X-Forwarded-For` **only** when
`RATE_LIMIT_TRUSTED_PROXIES > 0`, and only from the rightmost *untrusted* entry.
At `0` the header is ignored entirely, which behind a proxy collapses every
anonymous user into one bucket. Set too high, the limiter starts reading a
client-controlled header and the limit can be walked around. On Render this is
exactly `1`.

## Webhooks are fail-closed

`/api/v1/webhooks/*` returns **503** when Redis is unavailable rather than
serving the request. A webhook that cannot be counted can be replayed, and a
replayed `payment.captured` activates a subscription nobody paid for. Refusing
is the correct trade: revenue integrity over webhook convenience.

## Authorization

Server-side RBAC. `require_permission(Permission.X)` is a FastAPI dependency
evaluated against the **PostgreSQL** row on every request. There is no code path
that reads a role from a client-supplied token.

`tests/test_permission_matrix.py` asserts the **mapping** — a STUDENT holds no
administrative permission, and an ADMIN holds every permission except the
owner's. It does not assert that every route under `/api/v1/admin/*` carries the
dependency; that is currently enforced by review, and is worth an automated
check now that the route count has reached 180.

Two systems, two questions: Supabase Auth answers *is this token genuine*;
PostgreSQL answers *what may this account do*. A client cannot widen its own
permissions, because nothing reads the claim for that purpose.

Entitlements are derived from the subscription row **and the clock**, never from
the stored `tier` column. An expired `PREMIUM` row still says `PREMIUM`; the
effective tier is `FREE`. A test asserts exactly that — a lapsed subscriber
loses access while the row still reads `tier=PREMIUM, status=ACTIVE`.

## Signature verification

`hmac.compare_digest` everywhere. A `==` comparison short-circuits on the first
differing byte, leaking the correct signature prefix through timing.

Signatures cover the **raw request body**. Re-serialising parsed JSON changes
key order and whitespace, and the HMAC no longer matches.

## Sentry

`init_sentry()` (`app/core/observability.py`) runs from the application
lifespan and returns `False` without doing anything when no DSN is configured,
so monitoring is never a boot dependency and a staging box without Sentry is not
a failure.

When enabled it sets `environment` from settings, `traces_sample_rate=0.1`, and
`release` from the current git SHA — so a stack trace identifies the commit that
produced it without a deploy-time injection step.

**Credential hygiene: the DSN is never logged in full.** `dsn_host()` reduces it
to its hostname before it reaches the log line, because a DSN is a write-only
credential and a log aggregator is not the place for one. The DSN itself lives
only in the provider, declared `sync: false` and never committed.

**Known gap.** There is currently **no `before_send` scrubber**. Event payloads
are sent as the SDK builds them, so if an exception is raised with request data
in its context, that data reaches Sentry. Filtering request bodies, `Authorization`
headers and cookie values before transmission is not implemented. Until it is,
the practical mitigation is to avoid attaching request context to raised
exceptions.
