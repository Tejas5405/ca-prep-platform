# Live Integration Report

**Repository:** `ca-prep-platform`
**Commit verified:** `4f11473` — working tree = HEAD + verification docs only (no source changes)
**Date:** 2026-09-27 → 2026-09-28 (IST)
**Environment actually verified:** **LOCAL (development)** app instances pointed at real external
services. There is no staging deployment in this checkout.

---

## 0. Precondition gate — one precondition could not be met

| Gate | State |
|---|---|
| Local quality gates GREEN | **MET** — re-run at this commit (see §2, Phase 0) |
| CI baseline GREEN (local rehearsal) | **MET** — `docs/CI_EXECUTION_REPORT.md` |
| **Remote CI execution verified** | **NOT MET — BLOCKED_EXTERNAL.** This checkout has **no git remote** (`git remote -v` empty), so no remote Actions run exists to verify. Recorded previously: `CI_EXECUTED = NO`. |
| Staging environment accessible | **NOT MET — BLOCKED_EXTERNAL.** No deployed backend/frontend anywhere (no `*.onrender.com` / `*.vercel.app` URL; `infra/render.yaml` is a blueprint; no `apps/web/vercel.json`). |
| Test credentials (non-production) | **MET for Supabase + Gemini**; Razorpay / Resend / PostHog / Sentry / Firebase service account **absent**. |

Because the remote-CI precondition cannot be satisfied from this checkout, every phase that
depends on CI or a deployment is reported `BLOCKED_EXTERNAL`, and nothing is reported as
"deployed green". Everything else was exercised live on local instances against the real
external services for which credentials exist.

---

## 1. What "live" meant here

| Service | Real or local | Evidence |
|---|---|---|
| Supabase project `zyrmlnpvylhcpyaoizyz.supabase.co` | **Real** | JWKS 200 (1 key), admin API 200, storage 200, 3 private buckets |
| Google Generative Language (`gemini-3.8-flash`) | **Real** | models list 200 (50 models); grounded `write_suggestion` returned text |
| PostgreSQL | Local 17 at `localhost:5432` | canonical-chain DB `caprep_rebaseline_proof` (58 tables, at head) |
| Redis + RQ | Local (installed during this verification) | `PONG`; worker booted; job `FINISHED` — `Job OK` |
| Tesseract / PyMuPDF / pdfplumber | Local binaries | digital-tier and OCR-tier extraction both produced text |
| Razorpay | **Not configured** | only graceful degradation exercised (503 naming missing keys) |
| Render / Vercel / CI | **Not deployed** | nothing to smoke-test externally |

---

## 2. Phase results (summary)

| # | Phase | Status | One-line evidence |
|---|---|---|---|
| 0 | Baseline gates (local) | **PASS** | ruff/format/eslint/tsc clean; web 248 passed; backend 998 passed / 1 skipped; secrets scanner CLEAN |
| 1 | Environment & inventory | **PASS** | environment classified LOCAL (development); real vs local services listed above |
| 2 | Authentication (real Supabase) | **PASS** | admin create → password grant → JWKS → 6 authed routes 200, 3 admin routes 403; see F-02 for the macOS TLS caveat |
| 3 | Database / migrations | **PARTIAL** | canonical DB: `alembic current` = head `6c3f7b0a0c13`, `alembic check` = "No new upgrade operations detected"; **default `.env` DB fails** — F-01 |
| 4 | Storage (real Supabase) | **PASS** | upload → sign → signed download (sha256 match) → delete → list empty |
| 5 | Redis + RQ worker | **PASS** | `/health/redis` 200; enqueued job executed: `Job OK` |
| 6 | OCR pipeline | **PASS** | PyMuPDF tier (107 chars) + Tesseract tier (confidence 0.9524, 102 chars, 0.98 s) |
| 7 | Search | **PASS** | `GET /api/v1/search?q=tax` 200 with a real Supabase token |
| 8 | Student smoke | **PASS** | progress / curriculum / search / assistant / notifications / revision all 200 |
| 9 | Admin + RBAC sweep | **PASS** | 107 routes × 6 roles = 642 requests, 0 unhandled 5xx, 0 authorization mismatches |
| 10 | Razorpay (test mode) | **BLOCKED_EXTERNAL** | keys empty; order → 503 naming `RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET`; unsigned webhook → 401; subscription → 200 |
| 11 | AI provider | **PASS (caveat)** | live grounded suggestion returned; provider intermittently 503 "high demand" — F-05 |
| 12 | Deployment smoke | **BLOCKED_EXTERNAL** | nothing deployed; local frontend build + preview smoke PASS instead |
| 13 | Observability / logging | **PARTIAL** | structured JSON logs + `X-Request-Id` on normal responses; **missing on 500s** — F-03 |

---

## 3. Phase detail

### Phase 0 — Baseline gates (local, at `4f11473`)
Re-run rather than cited:
- `ruff check .` clean; `ruff format --check .` — 148 files clean.
- `eslint .` exit 0; `tsc -b --noEmit` clean; web suite **248 passed** (19 files, 7.5 s);
  `npm run build` OK (~1.2 s); `vite preview` served index **200**, SPA route **200**, hashed asset **200**.
- Backend suite (Redis absent — the environment the recorded baseline assumes):
  **999 collected, 998 passed, 1 skipped, 0 failed** (`tests/test_infra_contract.py:288` skip is the
  documented "no Supabase database URL in this checkout" guard).
- `python3 scripts/check_secrets.py --worktree` → **CLEAN** (0 unexpected matches).

### Phase 1 — Environment & inventory
- `.env`: `ENVIRONMENT=development`, `DEBUG=true`; web `.env.local`: `VITE_APP_ENV=development`.
- Real keys present: Supabase URL + publishable/secret/service-role, AI provider key.
- Absent: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RESEND_API_KEY`,
  `POSTHOG_API_KEY`, `SENTRY_DSN`, `FIREBASE_SERVICE_ACCOUNT_JSON` — and no application code
  references the last four, so they are unused configuration rather than broken integrations.
- No git remote; no Docker daemon; port 8000 is occupied by an unrelated checkout's dev server
  (see note N-1).

### Phase 2 — Authentication (real Supabase)
Performed against the real project using the service/anon keys (no value is reproduced here):
1. Admin API `GET /auth/v1/admin/users` → 200; `POST /auth/v1/admin/users`
   (`email_confirm: true`, synthetic `live-verify+student@example.test`) → 200.
2. Password grant `POST /auth/v1/token?grant_type=password` → 200 (real access token).
3. Project JWKS `GET /auth/v1/.well-known/jwks.json` → 200, 1 key (ES256/P-256).
4. API instance with real `SUPABASE_URL` accepted the token: `progress/overview`,
   `curriculum/subjects`, `search?q=tax`, `assistant/status`, `notifications/unread-count`,
   `revision/due` → **200 each**; first-request provisioning created the `users` row
   (verified by read-only query).
5. Negative checks: `admin/dashboard`, `admin/audit`, `admin/me/permissions` for that STUDENT
   token → **403 each**.
Caveat (F-02): on this macOS python.org build the JWKS fetch fails TLS verification
(`CERTIFICATE_VERIFY_FAILED`) until `SSL_CERT_FILE` points at a CA bundle; the API then answers
**401 "Invalid authentication token"** — misleading, because the token was valid.

### Phase 3 — Database / migrations
- `alembic heads` → single head `6c3f7b0a0c13`.
- Against the canonical-chain DB (`caprep_rebaseline_proof`, 58 tables):
  `alembic current` → `6c3f7b0a0c13 (head)`; `alembic check` → "No new upgrade operations detected."
- Against the default `.env` DB (`caprep`): `alembic current` **FAILS** —
  `Can't locate revision identified by '0015_syllabus_classification'` (see F-01). No migration
  was applied, stamped or edited anywhere in this milestone.

### Phase 4 — Storage (real Supabase)
Read-only checks plus one probe object in `user-uploads` (all three buckets are private):
list buckets → 200 (`question-media`, `user-uploads`, `question-pdfs`); probe lifecycle —
`POST object` 200 → `POST object/sign` 200 → signed URL GET 200 with **byte-exact sha256 match**
→ `DELETE` 200 → list empty. API-side: `/health/storage` → 200 (`bucket: question-pdfs` on the
instance pointed at the real project).

### Phase 5 — Redis + RQ
Redis was not installed on this machine (no `redis-server` binary, Docker daemon down); it was
installed via Homebrew for this phase (note N-3) and started on `127.0.0.1:6379`.
- `PING` → `PONG`; `/health/redis` → 200 `{"redis":{"status":"up"}}`.
- `python -m app.workers.rq_worker` booted: *"RQ worker starting on queues: ['ingestion','default']"*,
  *"*** Listening on ingestion, default..."*.
- A real job was enqueued (`send_notification_job`, synthetic payload) and the worker logged
  *"default: app.workers.rq_worker.send_notification_job(...)"* → *"Job OK"*; job status
  `FINISHED`; both queues returned to count 0.
- The worker and Redis were stopped after the phase; the Homebrew package remains installed.

### Phase 6 — OCR pipeline
Through `app.ocr.extractor` on a synthetic PDF:
- digital tier: `extract_pdf` → tier `PYMUPDF`, 107 chars, text found (`GSTR-3B`).
- raster tier: page rendered at 200 dpi → `ocr_page` → tier `TESSERACT`, confidence **0.9524**,
  102 chars, 0.98 s, required tokens found.

### Phase 7–9 — Search, student smoke, admin/RBAC
- Search: `GET /api/v1/search?q=tax` → 200 (real token; also 200 across roles in the sweep).
- Student: the six routes in Phase 2 plus RBAC-matrix entries; every student-reachable route
  answered per the permission matrix.
- RBAC sweep regenerated: `docs/verification/live-rbac-matrix.md` / `.json` —
  **107 routes × 6 roles = 642 requests, 0 unhandled 5xx, 0 authorization mismatches**, and
  2 storage-backed routes that report their dependency as unavailable (harness artifact, note N-2;
  real storage verified separately in Phase 4). The matrix grew from the previously committed 101
  routes because it is generated from the current code, which now includes the admin/payments routes.

### Phase 10 — Razorpay test mode (BLOCKED_EXTERNAL)
All three `RAZORPAY_*` variables are **empty**; no key was invented and no payment was attempted.
What the API does in that state (verified with a real student token):
- `POST /api/v1/payments/order {"plan_code": "PREMIUM"}` → **503** in the documented problem shape,
  naming exactly what is missing: *"Missing RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET … enter them once
  under Admin → Payments"*.
- `GET /api/v1/payments/subscription` → 200 (FREE tier + entitlements).
- `POST /api/v1/webhooks/razorpay` unsigned → **401 "Invalid signature"**.
Real test-mode checkout, webhook delivery and signature acceptance remain unverified until keys
exist — as `BLOCKED_EXTERNAL`, not as pass.

### Phase 11 — AI provider (live)
- Key is valid: `GET /v1beta/models` → 200 with 50 models; the configured `gemini-3.8-flash` is present.
- The app's own `write_suggestion` returned a grounded answer (154 chars, "Study suggestion: …")
  for a synthetic GST question, so the full path (prompt build → provider call → cleanup) works live.
- Provider instability observed: direct probes to `gemini-3.8-flash` returned 503
  ("This model is currently experiencing high demand") on roughly one of three attempts. The app
  degrades by design: `write_suggestion` returns `None` and the assistant keeps quotations without
  inventing an answer. Finding F-05 notes the fallback model is identical to the configured
  default, so the fallback path cannot change anything.

### Phase 12 — Deployment smoke (BLOCKED_EXTERNAL)
- No git remote → no CI run, no CD; no Render/Vercel deployment exists to smoke-test.
- Local substitutes, all green: production build, `vite preview` (index/SPA/asset 200), and a
  bundle scan — service-role key **absent**, publishable/anon key present by design.
- `infra/render.yaml` remains a blueprint; `apps/web/vercel.json` does not exist, so the baseline's
  earlier "SPA routing via vercel.json rewrites" claim was corrected.

### Phase 13 — Observability / logging (PARTIAL)
- Structured JSON logs are emitted for startup, access and errors, and `X-Request-Id` is returned
  on success/401/403 responses and echoes a client-supplied id (verified: sent `liveprobe-123`,
  received it back).
- **Defect (F-03):** on an unhandled 500 the response has **no** `X-Request-Id` header even though
  the body says "The request id is in the response headers", and the `app.error` log line carries
  `request_id: "-"`. The access-log line also always logs `request_id: "-"` (contextvar reset
  happens before the log call), so sweep access logs cannot be correlated with response ids.
- Sentry / PostHog / Resend / Firebase: no DSN or key, and no code references — `NOT_TESTED`
  (not wired), not failed.

---

## 4. Findings

Severity scale: P0 blocks everything; P1 serious (blocks a normal developer path); P2 moderate;
P3 minor/observability. **No fix was applied** — the milestone forbids silent fixes; each entry
carries a proposed fix for approval.

### F-01 — P1 — Default `.env` database is on a foreign, off-chain migration stamp
- **Repro:** `cd apps/api && python3 -m alembic current` (repo's `.env` `DATABASE_URL` → `caprep`)
  → `ERROR Can't locate revision identified by '0015_syllabus_classification'`. Then start the API
  with the default env and `curl /api/v1/payments/plans` → **500**
  (`UndefinedTable: relation "platform_settings" does not exist`).
- **Evidence:** `alembic heads` = `6c3f7b0a0c13`; `caprep` has 40 tables and stamp
  `0015_syllabus_classification`; that revision file exists **only** in the sibling checkout
  `CA Version 1/ca-prep-platform/apps/api/alembic/versions/`. Canonical DB
  (`caprep_rebaseline_proof`) has 58 tables and is at head.
- **Cause:** the shared local database was advanced/stamped by a different checkout's Alembic
  chain. This extends the divergence documented in `docs/DB_REBASELINE.md` (whose record shows the
  stamp moved from `0014_…` to `0015_…` since it was written).
- **Recommended fix (not applied — schema changes are out of scope):** point `DATABASE_URL` at a
  canonical-chain database (what this verification did via env override), and rebuild the divergent
  DB per `docs/DB_REBASELINE.md` §3 (archive dump → new DB on the canonical chain → repoint).
  Never `alembic stamp` it.

### F-02 — P2 — Real-Supabase JWKS fetch fails on a stock macOS python.org install; the API answers 401 as if the token were bad
- **Repro:** with the real `SUPABASE_URL`, send a freshly granted token to `/api/v1/progress/overview`
  → `401 {"detail":"Invalid authentication token"}`; the server log shows
  `Supabase token verification failed: Fail to fetch data from the url, err: "<urlopen error
  [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed…>"`. Starting the same process with
  `SSL_CERT_FILE=<certifi bundle>` makes the same token return 200 on all six routes.
- **Cause:** `PyJWKClient` fetches over `urllib` with the interpreter's default SSL context;
  python.org macOS builds ship without a CA bundle until `Install Certificates.command` is run
  (`httpx` works because it uses certifi).
- **Recommended fix (not applied):** document the CA prerequisite for local development (or pass a
  certifi-backed SSL context into the JWKS client), and consider surfacing a failed JWKS *fetch*
  as 503 "auth dependency unavailable" instead of 401 "invalid token" — during a Supabase/TLS
  outage every user would otherwise be told their credentials are wrong.

### F-03 — P3 — `X-Request-Id` is missing on 500 responses; error/access log lines lose the id
- **Repro:** `curl -D - -H 'X-Request-Id: probe500-abc' http://127.0.0.1:8012/api/v1/payments/plans`
  → 500 headers contain **no** `x-request-id`; `grep probe500-abc` in the server log → 0 lines; the
  `app.error` "Unhandled error" line records `request_id: "-"`. Success/401/403 responses do carry
  the header.
- **Cause:** the catch-all `Exception` handler runs in Starlette's `ServerErrorMiddleware`
  (outside the request-context middleware), and the middleware resets the contextvar before its
  own access-log call.
- **Recommended fix (not applied):** set the header and log the id from `request.state.request_id`
  inside the exception handler; move the access-log call before the contextvar reset.

### F-04 — P3 — Two backend tests assume the machine has no Redis and fail when one is reachable
- **Repro:** with Redis running on 127.0.0.1:6379, full suite → 999 collected, 996 passed,
  **2 failed**, 1 skipped (80.8 s). With Redis stopped → **998 passed, 0 failed**, 1 skipped.
  - `tests/test_api_contract.py::TestHealthContract::test_health_redis_returns_503_when_unreachable`
    monkeypatches `app.core.dependencies.get_redis_client`, but `app/api/v1/health.py` binds that
    name at import time, so the patch has no effect; with a live Redis the real client answers 200
    and the `assert 503` fails.
  - `tests/test_schema_contract.py::TestSchemaSize::test_the_upload_schema_rejects_an_unknown_kind_as_a_422`
    passes alone; it fails only after the test above has run with Redis up (the cached real Redis
    client is bound to that test's closed event loop; the next `TestClient` shutdown raises
    `RuntimeError: Event loop is closed`).
- **Impact:** CI is unaffected today (no Redis service there), but any developer running Redis
  locally — or a future CI that adds one — sees two red tests that do not indicate product defects.
- **Recommended fix (not applied):** patch `app.api.v1.health.get_redis_client` (or resolve it
  lazily inside the route), and make the cached async client loop-aware/reset between `TestClient`s.

### F-05 — P3 — The AI "fallback" model is identical to the configured default
- **Evidence:** `app/services/gemini.py` sets `_FALLBACK_MODEL = "gemini-3.8-flash"`, which is also
  the configured `AI_PROVIDER_MODEL` here; the retry branch (`model != _FALLBACK_MODEL`) therefore
  never triggers. The provider returned intermittent 503 "high demand" during probes, degrading to
  a quotations-only answer by design.
- **Recommended fix (not applied):** choose a genuinely different fallback model and/or add bounded
  retry with backoff so a transient provider 503 does not silently remove generated suggestions.

---

## 5. State changes and cleanup (full disclosure)

| Action | Detail | Left behind? |
|---|---|---|
| Redis installed | `brew install redis` (machine had none; Docker daemon unavailable) | Package installed; server **stopped** after Phase 5 |
| Local API instances | ports 8010 / 8011 / 8012, JWKS harness 54321 | **All stopped** after evidence was captured |
| RQ worker | one worker process | **Stopped** |
| Supabase test user | `live-verify+student@example.test` created with a random password (never printed or stored) | Account remains (synthetic; password resettable via admin API) |
| Local RBAC fixtures | 6 `live-verify+<role>@example.test` rows find-or-inserted by `scripts/live_verify.py` | Remain in `caprep_rebaseline_proof` (synthetic, by design) |
| Storage probe object | `user-uploads/live-verify/probe.txt` | **Deleted**; bucket list verified empty afterwards |
| Databases | read-only checks plus synthetic test rows; **no migration applied, stamped or edited** | No schema change anywhere |
| Source code | **No code change in this milestone**; findings are reported, not fixed | Only docs committed |

### Notes
- **N-1 — Foreign server on port 8000.** The process answering `:8000` belongs to another checkout
  (`.../CA Version 1/ca-prep-platform/apps/api`, logged to its own `api.log`) and currently returns
  500 for every request (`ModuleNotFoundError: No module named 'anyio._backends'` in that venv).
  It is not this repository; it was left untouched, and all verification used isolated ports.
  Any tooling that defaults to `:8000` (e.g. `live_verify.py` without `--api`) would measure that
  server — always pass `--api` explicitly.
- **N-2 — Storage 503s inside the RBAC sweep are a topology artifact.** The sweep instance points
  `SUPABASE_URL` at the local JWKS harness (needed to mint role tokens), so storage sign calls hit
  the harness and answer 501 → the API correctly reports 503 "Storage unavailable". Real storage was
  verified separately (Phase 4, full round-trip against the real project).
- **N-3 — Redis was absent, then added.** Baseline environments (CI, and this machine before today)
  have no Redis; `docs/CURRENT_SYSTEM_AUDIT.md` §9 recorded that `test_queue_boundary.py` ran only
  against `fakeredis`, with no real RQ job ever observed. This milestone is the first time real
  Redis + a real enqueued job were exercised.
- **N-4 — Environment correction.** `LIVE_INFRASTRUCTURE_BASELINE.md` previously asserted
  "Environment: STAGING" and a `vercel.json` SPA rewrite; both were template claims. Corrected to
  the verified LOCAL environment and the actual deployment state.

---

## 6. Explicitly NOT tested (and why)

| Area | Status | Reason |
|---|---|---|
| Remote CI execution (Actions) | `BLOCKED_EXTERNAL` | no git remote in this checkout |
| Render / Vercel deployment smoke | `BLOCKED_EXTERNAL` | nothing deployed; blueprint only |
| Razorpay test-mode checkout, webhook delivery, signature acceptance | `BLOCKED_EXTERNAL` | no `RAZORPAY_*` keys supplied; real charges prohibited by the milestone |
| Google OAuth sign-in on the Supabase project | `NOT_TESTED` | not enabled on the project; password grant path was verified instead |
| Sentry / PostHog / Resend / Firebase | `NOT_TESTED` | no key/DSN and **no code references** — integrations are not wired |
| Postgres RLS/pooler behaviour (Supabase pooled connections) | `NOT_TESTED` | verification used local Postgres; no Supabase DB URL in the checkout |
| Load/performance, concurrent webhook storms | `NOT_TESTED` | out of scope for a correctness verification |

---

## STOP CONDITION

This milestone stops here. Deliverables: this report and
`docs/LIVE_INFRASTRUCTURE_BASELINE.md`, plus regenerated `docs/verification/live-rbac-matrix.md`
and `.json`. No feature work, no schema change, no fix was applied; the five findings above await
approval. The working tree contains only verification documentation (plus a pre-existing
mode-only change to `scripts/dev-stack.sh`), and a secret scan is clean.

