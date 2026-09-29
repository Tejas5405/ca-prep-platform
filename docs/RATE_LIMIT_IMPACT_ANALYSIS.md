# Rate-Limit Impact Analysis — P0 #3

**Base:** `1a32897` · **Status:** analysis only, no code written
**Subject:** `config.py:300 rate_limit_per_minute = 100`, which **nothing reads**.

---

## 1. The setting, and every reference to it

```python
# app/core/config.py:300
rate_limit_per_minute: int = 100
```

`grep -rn rate_limit_per_minute app/ tests/` returns **exactly one line — the
definition.** No reader, no writer, no test. It is a dead field, which is worse
than absent: a reader of the config believes the API is rate limited.

## 2. The request pipeline

`app/main.py` builds `FastAPI(...)`, adds `CORSMiddleware`, then one
`@app.middleware("http")` (`request_context_middleware`) that attaches a request
id and writes the access log. There is **no** rate-limiting middleware.

A limiter belongs as a second `@app.middleware("http")` **outside**
`request_context_middleware`, so a rejected request is still logged and still
carries a request id. That ordering is a deliberate choice, not the default.

## 3. The two ad-hoc `429`s — and why they stay separate

| Location | Basis | Verdict |
|---|---|---|
| `assistant.py:142` | `AnalyticsEvent` **row count** per day | **Keep separate** |
| `mocks.py:234` | Redis `INCR`, `mock_attempts_per_hour` | **Keep separate** |

Neither is a general request rate limit. `assistant.py` enforces a *daily quota
on a business action*, counted from the database because it must survive a cache
flush. `mocks.py` enforces an *hourly cap on attempt starts*, already
Redis-backed.

Unifying them would be a regression: the assistant quota is a product rule that
must be durable, and putting it in a 60-second window would make it meaningless.
Both stay. The global limiter is a *transport-level* concern and sits above them.

Note `mocks.py` already establishes the project's precedent for Redis-based
limiting, including a documented fail-open. **P0 #3 deliberately does NOT copy
that fail-open blindly** — see §5.

## 4. Redis is available, and mandatory

`get_redis_client()` (`core/dependencies.py:91`) is a shared `redis.asyncio`
client with a 2s connect timeout, and the suite already runs in **Redis ON and
Redis OFF** modes. No new dependency, no new infrastructure.

**The deciding fact is in `infra/render.yaml:44`:**

```yaml
startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2
```

**Two worker processes.** An in-memory counter would be split across them, so a

## 5. Redis-unavailable behaviour — the explicit decision

This is the question the milestone was told to watch, so it is answered here
rather than defaulted into.

**Decision: fail open, but LOUDLY and only where the trade-off is defensible.**

| Situation | Behaviour | Why |
|---|---|---|
| Redis unreachable, any request | **allow**, log WARNING | A total outage must not lock every visitor out. A DDoS is already causing harm; a login wall is a self-inflicted outage. |
| Redis reachable | **enforce** | The real path. |

**What is explicitly rejected:** treating "Redis down" as "limit off, silently".
That is the exact class of defect this whole audit found — a control that appears
to exist and protects nothing. Instead the failure is **visible**: logged at
WARNING on every request, and reported by the existing `/health/redis` endpoint,
which already exists for this purpose.

The honest framing, recorded in the code: **a per-identity transport limit is
anti-abuse, not an authentication control.** It must not be able to take the
service down. That reasoning is why fail-open-with-a-loud-log is defensible here
and would **not** be for, say, a login-attempt limiter — where losing the check
means losing the control entirely.

## 6. Identity / bucket strategy — derived, not assumed

Four candidates were considered:

| Candidate | Verdict |
|---|---|
| **Authenticated user id** | **Primary.** Unforgeable, already resolved by `get_current_principal`, and one student behind a NAT must not exhaust their classmates' budget. |
| **Client IP** | **Secondary, for unauthenticated requests only.** |
| API key | None exists in this system. |
| Combination | The two above, in that order. |

**On trusting `X-Forwarded-For`:** `services/audit.py:91` already states the
project's position — the header *"is untrusted input - a client can send its own
- so it is recorded for context and NEVER used for authorization."* A rate-limit
bucket is authorization-adjacent: an attacker who spoofs the header gets a fresh
budget per forged address, which is unlimited access.

So the IP path takes **`request.client.host` only** — the socket peer, which
behind Render is the proxy and therefore gives a small number of shared buckets
for anonymous traffic. That is a deliberate, documented weakening: anonymous
callers share a budget. It is acceptable because unauthenticated surface is

## 8. Algorithm

**Fixed-window counter via a single Lua script** — not `INCR` + `EXPIRE`, and not
`INCR` + `EXPIRE` with a `count == 1` guard.

The existing `mocks.py` pattern is non-atomic: if the process dies between
`INCR` and `EXPIRE`, the key has **no TTL and never expires**, permanently
consuming that identity's budget for the life of the Redis instance. A Lua script
makes increment-and-expire one atomic server-side operation, closing that window.
This is also the race-condition requirement (test 5) satisfied properly rather
than incidentally.

Sliding-window log and token-bucket were rejected: materially more expensive per
request, and unnecessary for a transport anti-abuse limit.

## 9. Response shape

`429` with the project's own problem envelope (`problem(...)`, already used by
the other two limiters), plus `Retry-After`, `X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `X-RateLimit-Reset`.

**`X-RateLimit-*` must be added to CORS `expose_headers`** — currently only
`X-Request-Id` is exposed, so a browser could not read them. Easy to miss, and it
would make the headers invisible to the frontend that needs them.

## 10. Test plan against the 9 requirements

| # | Requirement | Approach |
|---|---|---|
| 1 | below limit succeeds | N requests, all 200 |
| 2 | exceeding → 429 | N+1, assert 429 + envelope + headers |
| 3 | window resets | injected clock, or delete the key and retry — **no `sleep`** |
| 4 | identities don't share | user A and user B each get a full budget |
| 5 | no race bypass | concurrent `asyncio.gather` at the boundary; count exact |
| 6 | Redis down | monkeypatched failure → assert **allow + WARNING logged** |
| 7 | excluded endpoints | `/health` and `/docs` unlimited under load |
| 8 | proxy/IP | XFF is **spoofed** and the bucket must NOT change |
| 9 | config changes limit | set `rate_limit_per_minute=2`, assert the 3rd is 429 |

Requirement 8 is the one most likely to be implemented wrongly, so it asserts the
**negative**: a forged header must not mint a new budget.

## 11. Mutation tests

| Mutation | Must fail |
|---|---|
| limiter bypassed (middleware returns `call_next`) | 1, 2, 5 |
| configured limit ignored (hardcoded 100) | 9 |
| `429` returned as 200 | 2 |
| Lua removed → plain `INCR` + `EXPIRE` | 5 (atomicity) |

---

## Recommendation

**ASGI/HTTP middleware, Redis fixed-window via Lua, user-id primary / socket-IP
secondary, the four exclusions in §7, fail-open-with-loud-log on Redis outage,
`X-RateLimit-*` added to CORS `expose_headers`.**

Files that will change: `app/core/rate_limit.py` (new), `app/main.py`
(registration + CORS), `app/core/config.py` (comment only), plus two test files.
No migration. No change to the existing ad-hoc limiters.

small, and trusting a spoofable header would be strictly worse.

**Bucket keys:** `rl:{scope}:{minute}:{identity}` with a 120s TTL — fixed-window
semantics with a minute of overlap so a request at a clock edge is not penalised
twice.

## 7. Excluded paths

| Path | Reason |
|---|---|
| `/health`, `/health/db`, `/health/redis`, `/health/storage` | Render's `healthCheckPath`. Rate limiting it would make a busy API look **down** and trigger replacement. |
| `/docs`, `/openapi.json` | Developer surface; throttling it breaks local work and leaks nothing. |
| `/api/v1/webhooks/razorpay` | Called by Razorpay's servers, not a user. A `429` causes event redelivery storms. Its own protection is signature verification. |
| `OPTIONS` (CORS preflight) | Not a real request; counting it would penalise browsers. |

**Not excluded:** `/api/v1/auth/*`. Sign-in is the classic brute-force target, and
the global IP bucket covers it.

100/min limit would enforce 50/min on each — a limit that *looks* enforced and is
half of what it claims, and which silently changes if the worker count is ever
tuned. Redis is the only correct store. (`num_instances` is unset, so Render runs
one instance, but the two in-process workers are enough to rule out memory.)
