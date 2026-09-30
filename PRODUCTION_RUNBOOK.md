# Production Go-Live Runbook

Operator instructions for deploying the CA Prep Platform to production on Render.
Follow in order. **Five of the steps below exist because their absence fails
silently** — the system keeps answering 200 while doing the wrong thing. Those
are marked ⚠️ and are the ones to slow down for.

---

## 1. System topology

```
                    ┌──────────────────────┐
  Browser  ───────► │  caprep-api   (web)  │ ──►  caprep-postgres  (system of record)
                    │  FastAPI, stateless  │ ──►  caprep-redis     (rate limits, queues)
                    └──────────────────────┘
                    ┌──────────────────────┐
                    │  caprep-worker(worker)│ ──►  same Postgres + Redis
                    │  RQ, Docker runtime  │      (OCR: tesseract + poppler)
                    └──────────────────────┘
```

| Service | Render type | Runtime | Role |
|---|---|---|---|
| `caprep-api` | `web` | native Python | All HTTP traffic |
| `caprep-worker` | `worker` | **docker** | RQ queue drain, OCR |
| `caprep-postgres` | database | Postgres 16 | Source of truth |
| `caprep-redis` | `keyvalue` | Redis-compatible | Rate limits, RQ, webhook replay guard |

**The worker MUST be `runtime: docker`.** Render's native Python runtime has no
`tesseract-ocr` and no `pdftoppm`. `pip install pytesseract pdf2image` succeeds
without them; the failure appears only at runtime, on the first scanned PDF, as
a pipeline that produces zero drafts **and no error on the job**. The worker
looks healthy while a whole class of uploads goes nowhere.

---

## 2. Pre-requisites

| Account | Needed for | Notes |
|---|---|---|
| Render | everything | Blueprint deploy |
| Supabase | auth + storage | **Separate production project.** Never reuse the dev project |
| Sentry | optional error reporting | App boots fine without it |
| Razorpay | payments | **Activated live account**, not just test keys |
| Domain | optional | For a stable webhook URL |

Local pre-flight before you start:

```bash
pytest -x -q                      # expect: 1022 passed, 276 skipped
ruff check app/ tests/            # expect: All checks passed!
./scripts/mypy_ratchet.sh app/   # expect: 35 <= 35 OK
git status --porcelain            # expect: empty
```

---

## 3. Phase A — Infrastructure provisioning

1. Render Dashboard → **New → Blueprint**.
2. Point it at this repository. Render reads `infra/render.yaml`.
   (If you prefer the repo root, copy the file there or set the blueprint path.)
3. Review the resources Render will create:

```
caprep-api       (web)     plan: starter        region: singapore
caprep-worker    (worker)  plan: starter        region: singapore
caprep-postgres  (postgres) plan: basic-256mb   postgres 16
caprep-redis     (keyvalue) plan: starter       maxmemoryPolicy: noeviction
```

4. Click **Apply**. Render will prompt for every `sync: false` variable.
   Leave them blank for now — Phase B fills them.
5. Wait for the first deploy. It will fail or partially fail, because
   `REDIS_URL` is not yet known. **This is expected.** Proceed to Phase B.

> **Note on `preDeployCommand`:** the blueprint sets
> `alembic upgrade head` on the web service, so migrations run automatically
> before a new version takes traffic. Manual fallback is in Phase D.

---

## 4. Phase B — ⚠️ Manual secret configuration

### 4.1 The Redis URL gap (read this twice)

**Render cannot inject a Key Value connection string into an environment
variable.** A `keyvalue` instance exposes `host` and `port` as *separate*
properties and has **no combined `connectionString`** property — unlike Render
Postgres, which does have one. This application takes a **single**
`REDIS_URL` (`app/core/dependencies.py`), so it cannot be assembled from those
two properties.

That is why `render.yaml` declares `REDIS_URL` as `sync: false` on both
services. **It is a manual step, not an oversight.**

```
1. Render → caprep-redis → "Connection" (or the dashboard's Connection tab)
2. Copy the INTERNAL connection URI
3. Paste it into caprep-api   → Environment → REDIS_URL
4. Paste it into caprep-worker → Environment → REDIS_URL
5. Click "Save Changes" and REBUILD both services
```

Format:

```
redis://:<password>@<host>:<port>/0
```

**If you skip this, or leave `localhost:6379`,** every rate-limited path and
every payment webhook returns **503**, because the limiter and the webhook
replay guard are both fail-closed by design. A 503 storm after deploy is this.

### 4.2 The twelve variables

| Variable | Source | Value / Notes |
|---|---|---|
| `DATABASE_URL` | blueprint (`fromDatabase`) | auto-wired to `caprep-postgres` |
| `REDIS_URL` | **manual, see 4.1** | `redis://:<password>@<host>:<port>/0` |
| `ENVIRONMENT` | blueprint | `production` |
| `DEBUG` | blueprint | `false` |
| ⚠️ `RATE_LIMIT_TRUSTED_PROXIES` | blueprint | **`1`** — see 4.3 |
| `RATE_LIMIT_PER_MINUTE` | blueprint | `100` |
| `SENTRY_DSN` | Sentry → Project Settings | `sync: false`, optional |
| `CORS_ORIGINS` | your frontend origin | comma-separated, **no trailing slash** |
| `SUPABASE_URL` | blueprint literal | must equal the frontend's value |
| `SUPABASE_SECRET_KEY` | Supabase → Project Settings → service_role | **server-side only** |

#### ⚠️ Silent Drop hazard — a misnamed variable is not an error

The settings model runs with **`extra="ignore"`** (`app/core/config.py`). An
environment variable it does not declare is **silently discarded**: no boot
error, no log line, no warning.

So `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_KEY` and `SUPABASE_JWT_SECRET` are
all **not** valid names here — they are ignored, and the app boots looking
healthy. Only these two Supabase variables exist:

```
SUPABASE_URL          (already a literal in the blueprint)
SUPABASE_SECRET_KEY
```

**The failure chain if `SUPABASE_SECRET_KEY` is missing or mistyped:**

1. `supabase_secret_key` resolves to `None`
2. `SupabaseAuthAdmin.configured` is `False`, so it returns `False` without
   making any HTTP call
3. the admin route swallows nothing — it reports `claimUpdated: false`
4. the **database row updates to `EDITOR`** while the Supabase JWT still says
   `STUDENT` until the token expires

The promotion appears to succeed and does not. This is the same defect Phase 4
fixed in code; the same failure can be reintroduced by a typo in a dashboard.
**Verify it with the `claimUpdated` check in `PRODUCTION_CHECKLIST.md` §5
before go-live** — it is a 30-second test for a day-one silent authorization
failure.
| `STORAGE_BUCKET` | blueprint | e.g. `question-pdfs` |
| ⚠️ `RAZORPAY_WEBHOOK_SECRET` | Razorpay dashboard | **see Phase C** |

### 4.3 ⚠️ `RATE_LIMIT_TRUSTED_PROXIES=1`

Rate limiting buckets anonymous requests by client IP, read from
`X-Forwarded-For`. Render terminates TLS and forwards the real client address
in that header.

- At **`0`**, the header is ignored, every anonymous user shares Render's proxy
  IP as their bucket, and the first ~100 requests in a minute exhaust the quota
  for **everyone**. A self-inflicted DoS that looks like a traffic spike.
- Set **too high**, the middleware starts reading a client-controlled entry and
  the limiter can be walked around.

**Exactly one proxy hop. `1`.**

---

## 5. Phase C — ⚠️ Razorpay live integration

### 5.1 Why this is the highest-risk step

If `RAZORPAY_WEBHOOK_SECRET` is unset or mismatched, the webhook route
**refuses every request with 401**. Orders are created; captures are never
delivered; subscriptions are never activated.

**Nothing errors. No log line names the cause. The symptom is "payments
don't work" days later.**

### 5.2 Register the webhook

1. Razorpay Dashboard → **Settings → Webhooks → Add New Webhook**.
2. **Webhook URL:**
   `https://<your-production-domain>/api/v1/webhooks/razorpay`
3. **Secret:** click Generate, copy it.
4. **Active events:** select `payment.captured`.
   (Add `payment.failed` too if you want failed attempts recorded.)
5. Save. Note the webhook's active status — Razorpay shows it in the list.

### 5.3 Configure the keys

In Render, on **both** `caprep-api` and `caprep-worker`:

```
RAZORPAY_KEY_ID          <- Razorpay → Account & Settings → API Keys
RAZORPAY_KEY_SECRET      <- same page ("Secret Key")
RAZORPAY_WEBHOOK_SECRET  <- the secret generated in 5.2 step 3
```

`RAZORPAY_WEBHOOK_SECRET` **must be byte-identical** to what Razorpay holds.
Rotate them together.

### 5.4 ⚠️ The staging guard

The app **refuses to boot** if a box with `ENVIRONMENT=staging` holds an
`rzp_live_` key. That is deliberate — a live key cannot be pasted into staging
by accident. On the **production** service, `rzp_live_` keys are correct and
required.

### 5.5 Prove it before declaring success

```bash
# Must return 401/400, NOT 2xx. A 2xx here means a forged signature was
# accepted and is an incident, not a pass.
curl -i -X POST https://<domain>/api/v1/webhooks/razorpay \
  -H 'Content-Type: application/json' \
  -H 'X-Razorpay-Signature: 0000000000000000000000000000000000000000000000000000000000000000' \
  -d '{"entity":"event","event":"payment.captured"}'
```

---

## 6. Phase D — Release verification

The blueprint already runs `alembic upgrade head` as a `preDeployCommand`. To
verify manually:

```bash
# From a shell with DATABASE_URL pointing at production:
alembic current          # expect: 0017_cursor_pagination_indexes (head)
alembic heads            # expect: exactly one head
alembic check            # expect: "No new upgrade operations detected."
```

`alembic check` matters: it is the only thing that detects a column or index
present in the database but missing from the ORM, which the next autogenerate
would silently drop.

**Confirm the worker is not silently failing OCR:**

```bash
docker run --rm <your-api-image> tesseract --version
docker run --rm <your-api-image> pdftoppm -v
```

Both must succeed. If tesseract is missing, ingestion jobs fail at the
scanned-PDF step and every other test still passes.

---

## 7. Phase E — Smoke testing

```bash
./scripts/smoke_test.sh https://<your-production-domain>
```

**Expected: `=== 10 passed, 0 failed ===` and exit code `0`.**

The ten checks: reachability · liveness 200 · JSON body · `X-Request-Id` ·
`X-RateLimit-*` present · `/health` exempt from limiting · forged webhook
signature refused · CORS allow-origin · expose-headers advertises
`X-Request-Id` · advertises `X-RateLimit-Limit`.

Exit codes: **0** all passed · **1** an assertion failed · **2** target
unreachable.

**Read the failures, do not just retry.** The check names the misconfiguration:
`no Access-Control-Allow-Origin` means `CORS_ORIGINS` does not list your
frontend; `X-RateLimit-* headers missing` means the limiter is not mounted or
`RATE_LIMIT_ENABLED=false`; a `503` from the webhook means Redis is unreachable.

---

## 8. Phase F — Sentry verification

### 8.1 Prove telemetry arrives

With no valid session, call any authenticated endpoint with a malformed UUID:

```bash
curl -i https://<domain>/api/v1/admin/users/not-a-uuid
```

A 4xx is handled, not reported. To force a genuine 500, temporarily add a route
that raises, deploy, then **remove it and redeploy** — do not leave it in.

### 8.2 Verify scrubbing (the important half)

In Sentry, open the resulting event and confirm:

- the request's **`Authorization`** header reads **`[REDACTED]`**
- **`X-Razorpay-Signature`** reads **`[REDACTED]`**
- **`Cookie` / `Set-Cookie`** read **`[REDACTED]`**
- `User-Agent` and `Accept` are **still present** (proves it is a filter, not a
  blanket)

If any live credential is visible, the scrubber is not running — check that
`SENTRY_DSN` is set and the image is current.

> The scrubber deliberately **fails open**: if it raises, it sends the event
> unsanitised and logs loudly rather than dropping the event. A log line reading
> `before_send scrubber failed` means credentials may have leaked. Treat it as
> an incident.

---

## 9. Rollback plan

### 9.1 Code rollback (Render)

1. Render → `caprep-api` → **Deploys** → pick the last good deploy → **Rollback**.
   Do the same for `caprep-worker`.
2. Roll back the **worker first**, then the API — a newer API against an older
   worker is the less broken combination.

### 9.2 Schema rollback

Only if the code rollback requires it, and only one revision at a time:

```bash
alembic downgrade -1          # step back one migration
alembic downgrade 0016_applicable_attempts   # or target a revision by id
alembic current               # verify
```

⚠️ **Every migration in this repository is reversible, but a downgrade is
destructive to data written under the newer schema.** Specifically:

- `0014_widen_course_level_set` — downgrading refuses `SET` courses. Rows are
  **not** rewritten to another level; the constraint reappearing rejects them.
- `0015_study_planner` — dropping the tables **deletes all plans and items**.
- `0016_applicable_attempts` — drops the `TEXT[]` column and its data.
- `0017_cursor_pagination_indexes` — drops indexes only. **Safe.**

### 9.3 Take Postgres snapshot first

Render takes daily snapshots, and an untested backup is not a backup. **Verify
a restore works before you need it.**

---

## 10. First-week watch list

| When | Check | Why |
|---|---|---|
| Hour 1 | smoke test exit 0 | catches §4.1 / §5.1 immediately |
| Day 1 | Sentry event count > 0 | proves telemetry, and §8.2 |
| Day 1 | real test-mode order end-to-end | the only proof the webhook works |
| Day 1 | worker logs for OCR errors | silent zero-draft failure |
| Day 3 | Redis eviction metrics | `noeviction` means writes fail at the limit |
| Day 7 | first real (low-value) payment | before high value |

**The single most important check: complete one real payment in test mode and
confirm the subscription activates.** Everything else on this page can look
healthy while that is broken.
