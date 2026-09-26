# Production Readiness

Assessment of whether the code, configuration, and process in this archive could
safely serve a real student today. Prepared 27 September 2026.

**Bottom line: NOT ready to Go-Live, but structurally close.** The application
code is production-shaped (enforced CORS, no secret in browser, hardened auth,
honest 503s for unconfigured integrations), but the deployment layer has never
been executed and several process gaps are unresolved.

## Frontend (Vercel)

| Item | Status | Notes |
| --- | --- | --- |
| Production build | **PASS** | `npm run build` succeeds; per-route chunks, source maps on; Node 20.19.0 pinned (`.nvmrc`) |
| `vercel.json` | **MISSING** | nothing in the repo configures Vercel (build cmd/out dir/env). Relies on framework detection + dashboard; SPA rewrites for `/admin/*`, `/learn`, `/practice` etc. are unrecorded — without a rewrite rule React Router returns 404 on deep links |
| Vercel env vars | UNVERIFIED | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (publishable), `VITE_API_BASE_URL` (Render origin), `VITE_SITE_URL` must be set in the dashboard |
| SPA routing / HTTPS / asset paths | UNVERIFIED | no deployment has been run |
| Service worker | PARTIAL | `sw.js` registers in production only; does not cache `/api` (documented); PWA install incomplete (no full icon set) — non-blocking |

## Backend (Render)

| Item | Status | Notes |
| --- | --- | --- |
| `infra/render.yaml` | **Present, unapplied** | API web service + worker (docker) + managed Postgres; `preDeployCommand: alembic upgrade head`; health check `/health`; `autoDeploy: true`; region singapore |
| Migration safety | **GOOD** | migrations run as a pre-deploy gate (abort on failure, previous version keeps serving); multi-instance safe; worker is `type: worker` not `type: web` (documented RQ pitfall avoided) |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2` | fine; note `--workers 2` with the async engine and pool size 5 — review under load |
| CORS | **GOOD** | production boot refuses empty/`*` allow-list; `CORS_ORIGINS` must be set explicitly |
| Logging | **GOOD** | structured JSON to stdout with `request_id`; global 500 handler returns the documented error shape (no stack leak) |
| Health checks | **GOOD** | `/health`, `/health/db`, `/health/redis`, `/health/storage` |
| Database connection | PARTIAL | `DATABASE_URL` + `DIRECT_DATABASE_URL` (pooler vs direct) handled by `db_urls.py`; Supabase pooler port auto-detected; prepared-statement workaround behind poolers — but never exercised live |
| Redis | PARTIAL | `REDIS_URL` wired; worker needs it; no real run yet |
| Worker deps | **GOOD** | Docker image installs tesseract-ocr-eng + poppler-utils — the system binaries Render's native runtime lacks |

## Database

| Item | Status | Notes |
| --- | --- | --- |
| Migrations on empty DB | **GOOD** | upgrade→downgrade→upgrade executed here on a fresh Postgres |
| `alembic check` drift | **GOOD** | no pending ops (one known benign warning) |
| Production-safe migration path | GOOD | direct (non-pooler) URL for migrations; transactional DDL |
| Backups | **NOT PROVEN** | Render daily snapshots assumed; the render.yaml comment itself says "TEST A RESTORE — an untested backup is not a backup" |
| Connection pooling | PARTIAL | app pool 5/10 with `pool_pre_ping`; supabase pooler handling coded, unverified live |
| Seed | GOOD | idempotent, deterministic ids; curriculum + starter bank; `--check` mode |

## Storage

| Item | Status | Notes |
| --- | --- | --- |
| Buckets | private (3) | declared in code; must be created in the Supabase project with matching names |
| Signed URLs | backend-brokered, short-lived | the single authorization boundary; verify against real project (blocked here) |
| Upload limits / validation | present | `validate_upload` (filename/content-type/size) before URL minting; oversized handled |
| Download authorization | per-document access engine | enforced before signing; tested |

## Payments

| Item | Status | Notes |
| --- | --- | --- |
| Webhook URL | unconfigured | must point `POST https://<api>/api/v1/webhooks/razorpay` in the Razorpay dashboard |
| Webhook secret | empty | `RAZORPAY_WEBHOOK_SECRET` unset → webhook rejects every event (fail-closed by design) |
| Test/production separation | by key pair | test-mode keys → test mode; no mock-mode switch in code (do not ship test keys as production env vars) |
| Idempotency / signature / amount | GOOD | verified by 130+ offline tests; no real gateway round-trip yet |

## Identity

- `SUPABASE_URL` must match the frontend's `VITE_SUPABASE_URL` exactly (issuer check → 401s on drift). **Before Go-Live: rotate keys (§6 of SECURITY_AUDIT.md), enable Google OAuth + redirect URLs, and confirm email confirmation templates.**

## CI / CD

- `.github/workflows/ci-cd.yml` is complete and correct-looking, but **has never run** (no git repo, no remote).
- Gates today would fail on ruff (29) and vitest (deterministic checkout + flake) — see `TEST_BASELINE.md`.
- Recommend: `git init` → commit `.gitignore` first → secret-scan first commit → enable CI → fix gates → then deploy.

## Go-Live checklist (ordered)

1. Rotate all `.env` credentials (SECURITY_AUDIT §6).
2. Create git repo; scan first commit for secrets; run CI once green.
3. Fix ruff gates (P1-1); fix checkout test (P2-1); isolate vitest flake (P1-5).
4. Configure Supabase project: enable Google OAuth, redirect URLs, publishable key; create buckets `question-pdfs` (+ 2 others per code); set RLS policies or rely on backend-only access (documented).
5. Render blueprint apply: set `DATABASE_URL`/`DIRECT_DATABASE_URL`, `REDIS_URL`, `SUPABASE_SECRET_KEY` (NEW rotated key), `CORS_ORIGINS`, Razorpay test keys, `AI_PROVIDER_API_KEY` (rotated).
6. Add `vercel.json` with SPA rewrite + output dir; link Vercel project; set `VITE_*` env vars.
7. Test a restore of the Render snapshot backup.
8. Run `scripts/live_verify.py` against the deployed API (requires the script's token minting) and record the fresh RBAC matrix.
9. End-to-end payment smoke with Razorpay **test cards** (order → checkout → confirm/webhook → entitlement), then switch to live keys only for real launch.
10. Performance smoke: page loads on the landing page; verify no unbounded queries on `/practice/questions` and `/search` with seeded scale.

## Known production blockers (must-fix before launch)

- B-1: No `vercel.json` → deep-link 404s on Vercel (P1-6).
- B-2: Credentials in the archive `.env` — rotate (S-1).
- B-3: CI red (ruff/vitest) — no trustworthy pipeline (P1-1, P1-5).
- B-4: Zero live verification of storage signing, RQ jobs, Razorpay webhooks (blocked on credentials/infra).
- B-5: Supabase migrations and pooler path unverified against the actual provider (the code handles it; nothing has run against Supabase Postgres).

## Verify commands (as run)

```bash
# Backend production-shape gates
cd apps/api
python -m pytest -o addopts="" -q                 # 995 passed / 4 skipped
ruff check app tests alembic                      # 29 errors  → P1-1
ruff format --check app tests alembic             # 5 files    → P1-1
alembic upgrade head && alembic check             # PASS

# Frontend production-shape gates
cd apps/web
npm run typecheck && npm run lint                 # PASS
npm run build                                      # PASS
npm test                                           # flaky + 1 deterministic → P1-5 / P2-1
```