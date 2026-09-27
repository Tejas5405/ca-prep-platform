# Staging Deployment Status

**Milestone:** P2 — Staging Deployment & Full Staging E2E
**Baseline commit:** `fbabeaa` (`main`, == `origin/main`, CI green)
**Status: BLOCKED at Phase 0. Nothing has been deployed.**

---

## Summary

Staging deployment **cannot start**. Phase 0 requires identifying a real staging
frontend host, staging backend host, staging database, staging Supabase Auth
project, staging storage, staging Redis, staging worker and Razorpay **TEST**
keys. Investigation found that **none of these exist as staging resources**.

This is a genuine external blocker, not a gap in the plan. Per the milestone
instruction — *"If any required external resource is missing: STOP and report
exactly what is missing. Do NOT invent infrastructure or silently substitute
production resources"* — execution stopped here.

**No deployment was attempted. No production resource was touched. No secret
value was printed. No file was modified outside this document.**

---

## Phase 0 — pre-flight inventory

Names and classification only. **No values are printed anywhere in this
document.**

### What exists

| # | Component | Provider | Environment | Status |
|---|---|---|---|---|
| 1 | **Frontend** | none | — | ⛔ **MISSING** — no `vercel.json`, no SPA rewrite config, no deployed URL (DG-1, DG-6) |
| 2 | **Backend API** | Render (`type: web`) | — | ⛔ **MISSING** — `infra/render.yaml` is a blueprint only; no service created; sets `ENVIRONMENT: production` literally (DG-8) |
| 3 | **Worker** | Render (`type: worker`, Docker) | — | ⛔ **MISSING** — depends on Redis, which is commented out of the blueprint (DG-4) |
| 4 | **Database** | local Postgres 17 `localhost:5432` | **LOCAL only** | ⛔ **NO STAGING INSTANCE** (DG-2) |
| 5 | **Auth** | Supabase project `zyrmlnpvylhcpyaoizyz` | **REAL / SHARED** | ⛔ **NO STAGING PROJECT** (DG-3) |
| 6 | **Storage** | Supabase Storage, 3 private buckets (`question-pdfs`, `question-media`, `user-uploads`) | **REAL / SHARED** | ⛔ **NO STAGING BUCKETS** (DG-3) |
| 7 | **Redis** | local Homebrew Redis | **LOCAL only** | ⛔ **NO STAGING INSTANCE** (DG-4) |
| 8 | **OCR** | Tesseract + Poppler in the worker image | LOCAL verification only | ⛔ **NOT DEPLOYED** — cannot be verified without a worker |
| 9 | **AI** | Google Generative Language | **REAL / SHARED** (`AI_PROVIDER_API_KEY` populated) | ⚠️ **NO STAGING-SCOPED CREDENTIAL** |
| 10 | **Payments** | Razorpay | none | ⛔ **MISSING** — all three `RAZORPAY_*` variables are **EMPTY** |
| 11 | **Logging / alerting** | none | — | ⛔ **MISSING** — no Sentry/PostHog/uptime monitor configured (DG-5) |

### Credential state (populated vs empty — no values)

Verified from the untracked local `.env`, which is the **local development**
environment, not staging:

| Variable | State | Class |
|---|---|---|
| `DATABASE_URL` | POPULATED → host `localhost:5432` | **local dev DB** |
| `REDIS_URL` | POPULATED → local Redis | **local dev Redis** |
| `SUPABASE_URL` | POPULATED → `zyrmlnpvylhcpyaoizyz.supabase.co` | **real, shared project** |
| `SUPABASE_SECRET_KEY` | POPULATED | **real server-side secret** |
| `AI_PROVIDER_API_KEY` | POPULATED | **real, shared** |
| `RAZORPAY_KEY_ID` | **EMPTY** | — |

---

## Phase 1 — environment separation: **NOT SATISFIED**

Staging would currently **share every mutable resource with real services**:

| Resource | Would staging share it with production? | Verdict |
|---|---|---|
| Database | local dev Postgres, not a dedicated staging instance | ⛔ |
| Supabase Auth | yes — `SUPABASE_URL` is one literal in `render.yaml` | ⛔ **identical project** |
| Storage | yes — same project, same 3 buckets | ⛔ **identical buckets** |
| Redis | local dev Redis | ⛔ |
| AI | yes — the one real API key | ⛔ |
| Razorpay | n/a — no keys at all | ⛔ |

Deploying today would point a staging environment at the **real Supabase
project, real Auth users, real storage buckets and a real AI key**. That is
precisely what the milestone forbids, so Phase 1 cannot be honestly marked
passed.

Additionally, `infra/render.yaml` sets `ENVIRONMENT: production` **literally** on
both services, so a staging deploy would misreport itself as production and
alter CORS and boot strictness (DG-8).

---

## Phase 2 — database: **partially verifiable, staging absent**

What was verified read-only, against the **local** database only:

- `alembic heads` → **`6c3f7b0a0c13` (head)** — matches the revision named in
  the milestone.
- `alembic current` → **`6c3f7b0a0c13`** — the local DB is at head.
- `alembic check` → **"No new upgrade operations detected"**.

These confirm the **repository's** migration chain is correct and consistent.
They say **nothing** about a staging database, which does not exist.
`alembic upgrade head` was **not** run against any staging instance because
there is none, and running it against the local DB would prove nothing new.

---

## What is required to unblock

Owner actions. Each is external provisioning; none can be resolved from the
repository.

| # | Required | Why it blocks | Gap |
|---|---|---|---|
| 1 | **Staging Supabase project** (DG-3) | auth, storage and the project identity are all one project today; without a second one, staging touches real identities and real buckets | DG-3 |
| 2 | **Staging Postgres instance**, database `caprep_staging` (DG-2) | migrations have nowhere to run | DG-2 |
| 3 | **Staging Redis** (DG-4) | `REDIS_URL` is injected from a service that is commented out; the worker cannot start | DG-4 |
| 4 | **Razorpay TEST keys** (`KEY_ID`, `KEY_SECRET`, `WEBHOOK_SECRET`) | all three are empty; Phase 8 cannot run at all | — |
| 5 | **Frontend host decision + SPA rewrite** (DG-1) | a client-routed SPA 404s on deep link and refresh without it | DG-1 |
| 6 | **`VITE_API_BASE_URL`** decision (DG-6) | the deployed bundle has no way to reach the backend | DG-6 |
| 7 | **Render staging environment** (DG-8) | blueprint is production-literal | DG-8 |
| 8 | **Observability choice** (DG-5) | no alerting or error tracker | DG-5 |
| 9 | **Staging AI credential** | the only real key must not be shared | — |
| 10 | **Domain / URL confirmation** for frontend and backend | needed for the `CORS_ORIGINS` allow-list | — |

### Minimum viable path

Items **1, 2, 3, 7** are the hard blockers for Phases 9–11 (deploying backend,
worker, frontend). Item **4** additionally blocks Phase 8. Items **5, 6, 10**
are needed for Phase 11 to be verifiable at all (deep-link refresh, CORS).

A **local staging substitute** (docker-compose with a separate database, a
second Supabase project, and TEST-mode Razorpay) would close the separation
requirement without any production exposure, and is the fastest way to begin
Phases 2–8. It does **not** satisfy Phases 9–11, which require real hosts.
This is a recommendation only — no such environment was created.

---

## Readiness matrix (computed, not claimed)

Per the milestone's definitions, with no deployment performed:

| Flag | Value | Basis |
|---|---|---|
| `STAGING_DEPLOYED` | **NO** | no staging host exists |
| `STAGING_AUTH_VERIFIED` | **NO** | no staging Auth project |
| `STAGING_DATABASE_VERIFIED` | **NO** | no staging DB (local-only checks performed) |
| `STAGING_STORAGE_VERIFIED` | **NO** | no staging buckets |
| `STAGING_REDIS_VERIFIED` | **NO** | no staging Redis |
| `STAGING_WORKER_VERIFIED` | **NO** | no deployed worker |
| `STAGING_OCR_VERIFIED` | **NO** | requires a deployed worker |
| `STAGING_AI_VERIFIED` | **NO** | no staging-scoped credential |
| `STAGING_PAYMENT_TEST_VERIFIED` | **NO** | Razorpay TEST keys absent |
| `STUDENT_E2E_VERIFIED` | **NO** | no staging URL |
| `ADMIN_E2E_VERIFIED` | **NO** | no staging URL |
| `SECURITY_VERIFIED` | **NOT_TESTED** | Phase 14 inspects a deployed bundle |
| `OBSERVABILITY_VERIFIED` | **NOT_TESTED** | nothing deployed to observe |

Carried forward unchanged from the proven-green baseline:

```
LOCAL_GREEN     = YES    (248 web, 803 backend, Redis ON and OFF)
CI_CONFIGURED   = YES
CI_EXECUTED     = YES
CI_GREEN        = YES    (runs 36350781773 and 36351190711, all 4 jobs)
```

**Production readiness is not claimed and is not assessable.** Nothing in this
milestone changed application architecture, credentials, schema or code.

---

## Repository state

- Working tree clean at `fbabeaa`; only this document is added.
- No force push, no history rewrite, no deployment, no production access.
- Secret scanner CLEAN on worktree, staged and `HEAD`.

| `RAZORPAY_KEY_SECRET` | **EMPTY** | — |
| `RAZORPAY_WEBHOOK_SECRET` | **EMPTY** | — |
| `RESEND_API_KEY` | **EMPTY** | — |

No `STAGING_*` variable exists anywhere in the repository (searched all
`.py`, `.ts`, `.tsx`, `.yml`, `.yaml`, `.example` files). The `STAGING_*` names
in the milestone are a **target naming scheme to be adopted**, not existing
configuration.
