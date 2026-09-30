# Production Go-Live Checklist

Print this. Tick every box. **Do not proceed past a section with an unticked
box** — items marked **⚠️** fail *silently*: the system returns 200 while doing
the wrong thing, and you will not find out until a user complains.

Operator: ____________________  Date: ____________  Domain: ____________________

---

## Section 0 — Pre-flight (local)

- [ ] `pytest -x -q` → **1022 passed, 276 skipped**
- [ ] `ruff check app/ tests/` → **All checks passed!**
- [ ] `ruff format --check app/ tests/` → **202 files already formatted**
- [ ] `./scripts/mypy_ratchet.sh app/` → **35 <= 35 OK**
- [ ] `alembic heads` → exactly one: `0017_cursor_pagination_indexes`
- [ ] `git status --porcelain` → **empty**
- [ ] Working on a branch that is fully pushed

## Section 1 — Accounts

- [ ] Render account, billing method confirmed
- [ ] Supabase **production** project created (NOT the dev project)
- [ ] Supabase Storage bucket created
- [ ] Sentry organisation + project created (optional)
- [ ] Razorpay account **activated for live** (not test-only)
- [ ] Domain pointed at `caprep-api` (or planned)

## Section 2 — Provisioning (Render Blueprint)

- [ ] Blueprint created from `infra/render.yaml`
- [ ] `caprep-api` (web) created
- [ ] `caprep-worker` (worker) created
- [ ] ⚠️ **`caprep-worker` runtime is `docker`** — not native Python.
      A native worker has no tesseract/poppler; scanned PDFs fail silently.
- [ ] `caprep-postgres` created (Postgres 16)
- [ ] `caprep-redis` created (`type: keyvalue`)
- [ ] ⚠️ **`caprep-redis` `maxmemoryPolicy: noeviction`**
      Under `allkeys-lru` an evicted counter reads as "no requests yet" —
      a free reset of every rate limit.
- [ ] `caprep-redis` `ipAllowList` is `[]` (internal only)
- [ ] All four resources in the same **region** (singapore)
- [ ] First deploy attempted (failure at this point is EXPECTED — no REDIS_URL yet)

## Section 3 — ⚠️ Manual overrides (the silent-failure section)

### 3a. Redis URL — CANNOT be automated

- [ ] Render → `caprep-redis` → Connection tab
- [ ] Internal connection URI copied
- [ ] Format confirmed: `redis://:<password>@<host>:<port>/0`
- [ ] Pasted into **`caprep-api`** → `REDIS_URL`
- [ ] Pasted into **`caprep-worker`** → `REDIS_URL`
- [ ] **Not** left as `localhost:6379`
- [ ] Both services **rebuilt** after saving
- [ ] ⚠️ Verified: `/health` returns 200 (a 503 storm means Redis is unreachable)

> Render Key Value exposes `host` and `port` as separate properties and has **no
> combined `connectionString`**, and this app takes a single `REDIS_URL`. Manual
> is not optional.

### 3b. Rate-limit proxy trust

- [ ] ⚠️ `RATE_LIMIT_TRUSTED_PROXIES=1` on `caprep-api` (exactly 1)
      - `0` → every anonymous user shares the proxy IP → self-DoS at ~100 req/min
      - too high → client-controlled header → limiter bypassable
- [ ] `RATE_LIMIT_PER_MINUTE=100`

### 3c. Identity & CORS

- [ ] `SUPABASE_URL` matches the frontend's value **exactly**
      (drift → sign-in works, every API call 401s, looks like an attack)
- [ ] `SUPABASE_SECRET_KEY` set (service_role, server-side only)
- [ ] `CORS_ORIGINS` lists the frontend origin, **no trailing slash**
- [ ] `ENVIRONMENT=production`, `DEBUG=false`
- [ ] `STORAGE_BUCKET` matches the created Supabase bucket

## Section 4 — ⚠️ Razorpay

- [ ] Live API keys obtained (Account & Settings → API Keys)
- [ ] `RAZORPAY_KEY_ID` set on `caprep-api` **and** `caprep-worker`
- [ ] `RAZORPAY_KEY_SECRET` set on `caprep-api` **and** `caprep-worker`
- [ ] Webhook added in Razorpay Dashboard → Settings → Webhooks
- [ ] Webhook URL is exactly `https://<domain>/api/v1/webhooks/razorpay`
- [ ] Active events include `payment.captured`
- [ ] Webhook secret generated and copied
- [ ] ⚠️ `RAZORPAY_WEBHOOK_SECRET` set on both services, **byte-identical**
      to the Razorpay value
- [ ] Forged-signature curl returns **401/400**, not 2xx
      (a 2xx means a forged signature was ACCEPTED — incident, not a pass)
- [ ] Staging guard understood: a `staging` box holding an `rzp_live_` key
      refuses to boot (by design)

> Unset/mismatched webhook secret → every capture silently 401s, orders are
> created, subscriptions never activate, and nothing logs the reason.

## Section 5 — Post-deploy verification

- [ ] Render build log shows no errors
- [ ] `alembic current` → `0017_cursor_pagination_indexes (head)`
- [ ] `alembic check` → `No new upgrade operations detected.`
- [ ] Worker service **active** (not crash-looping)
- [ ] Worker log shows the RQ worker started
- [ ] `tesseract --version` succeeds in the image
- [ ] `pdftoppm -v` succeeds in the image
- [ ] `/health` → 200
- [ ] ⚠️ `./scripts/smoke_test.sh https://<domain>` → **10 passed, 0 failed**, exit **0**
- [ ] ⚠️ **Admin claim sync verification** — `PATCH /api/v1/admin/users/{user_id}`
      with a new role, and confirm the response contains
      **`"claimUpdated": true`**.
      If it is **`false`**, `SUPABASE_SECRET_KEY` is missing or mistyped: the
      database row changes but the Supabase claim does not, silently. The app
      runs `extra="ignore"`, so a wrongly-named variable produces **no error at
      all** — this check is the only way to know. See Runbook §4.2.
- [ ] ⚠️ **One complete test-mode payment**: order created → webhook received →
      subscription **ACTIVE** in the database
      (this is the only check that proves the payment path works; everything
      else can look healthy while it is broken)
- [ ] Upload a **scanned** PDF and confirm drafts are produced
      (proves OCR; a text-layer-only PDF proves nothing)

## Section 6 — Sentry

- [ ] `SENTRY_DSN` set (or consciously skipped — app boots fine without it)
- [ ] One event visible in Sentry
- [ ] ⚠️ Event shows `Authorization` → **`[REDACTED]`**
- [ ] ⚠️ Event shows `X-Razorpay-Signature` → **`[REDACTED]`**
- [ ] ⚠️ Event shows `Cookie` / `Set-Cookie` → **`[REDACTED]`**
- [ ] `User-Agent` / `Accept` still present (proves filter, not blanket)
- [ ] No log line: `before_send scrubber failed` (it would mean a possible leak)
- [ ] No live credential visible in any event payload

## Section 7 — Security review

- [ ] `python3 scripts/check_secrets.py` → `CLEAN`
- [ ] No secret committed to git (all `sync: false` or set in provider)
- [ ] `tests/integration/test_admin_route_security.py` passes
      (no ungated `/api/v1/admin/*` route)
- [ ] Health probe is **exempt** from rate limiting
      (`RATE_LIMIT_EXEMPT_PATHS` contains `/health`)
- [ ] Database restore tested, not just snapshotted

## Section 8 — Go-live sign-off

- [ ] Every box above is ticked
- [ ] Rollback path understood: Render deploy rollback, then
      `alembic downgrade -1` if the schema must follow
- [ ] ⚠️ Confirmed aware that `0015_study_planner` downgrade **deletes all
      plans and plan items**
- [ ] First-week watch list scheduled (see runbook §10)
- [ ] On-call contact assigned
- [ ] **Operator signature**: ____________________  **Date**: __________

---

**Do not go live on a "probably".** The five ⚠️ items — Redis URL, proxy trust,
Razorpay secret, forged-signature check, and the Sentry redaction check — are the
ones where a mistake produces a system that looks perfectly healthy and is not.
