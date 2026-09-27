# Live Infrastructure Baseline

**Environment:** LOCAL (development) — **corrected** from the template's "STAGING"; no staging
deployment exists (no git remote, no Render/Vercel URLs). See `LIVE_INTEGRATION_REPORT.md` §1.
**Verification Date:** 2026-09-27 / 2026-09-28 (IST)
**Verifier:** Live integration verification milestone
**Status:** baseline completed and verified against real external services where credentials
exist; per-phase results and findings in `docs/LIVE_INTEGRATION_REPORT.md`.

---

## 1. External Dependencies Inventory

| Dependency | Provider | Environment | Purpose | Status |
|------------|----------|-------------|---------|--------|
| **PostgreSQL Database** | Supabase (managed project `zyrmlnpvylhcpyaoizyz`) | Local Postgres 17 `localhost:5432` for verification; Supabase project for storage/auth | Primary data store | ✅ Verified (canonical-chain DB); ⚠️ default `.env` URL divergent — see report F-01 |
| **Supabase Auth** | Supabase | Real project | JWT issuance + JWKS verification | ✅ Verified (admin create → password grant → JWKS → authenticated calls 200, admin routes 403 for STUDENT) |
| **Supabase Storage** | Supabase | Real project | File storage (3 private buckets) | ✅ Verified (upload → sign → signed download sha-match → delete) |
| **Redis** | Local (Homebrew) | Local host | Cache, rate limiting, RQ queue | ✅ Verified (`PONG`, `/health/redis` 200, RQ job round-trip FINISHED) |
| **RQ Worker** | `python -m app.workers.rq_worker` | Local process | Async jobs | ✅ Verified (worker boot + job executed, "Job OK") |
| **AI Provider** | Google Generative Language (`gemini-3.8-flash`) | Real API key | Grounded study suggestions | ✅ Verified (live grounded answer returned); provider intermittently 503 — see report F-05 |
| **Razorpay** | Razorpay **test mode** | Not configured | Payments | ⛔ BLOCKED_EXTERNAL — all `RAZORPAY_*` empty; graceful 503 with named missing keys verified; no payment attempted |
| **Backend Deployment** | Render | Not deployed | FastAPI hosting | ⛔ BLOCKED_EXTERNAL — `render.yaml` is a blueprint only |
| **Frontend Deployment** | Vercel | Not deployed | React SPA hosting | ⛔ BLOCKED_EXTERNAL — no `vercel.json`, no deployed URL; local build + preview smoke verified instead |

---

## 2. Environment Classification

| Environment | Purpose | Data Sensitivity |
|-------------|---------|------------------|
| **LOCAL** | Developer machine, ephemeral data | Synthetic test data only |
| **STAGING** | Pre-production verification, mirrors production config | Synthetic/anonymized test data only |
| **PRODUCTION** | Live customer-facing system | Real user data - **NO TESTING HERE** |

**Actual verified environment: LOCAL (development).** Evidence: `.env` `ENVIRONMENT=development,
DEBUG=true`; `apps/web/.env.local` `VITE_APP_ENV=development`; no git remote; no deployed URLs.
Real external services used from this LOCAL environment: Supabase project (auth, storage, JWKS)
and Google Gemini. No production system or production data was touched.


---

## 3. Deployment Targets (none exist yet — verified)

### Backend (API) — ⛔ not deployed
- **Platform:** Render Web Service (blueprint `infra/render.yaml`; nothing provisioned)
- **Runtime:** Python 3.11+ (FastAPI + Uvicorn)
- **Entry Point:** `apps/api/app/main.py`
- **Process:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Health Endpoint:** `GET /health`
- **Verified instead:** local instances (ports 8010/8011/8012) against real Supabase + real Gemini;
  `/health`, `/health/db`, `/health/redis`, `/health/storage` all answered with the documented semantics.

### Backend Worker (RQ) — ⛔ not deployed
- **Platform:** Render Background Worker (blueprint only)
- **Entry Point:** `python -m app.workers.rq_worker`
- **Queue:** `ingestion`, `default` (Redis-backed)
- **Verified instead:** local worker booted and executed a real enqueued job (`Job OK`).

### Frontend (Web) — ⛔ not deployed
- **Platform:** Vercel (no project linked, no `vercel.json` in this checkout)
- **Framework:** React + Vite + TypeScript
- **Build Command:** `npm run build` (verified locally: built in ~1.2 s)
- **Output Directory:** `dist`
- **Verified instead:** `vite preview` smoke — index 200, SPA route 200, hashed asset 200;
  bundle contains no server-only material (service-role key absent).

---

## 4. Network & Security

| Component | Configuration |
|-----------|---------------|
| **CORS Origins** | Explicit allow-list (no wildcards in staging/production) |
| **TLS** | Enforced by Render/Vercel (HTTPS only) — not verifiable, nothing deployed |
| **Auth** | Supabase JWT verified against live project JWKS (ES256/P-256 key observed) |
| **API Keys** | Server-side only — verified absent from the built frontend bundle |
| **Database** | Supabase pooled connection (transaction mode) — not applicable locally |
| **Direct DB URL** | For migrations only (bypasses pooler) |

---

## 5. Verification Checklist

### Pre-Verification Gates
- [x] Local quality gates: GREEN (re-verified at `4f11473` in the documented environment: ruff,
      ruff format, eslint, tsc, web 248 tests, backend 998 passed / 1 skipped, secret scanner CLEAN)
- [x] CI baseline: GREEN (local rehearsal — `docs/CI_EXECUTION_REPORT.md`)
- [ ] Remote CI execution: **NOT VERIFIED — no git remote in this checkout** (blocked, reported)
- [x] No uncommitted secrets in working tree (`scripts/check_secrets.py --worktree` → CLEAN)
- [ ] Staging environment: **does not exist** (no deployed backend/frontend found)
- [x] Test credentials (non-production): Supabase service/anon keys + Gemini key present; used to
      create a synthetic `live-verify+student@example.test` account (password random, not stored)

### Environment Variables Audit
- [x] Frontend variables: only `VITE_*` public values in `apps/web/.env.local`
- [x] Backend variables: secrets live only in the server-side `.env`
- [x] No credential leakage in built frontend bundle (service-role key absent; publishable/anon
      key present by design)
- [x] Service-role keys, DB credentials, AI keys protected (never printed or committed in this
      milestone; Razorpay/Resend/PostHog/Sentry keys are absent, not leaked)

---

## 6. Test Accounts (created / seeded during verification)

| Role | Email Pattern | Purpose |
|------|---------------|---------|
| Student (real Supabase user) | `live-verify+student@example.test` | Real sign-in + authenticated student-flow verification |
| Student / Editor / Content Manager / Moderator / Admin / Super Admin (local fixtures) | `live-verify+<role>@example.test` | RBAC sweep in `caprep_rebaseline_proof` (107 routes × 6 roles) |

---

## 7. Safety Rules

1. **NO PRODUCTION DATA** - synthetic accounts and one probe object only; the probe object was deleted
2. **NO REAL PAYMENTS** - Razorpay keys absent; only the documented 503 / 401 degradation was exercised
3. **NO SECRETS IN LOGS** - keys are referenced by name/length only; no value appears in this report
4. **NO SCHEMA CHANGES** - databases were read; `alembic current`/`check` only, no `upgrade`/`stamp`
5. **NO FEATURE CHANGES** - verification only; findings are reported, not silently fixed
6. **STOP ON BLOCKERS** - missing infrastructure (remote CI, Razorpay keys, deployments) is reported
   as `BLOCKED_EXTERNAL` rather than improvised

---

*Baseline completed. Per-phase results, failure details (repro / severity / cause / fix) and
evidence: `docs/LIVE_INTEGRATION_REPORT.md`.*