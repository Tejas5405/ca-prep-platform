# Next Execution Plan

Prioritised tasks from the audit of 27 September 2026. Priorities:
**P0** = security / data-loss / authorization / broken core functionality;
**P1** = major user-flow or gate defects;
**P2** = UX / performance / maintainability;
**P3** = optional features / enhancements.

Each task states the defect, the evidence, the files it touches, the smallest
proposed fix, the regression test, the verification command, dependencies, and
status. Nothing here has been implemented — this is the plan, not the work.

---

## P0 — Security, data integrity, authorization

### P0-1 Rotate exposed credentials (Supabase secret/service_role, Gemini key)
- **Problem:** the archived `ca-prep-platform/.env` contains real values for
  `SUPABASE_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`,
  `AI_PROVIDER_API_KEY`; the project's own comments say they transited a chat.
  Anyone with the ZIP has service-role access to the Supabase project and a
  billing-backed Gemini key.
- **Evidence:** `docs/SECURITY_AUDIT.md` §1/§6; `.env` structural scan (values
  never printed).
- **Files affected:** none (operator action in the Supabase dashboard + Google AI
  Studio). After rotation, set the new values only in the deployment platform.
- **Proposed solution:** rotate each key; delete/ignore the old `.env`; ensure
  future reviewer handouts exclude env files.
- **Test required:** after rotation, confirm the *old* key is rejected (storage
  401) and the new one works; re-run `test_infra_contract.py`.
- **Verification:** `python -m pytest tests/test_storage.py tests/test_infra_contract.py -q`
- **Dependencies:** owner access to Supabase + Google AI Studio.
- **Status:** NOT STARTED (owner action).

### P0-2 Fix the workspace database drift (missing migration `0014_drop_duplicate_queue_index`)
- **Problem:** both dev databases are stamped at a revision whose migration file
  is absent from the archive; `alembic current/upgrade/downgrade` fail on them.
- **Evidence:** `alembic current` → `Can't locate revision identified by
  '0014_drop_duplicate_queue_index'`; on-disk chain is otherwise clean (fresh DB
  upgrade/downgrade/upgrade passed).
- **Files affected:** none required (operator re-creates the dev DBs), or
  `apps/api/alembic/versions/` only if the chain is intentionally re-based.
- **Proposed solution (smallest):** re-baseline the two stale dev DBs by
  re-creating them from the on-disk chain (`alembic upgrade head`). Do NOT
  invent the missing migration's content.
- **Test required:** afterwards `alembic current` on both DBs shows
  `6c3f7b0a0c13`; full pytest green.
- **Verification:** `alembic current && alembic check`
- **Dependencies:** access to the local Postgres; confirm no live data sits only
  in those dev DBs before dropping.
- **Status:** NOT STARTED.

### P0-3 Add a secrets scanner before the first commit
- **Problem:** no git repository exists; the first `git init`/`git add` could
  accidentally stage `.env`/`.env.local` or any chat-pasted key.
- **Evidence:** `SECURITY_AUDIT.md` §3.
- **Files affected:** `.gitignore` (already correct — keep), optional
  `.pre-commit-config.yaml` / CI secret-scan step (gitleaks/trufflehog).
- **Proposed solution:** `git init`; commit `.gitignore` first; `git add`
  progressively with a secret scan; add a CI secret-scan job.
- **Test required:** scan the first commit — zero secret hits; assert `.env` is
  not in `git ls-files`.
- **Verification:** `git ls-files | grep -c '^\.env'` → 0; scanner exit 0.
- **Dependencies:** none.
- **Status:** NOT STARTED.

### P0-4 Re-verify RBAC and data isolation against the real Supabase project
- **Problem:** the RBAC matrix was last generated against a live stack and is
  recorded in `docs/verification/`; the API has since grown (153 paths) and this
  workspace has no `SUPABASE_URL`, so nothing live was re-verified here.
- **Evidence:** `docs/CURRENT_SYSTEM_AUDIT.md` §5/§6; `live-rbac-matrix.md`
  ("routes checked: 101" vs 153 now).
- **Files affected:** none (verification only) — `scripts/live_verify.py`.
- **Proposed solution:** with rotated credentials in place, run `live_verify.py`
  and regenerate the matrix; fix any 403/200 mismatch found.
- **Test required:** 0 unhandled 5xx, 0 authorization mismatches across all 153
  routes × roles.
- **Verification:** `python3 scripts/live_verify.py ...` (see script usage).
- **Dependencies:** P0-1, a reachable API + Supabase project.
- **Status:** BLOCKED on P0-1.

---

## P1 — Major defects / gates / user flows

### P1-1 Make ruff gates green (29 errors, 5 unformatted files)
- **Problem:** `ruff check app tests alembic` → 29 errors (20× E501 in
  `app/api/v1/studio.py`, UP007/UP035 in the newest migration, I001 import sort,
  C420 in `platform_defaults.py`); `ruff format --check` → 5 files. CI fails.
- **Evidence:** `docs/TEST_BASELINE.md` §3–§4.
- **Files affected:** `app/api/v1/studio.py`, `app/services/platform_defaults.py`,
  `app/main.py`, `tests/integration/test_postgres_studio.py`,
  `alembic/versions/20260926_2002_6c3f7b0a0c13_campus_tools_and_review_state.py`,
  `app/models/user.py`, `app/services/question_history.py`.
- **Proposed solution:** `ruff format` the 5 files; wrap the E501 lines
  (studio.py holds 17 of them); apply UP007/UP035 + I001 to the new migration
  (align with the other migrations' template style); replace the C420
  dict-comprehension.
- **Test required:** full pytest still green after the format pass.
- **Verification:** `ruff check app tests alembic && ruff format --check app tests alembic`
- **Dependencies:** none.
- **Status:** NOT STARTED.

### P1-2 Make CI real and run it once to green
- **Problem:** `.github/workflows/ci-cd.yml` has never executed (not a git repo).
- **Evidence:** audit §14; `git rev-parse` fails in the workspace.
- **Files affected:** repo bootstrap (`.git`, remote), no code change expected.
- **Proposed solution:** `git init`, first commit (secrets-safe per P0-3), push
  to GitHub, let CI run; fix whatever it surfaces until the pipeline is green.
- **Test required:** CI green on `main` (api job incl. `alembic check`,
  reversibility job, web job).
- **Verification:** GitHub Actions run status.
- **Dependencies:** P0-3, P1-1, P2-1, P1-5 (gates must pass first).
- **Status:** NOT STARTED.

### P1-3 Complete the API↔frontend integration sweep (student + admin journeys)
- **Problem:** no recorded evidence that the student/admin journeys work through
  the whole stack (React fetch wiring → API → Postgres → UI). Docs say so.
- **Evidence:** `docs/CURRENT_SYSTEM_AUDIT.md` §12/§13; `API_CONTRACT_AUDIT.md`
  discrepancy #2.
- **Files affected:** none to start (add a `docs/verification/` artifact; fix
  defects as they surface).
- **Proposed solution:** run the 12 student flows + admin flows against a fully
  local stack (or deployed stack), recording success/loading/empty/validation/
  authz/server-error/refresh/back/direct-URL for each; file defects as found.
- **Test required:** per-flow evidence; new Vitest/pytest regression for every
  defect found.
- **Verification:** recorded matrix + `npm test` / `pytest` green.
- **Dependencies:** P0-1/P0-4 for auth round-trip; local Redis for job flows.
- **Status:** NOT STARTED.

### P1-4 Add a cross-layer contract test (page wiring ↔ real API)
- **Problem:** endpoint and page contracts are tested separately; a
  payload-shape drift between them is only caught manually.
- **Evidence:** `API_CONTRACT_AUDIT.md` §Discrepancies #2.
- **Files affected:** new test (e.g. `apps/api/tests/integration/` driving the
  client `api.ts` against TestClient, or a Vitest test hitting a live local API).
- **Proposed solution:** one integration test that boots the FastAPI app and
  exercises the exact fetch layer the pages use for a core journey
  (login-less: pricing catalogue; authed: practice questions → answer).
- **Test required:** the new cross-layer test passes.
- **Verification:** `python -m pytest tests/integration -q` + `npm test`.
- **Dependencies:** local DB (present).
- **Status:** NOT STARTED.

### P1-5 Isolate and fix the vitest flake
- **Problem:** `npm test` fails 10 × in run 1, 2 × in run 2 (different sets);
  each test passes isolated. Non-deterministic CI results undermine the gate.
- **Evidence:** `docs/TEST_BASELINE.md` §10.
- **Files affected:** to be determined (`landing.test.tsx`,
  `publicPages.test.tsx`, `login.test.tsx`, `libraryPages.test.tsx`,
  `adminConsole.test.tsx`, `adminAndQuestionDetail.test.tsx`).
- **Proposed solution:** reproduce with `--maxWorkers=1` vs default to see if
  it is load or inter-file state (shared `fetch` stub / fixed timers / reused
  Supabase mock); fix isolation (fresh stub per file, `vi.resetModules` where
  needed).
- **Test required:** 3 consecutive full `npm test` runs green.
- **Verification:** `npm test` ×3.
- **Dependencies:** none.
- **Status:** NOT STARTED.

### P1-6 Add Vercel configuration (SPA routing)
- **Problem:** no `vercel.json`; deep links (`/admin/*`, `/practice`, `/learn`,
  `/auth/callback`) would 404 on a fresh Vercel deploy.
- **Evidence:** `PRODUCTION_READINESS.md` B-1.
- **Files affected:** new `apps/web/vercel.json` (rewrites to `index.html`,
  output `dist`, build `npm run build`, Node 20).
- **Proposed solution:** framework-agnostic `vercel.json` in `apps/web` with a
  catch-all rewrite; document the `VITE_*` env in `.env.example`.
- **Test required:** a Vercel preview deploy serves `/practice` and
  `/auth/callback` (200, SPA).
- **Verification:** `npx vercel --prod` smoke on preview URLs.
- **Dependencies:** Vercel account/linkage.
- **Status:** NOT STARTED.

### P1-7 Declare `greenlet` explicitly in the backend dev requirements
- **Problem:** a fresh `requirements-dev.txt` install is missing `greenlet`, so
  the async suite fails with a confusing error on a clean machine (37F/222E in
  the first run here).
- **Evidence:** `docs/TEST_BASELINE.md` §2.
- **Files affected:** `apps/api/requirements-dev.txt` (add `greenlet` pinned to
  what SQLAlchemy 2.0.54 resolves against).
- **Proposed solution:** add the pin, re-run the full suite from scratch in a
  fresh venv.
- **Test required:** fresh venv → clean install → pytest green.
- **Verification:** as in `TEST_BASELINE.md` golden commands.
- **Dependencies:** none.
- **Status:** NOT STARTED.

### P1-8 Verify the ingestion→OCR→review→publish pipeline end-to-end
- **Problem:** zero observed runs of the content pipeline against a live worker
  and storage (the platform's core scaling path).
- **Evidence:** `CURRENT_SYSTEM_AUDIT.md` §8/§9/§13; `docs/REMAINDER_STATUS.md`.
- **Files affected:** none to start; fix defects as found (worker, extractor,
  storage).
- **Proposed solution:** with Redis + a configured Supabase, drive one digital
  PDF and one scanned PDF through upload → job → extraction → draft → QA →
  approve → publish; verify a FAILED job is terminal-and-retryable and that an
  unpublished draft is invisible to students.
- **Test required:** recorded run; extend `test_postgres_ingestion_queue.py` for
  anything it doesn't already cover.
- **Verification:** `python -m pytest tests/integration/test_postgres_ingestion_queue.py -q`
  plus the manual run.
- **Dependencies:** P0-1, live Redis.
- **Status:** BLOCKED.

---

## P2 — UX / performance / maintainability

### P2-1 Fix the deterministic checkout 503-copy test failure
- **Problem:** `checkout.test.tsx` "names the missing environment variables..."
  cannot pass: the page only shows the fixed "cannot take payments yet" copy when
  the 503 problem has no `detail`, but the API always supplies one.
- **Evidence:** `TEST_BASELINE.md` §10; `API_CONTRACT_AUDIT.md` discrepancy #1.
- **Files affected:** `apps/web/src/pages/Upgrade.tsx` (render fixed copy +
  server detail, e.g. "This deployment cannot take payments yet: …"), or the
  test if the copy decision changes deliberately.
- **Proposed solution:** always render the operator-facing sentence and append
  the server detail; keep the env-var list rendering unchanged.
- **Test required:** the existing test passes plus a new assertion that the
  server detail appears too.
- **Verification:** `npx vitest run src/tests/checkout.test.tsx`
- **Dependencies:** none.
- **Status:** NOT STARTED.

### P2-2 Renew the stale DELIVERY_REPORT
- **Problem:** `docs/DELIVERY_REPORT.md` claims 88 paths / 7 migrations / 981
  tests vs 153 / 15 / 995 now; the next reader will be misled.
- **Evidence:** `CURRENT_SYSTEM_AUDIT.md` §1 discrepancy.
- **Files affected:** `docs/DELIVERY_REPORT.md` (revisions §3–§7) or a pointer
  to this audit set.
- **Proposed solution:** update the headline numbers and the "not built" list to
  match the audit output; delete nothing.
- **Test required:** n/a (docs).
- **Verification:** manual read-through.
- **Dependencies:** the audit docs (this set).
- **Status:** NOT STARTED.

### P2-3 Focused UX/accessibility pass on the highest-traffic screens
- **Problem:** no a11y/keyboard-audit artifact exists; pages have loading/empty/
  error states but focus management, aria labels, and mobile nav are unverified.
- **Evidence:** `CURRENT_SYSTEM_AUDIT.md` §12 (states present, a11y unproven).
- **Files affected:** targeted components on Landing, Login, Practice, Mocks,
  Dashboard, Admin shell.
- **Proposed solution:** keyboard-nav + screen-reader pass on those screens; fix
  measurable issues only (no redesign).
- **Test required:** new a11y assertions where practical.
- **Verification:** `npm test` + manual keyboard pass.
- **Dependencies:** P1-5 (stable test gate first).
- **Status:** NOT STARTED.

### P2-4 Performance sweep (N+1 / duplicate requests / bundle / pool sizing)
- **Problem:** no load evidence; `--workers 2` with async pool size 5 unverified;
  practice/search queries unbounded at scale by admission.
- **Evidence:** `PRODUCTION_READINESS.md` (pool sizing), `CURRENT_SYSTEM_AUDIT.md` §11.
- **Files affected:** query hotspots found by the sweep; possible index/migration
  additions.
- **Proposed solution:** profile `/practice/questions`, `/search`,
  `/mocks/{id}/attempts`; fix measurable N+1s; add pagination/index where
  needed; adjust worker/pool sizing with load numbers.
- **Test required:** benchmark before/after; pytest green.
- **Verification:** `EXPLAIN ANALYZE` + request-time measure before/after.
- **Dependencies:** P1-3.
- **Status:** NOT STARTED.

---

## P3 — Optional / enhancements

- **P3-1** Google OAuth enablement + branded email templates (Resend) — needs the
  provider accounts; `BLOCKED_EXTERNAL` today.
- **P3-2** Scheduled job runner for question re-verification (law notices,
  historical flags) — currently operator-button-driven by design; wire a cron
  only when the blueprint's trigger condition is met.
- **P3-3** PWA install polish (full icon set) and service-worker cache policy for
  static assets only.
- **P3-4** Browser E2E suite (Playwright) for the 12 student flows once the
  Vitest flake is fixed — the automation that makes the integration sweep
  repeatable.
- **P3-5** `mypy --strict` adoption module-by-module (already advisory in CI).
- **P3-6** Analytics instrumentation (`POST /events`) behind an explicit privacy
  decision (currently deferred).

---

## Execution order (recommended)

```
P0-1 → P0-2 → P0-3          # security + data repair + history hygiene
P1-7 → P1-1 → P2-1 → P1-5   # make the gates trustworthy
P1-2                        # CI green end-to-end
P0-4 → P1-8 → P1-3          # live verification (needs the platform accounts)
P1-4 → P1-6 → P2-2 → P2-3 → P2-4   # contracts, deploy config, polish
P3-*   when the trigger conditions are met
```

Every task above obeys the change-control rule: explain → identify files →
smallest fix → regression test → run relevant tests → broader tests → update
docs only after verification. No task in this plan rewrites working code,
changes the architecture, or migrates technology.

## Audit stop point

This is the end of the audit phase. Per the master instruction, implementation
begins only after this plan has been presented and the milestone boundaries for
the first P0 task (or a P0→P1 sequence) have been agreed. No code was modified
during the audit.