# Current System Audit

- **Date of audit:** 27 September 2026
- **Baseline:** `ca-prep-platform/` as extracted from `CA Version 2.zip` (no `.git` directory is present anywhere in the archive, so no commit can be named).
- **Method:** static reading of the source tree + executed quality gates. Every claim below was produced either by reading the file named or by running the command in `docs/TEST_BASELINE.md`.
- **Status legend used throughout:**
  - `COMPLETE` — implemented, connected, and verified (by tests executed here or by prior live verification recorded in `docs/verification/`).
  - `PARTIAL` — implemented but with gaps named in the entry.
  - `BROKEN` — present and failing.
  - `UNTESTED` — implemented; no automated or live evidence found here.
  - `BLOCKED_EXTERNAL` — code exists but cannot be verified without a credential/infrastructure/account not available in this workspace.
  - `DEFERRED` — deliberately not built; the blueprint/stack amendments name a trigger.

---

## 1. Architecture — COMPLETE (with one hygiene caveat)

Monorepo-style layout under `ca-prep-platform/`:

- `apps/api` — FastAPI + SQLAlchemy 2 (async, psycopg 3) + Alembic + RQ worker + OCR pipeline.
- `apps/web` — React 19 + TypeScript + Vite + Tailwind 4 + React Router 7; Supabase auth client; purpose-built `api.ts` HTTP client (no React Query / React Hook Form / Zod, a recorded deviation in `docs/DELIVERY_REPORT.md`).
- `infra/` — `render.yaml` blueprint (API + worker + managed Postgres) and `docker-compose.yml` (local Postgres + Redis).
- `.github/workflows/ci-cd.yml` — full pipeline: ruff, ruff format, mypy (advisory), alembic upgrade + drift check + migration reversibility job, pytest, and the complete web gate set.
- `docs/` — delivery report, build audit, stack amendments, verification evidence (live RBAC matrix).
- `scripts/` — `dev-stack.sh` (local stack recovery), `live_verify.py` (live RBAC sweep), `local_auth.py` (local JWKS harness).

**Caveat (hygiene, not architecture):** the workspace root also contains `_archive_v2_nestjs_stack/` (retired stack, reference only), `.config/`, `.pki/`, `syslibs/`, `tools/`, `uploads/`, and (critically) a real `.env` with live credentials — see `SECURITY_AUDIT.md`. The application itself is `ca-prep-platform/`.

**Discrepancy found:** `docs/DELIVERY_REPORT.md` (25 Sep 2026, revision `0007_collections`) describes an 88-path API and 7/8 migrations. The tree on disk has **153 OpenAPI paths and 15 migration files through head `6c3f7b0a0c13`**. The report is stale relative to the code; the newer `docs/REMAINDER_STATUS.md` and the generated OpenAPI are current.

## 2. Frontend — COMPLETE (implementation), PARTIAL (integration proof)

- 21 student screens + 14 admin console sections + public marketing pages, all routed in `apps/web/src/app/App.tsx`. Routes confirmed: `/dashboard`, `/learn`, `/study`, `/practice`, `/mocks`, `/planner`, `/revision`, `/doubts`, `/progress`, `/search`, `/collections`, `/ldr`, `/library`, `/subscription`, `/upgrade`, `/inbox`, `/achievements`, `/assistant`, `/profile`, plus `/admin/*`.
- Auth wall via `ProtectedRoute`; role-gated admin shell (`minimumRole="EDITOR"`).
- Lazy loading per route (confirmed by `npm run build`: per-route chunks, largest `index` 406 kB / 122 kB gzip).
- **Gaps (PARTIAL):**
  - No browser-level E2E suite (only Vitest unit/component tests).
  - Never verified against a real deployed stack (Vercel + Render + Supabase + Google OAuth) — stated in `docs/DELIVERY_REPORT.md` and confirmed by the absence of deployment config (§15).
  - `npm test` is **flaky under full parallel run** (10 failures in run 1, 2 in run 2, one deterministic) — `TEST_BASELINE.md`.

## 3. Backend — COMPLETE

FastAPI app at `apps/api/app/main.py`; 179 route decorators across 21 routers, 153 OpenAPI paths. Layering is disciplined: `api/v1` (routers) → `services` (pure domain logic) → `repositories` (SQLAlchemy) → `models`. Envelope contract (`app/core/envelope.py`) enforced app-wide; RFC 7807 error bodies (`api.ts` client + `test_api_contract.py` assert it). Request-id contextvar for structured logging; async engine + `AsyncSession` throughout. Global 500 handler returns the documented shape and logs with the request id — an unhandled exception cannot leak a stack trace.

## 4. Database — COMPLETE (chain) / BROKEN (workspace schema drift)

- Fresh-database proof (executed here): `alembic upgrade head` → `downgrade base` → `upgrade head` all succeed on a brand-new database. `alembic check` → "No new upgrade operations detected" (only the known `document_pages.search_vector` computed-default warning). Fresh schema: **58 tables, 96 CHECK constraints, 1 FTS index**.
- 15 migration files on disk: `0001_initial` → `6c3f7b0a0c13` (linear; every `down_revision` resolves).
- **BROKEN (workspace-local):** both pre-existing databases (`caprep`, `caprep_test`) are stamped `alembic_version = '0014_drop_duplicate_queue_index'`, a revision with **no migration file in this archive**. `alembic current/upgrade/downgrade` fail on them. The on-disk chain is fine — a fresh DB migrates cleanly — so this is stale local state (a migration deleted from source after being applied). Reusing the old `.env` DBs will break migrations; see `NEXT_EXECUTION_PLAN.md` P0-4.
- Indexes/unique constraints/partial-unique payment indexes, CHECK constraints on enum columns (VARCHAR + CHECK rather than PG enums, deliberately), soft delete + `is_active` for users, deterministic uuid5 seeding. No `password_hash` column anywhere (auth owned by Supabase).

## 5. Authentication — COMPLETE (code + unit) / BLOCKED_EXTERNAL (live)

- Supabase Auth access tokens verified **locally against the project JWKS** (`PyJWKClient`, cached), not via a per-request `get_user` call.
- Algorithm pinned to `ES256`/`RS256`; `aud=authenticated`, `iss=<project>/auth/v1` verified; anon/service_role API keys are refused as bearer tokens; role read from `app_metadata.role`/namespaced claim, never `user_metadata` (the previous framework's privilege-escalation bug is called out and regression-tested — `core/security.py`, `tests/test_security.py`).
- Account provisioning on first authenticated request (`core/identity.py`), barred/deleted-account refusal paths, committed transaction, race-safe.
- **BLOCKED_EXTERNAL here:** `SUPABASE_URL` is not set in this workspace, so the API refuses to verify tokens. The JWKS path needs the real project URL to run live; unit/contract tests cover the logic offline. Google OAuth is code-complete but not enabled on the project (per `docs/DELIVERY_REPORT.md` §7).

## 6. Authorization — COMPLETE

- `require_role` (token claim rank) and `require_permission` (DB row role → permission matrix in `app/core/permissions.py`), with `OWNER_ONLY` (MANAGE_SETTINGS) carve-out for SUPER_ADMIN.
- Matrix pinned to the blueprint by `tests/test_permission_matrix.py`.
- Cross-user isolation: `resolve_owned_user_id` rejects any student request for another user's id (403, logged).
- Content access is a separate engine (`services/entitlements.py` + `services/content_library.py`): grants/filters, tier, plan, deny-beats-allow.
- **Prior live evidence:** `docs/verification/live-rbac-matrix.md` — 101 routes × 6 roles = 606 requests, **0 unhandled 5xx, 0 authorization mismatches**. The generator (`scripts/live_verify.py`) reads required permissions from route closures, so expectations cannot drift from code.

## 7. Storage — COMPLETE (code) / BLOCKED_EXTERNAL (live)

- Supabase Storage, 3 private buckets; **backend-brokered signed URLs** — the API validates upload metadata, mints short-lived signed upload/download URLs with the secret key; the browser never holds the secret (one authorization boundary, not two).
- `_build_headers` distinguishes `sb_secret_...` (apikey header) from legacy service_role JWT (Bearer) — asserted by `tests/test_storage.py`.
- **BLOCKED_EXTERNAL here:** no live Supabase credentials available for verification, so the 8 storage-dependent routes correctly answer **503 in the API's own problem shape** (as the prior live RBAC matrix records).

## 8. OCR — COMPLETE (code) / PARTIAL (verification)

- Tiered extraction: PyMuPDF → pdfplumber → pdf2image + Tesseract (`app/ocr/extractor.py`); stage machine `QUEUED → … → AWAITING_QA → PUBLISHED/FAILED/REJECTED`.
- Worker image (`apps/api/Dockerfile`) apt-installs tesseract-ocr-eng + poppler-utils + libpq5 — the exact binaries missing from Render's native Python runtime (documented, failure mode analyzed).
- `tests/test_ocr.py` ran green this session (tesseract + pdftoppm present locally).
- **PARTIAL:** no E2E run of a real scanned-PDF job against a live worker (requires Redis/RQ + storage). "Automated ingestion must never publish" is enforced in code (explicit `PUBLISH_CONTENT` gate; asserted by `test_review_api.py`).

## 9. Background Jobs — COMPLETE (code) / BLOCKED_EXTERNAL (run)

- RQ worker (`app/workers/rq_worker.py`): separate ingestion/default queues, finite timeouts (900 s / 180 s), retries=2, failed-job registry. Render treats the worker as `type: worker` with `preDeployCommand` migration gating.
- **BLOCKED_EXTERNAL here:** no live Redis in the workspace; `test_queue_boundary.py` uses fakeredis and passes, but no real RQ job run was observed.

## 10. Payments — COMPLETE (code + offline tests) / BLOCKED_EXTERNAL (live money)

- Server-computed amounts from `PLANS` (`app/services/billing.py`, never trusts the client); order creation → browser checkout → confirm/webhook; server **fetches the payment back from Razorpay** and compares amount+order (anti-fraud); signature verification for both checkout return and webhook; idempotent `ON CONFLICT` order store; partial unique indexes on provider ids; entitlement granted only from the DB subscription row; gateway keys stored in `payment_gateway_config`, never serialised to the browser.
- `test_billing.py` (72 tests), `test_payments_api.py` (61), mutation-tested money path per `docs/architecture/build-audit.md`.
- **BLOCKED_EXTERNAL:** all three `RAZORPAY_*` env vars are **empty** in the archive; endpoints correctly answer 503 (`payments_enabled()`); no real charge or webhook has ever been exercised (docs and env agree).

## 11. Search — COMPLETE (feature), PARTIAL (scale)

- PostgreSQL FTS + trigram over `questions` and extracted `document_pages` text (GIN indexes from raw SQL in migration 0001; the known `search_vector` computed-default warning is the only `alembic check` noise).
- Search never returns rows the caller may not read (`tests/integration/test_postgres_content_library.py` asserts this); `%` in admin search is escaped (literal, not wildcard).
- **PARTIAL (scale trigger):** offset pagination and FTS are documented as acceptable until the question bank crosses the blueprint's scale trigger; no search server (deliberate anti-goal).

## 12. Student Features — COMPLETE (surface), PARTIAL (integration proof)

Every listed surface has a page + serving endpoints: Dashboard, Learn/Study, Practice, Mocks (+report, exam mode), Planner, Revision (spaced repetition), Doubts, Progress (+projection), Search, Collections, LDR, Library (+reader, pages, search), Subscription, Profile, Notifications, Achievements, Assistant, Inbox, Support widget.

- Loading, empty, and error branches are present page-wide (pattern scan: every page references `isLoading`/`Skeleton` and `empty`/`No …`).
- **PARTIAL:** the "verify every journey against the real stack" milestone has not been executed as a recorded artifact here. Component tests cover many flows (studyPages, collectionsPages, libraryPages, inboxPage, checkout, login, achievements), but full journeys across a deployed stack are unproven. `docs/DELIVERY_REPORT.md` says the same.

## 13. Admin Features — COMPLETE (surface), PARTIAL (ops proof)

- Console sections: dashboard, people/users, access/roles (+permissions matrix), review queue, library (+bulk metadata), bulk upload, payments (orders/events/gateway), operations (notifications/badges/settings), insights (analytics/audit), studio (questions/tests/curriculum/plans/AI/storage), editorial queue, experiments, law notices, glossary, formulas, mentorship, marketplace.
- Dangerous mutations permission-gated per route and audited (asserted by `test_postgres_studio.py`); role changes take effect next request (DB-read matrix).
- **PARTIAL:** upload/extraction/QA/approve/publish E2E needs a live worker + storage; review workflows covered by `test_review_api.py` + `test_draft_review.py`.

## 14. Testing — COMPLETE (as designed) / PARTIAL (CI never ran)

- Backend: **995 passed / 4 skipped / 0 failed** this session against a fresh migrated database (360 s). Suite splits into pure unit + Postgres integration (skips loudly if the DB is unreachable; fails if asyncpg is missing).
- Frontend: 248 tests; typecheck/lint/build green; **vitest has one deterministic + several flaky failures** (`docs/TEST_BASELINE.md`).
- **PARTIAL:** `.github/workflows/ci-cd.yml` is well-formed but has **never executed** — the archive is not a git repository and there is no remote.

## 15. Deployment — PARTIAL

- `infra/render.yaml` complete and reviewed (service topology, `preDeployCommand` migrations, health checks, secrets `sync:false`). Never applied.
- **No `vercel.json`** exists anywhere; no committed Vercel config (build, output dir, SPA rewrite). Framework detection + dashboard settings could carry the app, but SPA routing and env plumbing are unrecorded (P1-6).
- CORS: the app refuses to boot in production with an empty or `*` allow-list (`main.py`).
- Local stack: `scripts/dev-stack.sh` targets a Linux embedded Postgres and will not run unchanged on macOS; local Homebrew Postgres works (used for this audit).

## 16. Security — COMPLETE (design), CRITICAL (credential exposure in the archive)

- No hardcoded secrets in source (swept this session). Server secret separation from the `VITE_*` browser namespace is structural and regression-tested (`apps/api/tests/test_infra_contract.py` asserts the bundle ships the publishable key and not the secret).
- `.gitignore` correctly excludes `.env`, `.env.*`, `*.local` with `.env.example` negation. **There is no `.git` directory at all**, so nothing can be proven about history — and the workspace root contains a **real, populated `.env`**.
- **CRITICAL:** the archive's `.env` holds real values for `SUPABASE_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (legacy JWT), `SUPABASE_ANON_KEY`, and `AI_PROVIDER_API_KEY`. The project's own comments state these transited a chat and must be rotated. Full detail in `docs/SECURITY_AUDIT.md`.

## 17. Environment Dependencies — PARTIAL

- Runtime deps pinned exactly (`requirements.txt`); dev deps pinned (`requirements-dev.txt`); Python ≥ 3.12 (CI 3.12, Render 3.12.6). This session ran the blessed suite on Python 3.13 in a fresh venv — clean.
- Node 20.19.0 pinned (`.nvmrc`, CI); local Node 22 works.
- Expects PostgreSQL, Redis, Supabase (Auth + Storage), Google OAuth, email, Razorpay. All optional server-side integrations answer 503 / scoped-absence when unconfigured rather than crashing.

## 18. Known Bugs

1. **Deterministic frontend test failure:** `checkout.test.tsx` "names the missing environment variables when the API has no gateway keys" — the page's fixed copy "...cannot take payments yet..." (`pages/Upgrade.tsx:140`) is only used when the 503 problem lacks `detail`; the API always returns one, so the fallback never renders and the test cannot pass. (P2-1.)
2. **Workspace DB drift:** `alembic_version` on the two dev DBs names a revision with no migration file in this archive; migrations fail on them until repaired. (P0-4.)
3. **Frontend test flakiness:** 8+ tests fail only under a full parallel vitest run and pass in isolation (landing/publicPages/login/libraryPages/admin). Cause not yet isolated. (P1-5.)
4. **Ruff gates are red on the current tree:** 29 `ruff check` errors (20× E501 in `app/api/v1/studio.py`; UP035/UP007/I001/C420 elsewhere) and 5 files fail `ruff format --check`. CI would fail today. (P1-1.)
5. **`docs/DELIVERY_REPORT.md` stale** (88 paths / 7 migrations / "981 passed" vs the current 153 paths / 15 migrations / 995 tests). (P2-3.)

## 19. Missing Integrations

- Google OAuth not enabled on the Supabase project (code present). `BLOCKED_EXTERNAL`.
- Live Razorpay (no keys), Resend (no key), PostHog (no key), Sentry (no DSN) — code-complete, none verified. `BLOCKED_EXTERNAL`.
- No Vercel config/linkage. `PARTIAL`.
- No git repo, no CI execution, no deployment, no production DB, no real Redis/RQ run, no real signed-URL storage round-trip, no scheduled job runner (re-verification is operator-button-driven). `BLOCKED_EXTERNAL`/`DEFERRED`.
- Video solutions removed by owner decision; mentorship/forum/calendar/pomodoro shipped as thin-but-honest versions (`docs/REMAINDER_STATUS.md`).

## 20. Recommended Execution Order

1. P0 security: rotate the exposed credentials; keep `.env` out of any future commit (§16, `SECURITY_AUDIT.md`).
2. P0 data: repair or re-baseline the stale dev databases (P0-4).
3. P0 auth/RBAC/content-isolation/payment checks — largely already covered by the 995-test suite + prior live matrix; re-run `scripts/live_verify.py` once `SUPABASE_URL` is configured.
4. P1: make CI real (git init, first commit, remote) and fix the red ruff gates + the deterministic checkout failure to a green baseline.
5. P1: isolate and fix the vitest flake.
6. P1: API↔frontend integration sweep across student flows, then the admin/content pipeline end-to-end (upload → storage → worker → OCR → review → publish).
7. P1/P2: Vercel config + SPA routing + env plumbing; production-readiness checklist (backups, monitoring, webhook URL, CORS allow-list).
8. P2/P3: UX polish (accessibility, toasts), performance sweep (N+1 / duplicate requests), optional features.

Detailed, task-by-task plan: `docs/NEXT_EXECUTION_PLAN.md`.