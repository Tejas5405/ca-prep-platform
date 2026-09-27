# Staging Deployment Plan

**Milestone:** repository ownership + staging readiness · **Baseline commit:** `49adb42`
**Status: NOT EXECUTED.** This is the sequence to follow once the owner has
supplied the external infrastructure. Steps 1–2 are blocked today; see
`docs/OWNERSHIP_AND_CI_REPORT.md`.

Every step names its **verification** and its **rollback**, because a deployment
plan without a rollback is a wish list. Nothing here may be run until the
preceding step's verification has passed.

---

## Preconditions — all must hold before step 1

| # | Precondition | Status |
|---|---|---|
| P1 | A real Git remote is configured by the owner | ❌ **BLOCKED** — no remote exists |
| P2 | Remote CI has completed green on the pushed commit | ❌ **BLOCKED** — depends on P1 |
| P3 | A staging Supabase project exists with its own Auth users and buckets | ❌ owner action (DG-3) |
| P4 | A staging Postgres instance exists | ❌ owner action (DG-2) |
| P5 | A staging Redis instance exists | ❌ owner action (DG-4) |
| P6 | Razorpay **TEST** keys obtained | ❌ owner action |
| P7 | All local gates green on the exact commit to be deployed | ✅ verified at `49adb42` |
| P8 | Secret values stored in the provider's dashboard, never in git | to do at step 12 |

---

## The sequence

### 1. Remote repository

**Do:** the owner configures the remote and grants access.

```bash
git remote -v          # owner supplies the URL; the agent does not invent it
git push -u origin main
```

**Verify:** `git branch -vv` shows `main` tracking `origin/main`.

**Rollback:** none needed — a push is additive and history is not rewritten.

**Blocked:** no remote is configured. `REMOTE_REQUIRED` is reported in
`docs/OWNERSHIP_AND_CI_REPORT.md`.

### 2. CI green

**Do:** let the `CI` workflow run on the pushed commit. **Do not change anything
while waiting.**

**Verify — the four jobs, all required:**

| Job | What it proves |
|---|---|
| `security` | `check_secrets.py --worktree` finds no credentials |
| `api` | ruff check, ruff format, `alembic upgrade head`, `alembic check`, full pytest against PostgreSQL 16 + Redis 7 |
| `migrations-are-reversible` | `upgrade head` → `downgrade base` → `upgrade head` all succeed |
| `web` | typecheck, eslint, vitest, production build |

**If it fails:** report the failure output first. Do not weaken a local gate to
make CI pass, and do not re-run hoping for a flake before reading the log.

**Rollback:** n/a — nothing is deployed yet.

### 3. Backend staging environment

**Do:** create a Render **staging** environment (DG-8). `infra/render.yaml` today
sets `ENVIRONMENT: production` literally on both services, so a staging deploy
would misreport itself as production and change CORS and boot strictness. The
staging environment needs its own env vars with `ENVIRONMENT=staging`.

**Verify:** the service boots and `/health` returns 200.

**Rollback:** delete the environment. Nothing else depends on it yet.

### 4. Database

**Do:** provision staging Postgres (DG-2), create database `caprep_staging`. Per
`docs/ENVIRONMENT_ISOLATION.md` this is a **dedicated instance**, never a local or
production server.

**Verify:** `psql` connects; the database is empty.

**Rollback:** drop the instance.

### 5. Migrations

**Do:** `alembic upgrade head` against `caprep_staging`, as the deploy pipeline's
`preDeployCommand` — **not** from the application startup path, which would race
across instances.

**Verify:**

```bash
alembic current     # → 6c3f7b0a0c13 (head)
alembic check       # → No new upgrade operations detected.
```

**Rollback:** per `docs/STAGING_ARCHITECTURE.md` §4.1 — roll back the
*application*, never the schema. A migration that is itself wrong is corrected by
a new forward migration. `alembic downgrade` is **not** run against staging as an
experiment.

### 6. Seed

**Do:** `python -m app.seed` against `caprep_staging`. Idempotent and
deterministic.

**Verify:** 3 courses, 16 subjects, 35 chapters, 27 questions, 3 mocks, 3 exam
sessions; `--check` reports all reference content present.

**Rollback:** the database is disposable — rebuild it.

### 7. Redis

**Do:** provision staging Redis (DG-4). `infra/render.yaml` injects `REDIS_URL`

### 8. Worker

**Do:** deploy `caprep-worker-staging` from `infra/render.yaml` — `type: worker`,
**Docker** runtime, `startCommand: python -m app.workers.rq_worker`.

**Verify:** the worker logs *"RQ worker starting on queues: ['ingestion',
'default']"*; enqueue a job and observe it execute.

**Rollback:** stop the worker. The API keeps serving; ingestion jobs queue and
wait. This is the safe failure direction.

### 9. Storage

**Do:** create the three private buckets in the **staging** Supabase project, and
confirm each is **not** public.

**Verify:** `/health/storage` → 200; upload → sign → signed download (sha256
match) → delete → list empty.

**Rollback:** delete the buckets.

**Never:** make a staging bucket public, even briefly.

### 10. Authentication

**Do:** create staging users in the staging Supabase project per
`docs/STAGING_DATA_POLICY.md` (`.invalid` emails, random passwords held in the
password manager). Set `SUPABASE_URL` to the **staging** project on the API, the
worker, and the frontend's `VITE_SUPABASE_URL` — all three, or every token is
rejected with 401.

**Verify:** sign in → token issued → 6 authenticated routes 200 → 3 admin routes
403 for a STUDENT.

**Rollback:** delete the staging users.

### 11. AI test configuration

**Do:** set `AI_PROVIDER_API_KEY` (staging), `AI_PROVIDER_MODEL`, and a low
`AI_MONTHLY_CEILING_USD`. `AI_PROVIDER_FALLBACK_MODEL` may be left **unset** (one
attempt, honest) or set to a genuinely different model; an identical pair is
rejected at startup by design.

**Verify:** a grounded answer is returned, **or** the quotations-only degradation
occurs — both are acceptable. An invented section number or rate is not.

**Rollback:** unset the key; the assistant degrades to quotations-only.

### 12. Razorpay TEST

**Do:** set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`
from the **Razorpay test dashboard**, entered in the Render dashboard
(`sync: false`) and **never** in git.

**Verify — the full payment lifecycle, in this order:**

| # | Check | Expected |
|---|---|---|
| 1 | Order creation | `POST /api/v1/payments/order` returns an order for a synthetic user |
| 2 | Signature verification | a tampered signature is rejected |
| 3 | Webhook verification | a webhook with a bad signature → **401** |
| 4 | Valid webhook | entitlement activates for the synthetic user |
| 5 | Idempotency | the same `event_id` twice → processed **once** |
| 6 | Failed payment | a declined test card leaves no entitlement |
| 7 | Duplicate webhook | replayed after success → no double credit |

**Rollback:** unset the keys. Payments return the graceful 503 that names
`RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET` — verified working, so the unset state is
safe and explicit.

**Never** request or configure production Razorpay keys at this stage.

### 13. Frontend

**Do:** deploy the frontend with `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`
for the **staging** project, and `CORS_ORIGINS` on the API set to the staging
frontend origin. Resolve **DG-1** (SPA rewrite) and **DG-6** (API base URL) first —
without them a deep link 404s and the bundle has no way to reach the backend.

**Verify:** sign in from the browser; a deep link and a browser refresh both
resolve; an authenticated API call succeeds.

**Rollback:** redeploy the previous deployment. The frontend is stateless, so this
is instant and total.

### 14. Smoke tests

**Do:** run the six areas against the deployed staging URL, in this order:

| # | Area | Pass condition |
|---|---|---|
| 1 | Authentication | valid JWT → 200; garbage/no token → 401; STUDENT → 403 on admin |
| 2 | Database health | `/health/db` 200 |
| 3 | Storage | upload → sign → download (sha match) → delete |
| 4 | Redis | `/health/redis` 200; a job executes end-to-end |
| 5 | OCR | both a text-layer and a scanned PDF produce a draft |
| 6 | RBAC | the role × route matrix returns no unexpected 5xx |

Plus: **every response carries `X-Request-Id`**, and a provoked 500 returns one in
both the header and the logs (F-03).

**Verify:** all six pass, with the request-id and 500 checks included.

**Rollback:** n/a — this step changes nothing.

### 15. Rollback

**The rollback procedure, stated once, applies to any step above:**

| Failure | Action | Explicitly NOT done |
|---|---|---|
| Frontend broken | redeploy the previous frontend deployment | — |
| Backend code broken | roll back the API to the previous deploy | schema untouched |
| Worker broken | stop the worker; jobs queue | API keeps serving |
| Migration wrong | roll back the app; write a new forward migration | `alembic downgrade` |
| Credentials wrong | unset them; the named 503 is the safe state | — |
| Anything untrustworthy | destroy the staging database and rebuild from `app.seed` | — |

**Staging is disposable by design.** Rebuilding it is a legitimate rollback, and
preferable to debugging a contaminated environment. The only thing that must never
be rebuilt from is a production backup.

`fromService: caprep-redis`, so the service must exist or the deploy fails. The
`noeviction` policy in `docker-compose.yml` is deliberate and should be carried
over: under `allkeys-lru` a memory spike silently discards queued ingestion jobs
while the API still answers 200 for "job accepted".

**Verify:** `redis-cli ping` → `PONG`; `/health/redis` → 200.

**Rollback:** delete the instance.
