# Security Remediation — P0-1

**Scope:** make the repository safe to receive newly rotated credentials.
**Date:** 2026-09-27
**Method:** static inspection of the checkout, Git ignore evaluation, pattern scans over source,
docs, tests, migrations, infra and built output. **No secret value was printed, logged, copied or
committed during this task.** Every finding below is reported as *file + line + variable name +
issue type* only.

> **Honesty statement.** Rotation is an action inside the Supabase / Google / Razorpay consoles.
> Nothing in this repository can confirm that a key was rotated. No credential is claimed as
> rotated unless a provider confirmed it, and no such confirmation exists for this task.

---

## 1. Credential categories requiring rotation

Presence was determined by reading **keys only** from the git-ignored root `.env` and recording
`SET` / `empty`. Values were never read into a report.

| ID | Category | Variable (name only) | Presence | Why it is treated as compromised | Rotation authority | Status |
|----|----------|----------------------|----------|----------------------------------|--------------------|--------|
| C-1 | Supabase secret key (`service_role` equivalent, BYPASSRLS) | `SUPABASE_SECRET_KEY` | **SET** | Present in the git-ignored `.env`; the in-file note records that both key values transited a chat window, and the archive was shared for review | Supabase dashboard → Project settings → API keys → rotate | **PENDING (external)** |
| C-2 | Legacy Supabase `service_role` JWT (fallback path) | `SUPABASE_SERVICE_ROLE_KEY` | **SET** | Same as C-1; the legacy JWT carries the project ref and full bypass | Supabase dashboard → API keys → revoke legacy | **PENDING (external)** |
| C-3 | Supabase PostgreSQL password | embedded in `DATABASE_URL` / `DIRECT_DATABASE_URL` | **SET** (password segment) | Present in the git-ignored `.env`; the DB password is not derivable from API keys, so it must be reset separately | Supabase dashboard → Database → reset database password | **PENDING (external)** |
| C-4 | AI provider key (Gemini) | `AI_PROVIDER_API_KEY` | **SET** | Present in the git-ignored `.env` and exposed during development | Provider console (Google AI Studio) → revoke + reissue | **PENDING (external)** |
| C-5 | Supabase publishable / anon key | `SUPABASE_ANON_KEY`, `SUPABASE_PUBLISHABLE_KEY`, `VITE_SUPABASE_ANON_KEY` | SET | **Public by design** — shipped to browsers; authorises nothing on its own (`@supabase/supabase-js` cannot initialise without one) | Supabase dashboard | **OPTIONAL / low priority** |
| C-6 | Razorpay key id, key secret, webhook secret | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | **empty** in the checkout | No value was present in `.env` at review time | Razorpay dashboard (only if keys were configured in Render/Vercel dashboards — outside repo scope) | **NO ACTION IN REPO** |
| C-7 | Email / analytics / error tracking | `RESEND_API_KEY`, `POSTHOG_API_KEY`, `SENTRY_DSN` | **empty** | Absent | — | **NO ACTION** |
| C-8 | Redis connection | `REDIS_URL` | SET (localhost, no credential segment) | Local development URL only | — | **NO ACTION** |
| C-9 | Auth signing / JWT secret | `JWT_SECRET`, `SECRET_KEY` | **absent** from `.env` and code | Identity is Supabase; the API verifies tokens against the project's **public JWKS**, so no shared signing secret exists on the authentication path | — | **NO ACTION** |
| C-10 | Google/Firebase service account | `FIREBASE_SERVICE_ACCOUNT_JSON` (**empty**), `FIREBASE_PROJECT_ID` (SET) | see note | Identity moved to Supabase (SA-09); the `FIREBASE_*` fields no longer exist in `app/core/config.py`. The service-account JSON is empty and a project id is a public identifier, not a credential | — | **NO ROTATION NEEDED — stale keys, recommend deleting from `.env`** |

**Categories requiring external rotation: C-1, C-2, C-3, C-4.** C-5 is optional.

---


## 2. Repository locations checked

| Area | Paths inspected | Result |
|------|-----------------|--------|
| Environment files | `.env`, `.env.example`, `apps/web/.env.local`, `apps/web/.env.example` | Templates placeholder-only (§6); live values only in git-ignored files |
| Backend source | `apps/api/app/**` (core, api/v1, services, repositories, models, integrations, ocr, workers, schemas) | No hardcoded credentials |
| Backend tests | `apps/api/tests/**` incl. `tests/integration/**` | Only **intentional fake fixtures** (§5) |
| Migrations | `apps/api/alembic/versions/**` (+ `alembic.ini`, `env.py`) | No credentials |
| Frontend source | `apps/web/src/**`, `apps/web/vite*.config.ts`, `vitest.config.ts` | Only `VITE_*` reads; no server-env injection (§4) |
| Frontend tests | `apps/web/src/tests/**` | Only **intentional fake fixtures** (§5) |
| Built output | `apps/web/dist/**` (82 files) | 0 secret-pattern hits |
| Infra | `infra/render.yaml` | Secret keys referenced as `sync: false`; no literal secrets |
| CI | `.github/workflows/ci-cd.yml` | One **publishable** key literal for build-time env (public by design); no server secrets |
| Docs | `docs/**` incl. `docs/verification/` (screenshots, RBAC JSON) | No credentials; one publishable key literal redacted (§6) |
| Verification artefacts | `docs/verification/live-rbac.json` (101 entries) | No token / `Authorization` material — scanned for JWT and `Bearer` signatures |
| VCS metadata | `.gitignore`, `apps/web/.gitignore` | See §3 |

---

## 3. Git ignore status

Evaluated with `git check-ignore` after `git init`:

| Path | Ignored | Note |
|------|---------|------|
| `.env` | **YES** | live values — must never be tracked |
| `.env.local`, `.env.production`, `.env.production.local` | **YES** | covered by `.env.*` / `*.local` rules |
| `apps/api/.env` | **YES** | |
| `apps/web/.env`, `apps/web/.env.local` | **YES** | |
| `.env.example`, `apps/web/.env.example` | NO (intentional) | tracked templates, placeholders only |
| `apps/dist-preview/` | **YES** | added during this task (§6) — 320 KB generated bundle |
| `apps/api/.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/` | **YES** | |
| `apps/web/node_modules/`, `apps/web/dist/` | **YES** | |
| `.DS_Store` | **YES** | |

---

## 4. Browser / server exposure analysis

**Browser-exposed (public by design)**

* Resolved by Vite from `VITE_*` only. `vite.config.ts` contains no `define`, `envPrefix`,
  `loadEnv` or `process.env` access, so nothing is injected beyond Vite's default.
* Frontend source reads exactly: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`,
  `VITE_API_BASE_URL`, `VITE_SITE_URL`, `VITE_SUPPORT_EMAIL`, `VITE_APP_ENV`, plus
  `import.meta.env.PROD`. **Zero `process.env` references in `apps/web/src`.**
* `apps/web/.env.local` (git-ignored) contains only four `VITE_*` keys; the checked-in
  `apps/web/.env.example` contains only `VITE_*` keys.
* Built-bundle scan (`apps/web/dist`, 82 files): **0 hits** for `sb_secret_`, JWT payload
  signatures, `rzp_live_`, Google `AIza…` keys and PEM private-key headers.
* Existing guard: `apps/api/tests/test_infra_contract.py` asserts all configured sources name the
  same Supabase project and scans the built output for server-only material.

**Server-only (must never acquire a `VITE_` prefix)**

`SUPABASE_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, `DIRECT_DATABASE_URL`,
`REDIS_URL`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`,
`AI_PROVIDER_API_KEY`, `RESEND_API_KEY`, `POSTHOG_API_KEY`, `SENTRY_DSN`, `STORAGE_BUCKET`,
`CORS_ORIGINS`.

Verified: none of these names appears in any frontend env file, and none is referenced in
`apps/web/src`.

---

## 5. Hardcoded-secret findings

Scanned the whole tree **and** the staged file set for: JWT payload signatures, `sb_secret_*`,
`sb_publishable_*`, `rzp_live_*` / `rzp_test_*`, `sk_live_*` / `sk_test_*`, `AIza…`, `sk-…`,
`AKIA…`, PEM private-key headers, and high-entropy quoted assignments.

| Location | Variable / pattern | Classification | Action |
|----------|--------------------|----------------|--------|
| `.env.example:78`, `.env.example:132` | `SUPABASE_SECRET_KEY` placeholder *shaped like a real key* | False-positive risk for secret scanners | **REMEDIATED** → `REPLACE_WITH_YOUR_SUPABASE_SECRET_KEY` |
| `.env.example:61` | `SUPABASE_URL` = live project URL | Non-secret public identifier, but not a placeholder | **REMEDIATED** → `https://your-project.supabase.co` (must match the frontend template — see §6) |
| `.env.example:60` | comment naming the live project ref | Non-secret, but template hygiene | **REMEDIATED** → generic guidance pointing at `infra/render.yaml` |
| `.env.example:63,85,106,110` | `sb_secret_` inside **comments** documenting key format | Documentation, no value | Retained intentionally |
| `apps/api/tests/test_storage.py:453,480,483,490,492`; `apps/api/tests/test_roles_api.py:50,223` | `sb_secret_…` literals | **Intentional fake fixtures** (assert header routing) | Retained |
| `apps/api/tests/test_billing.py:155`; `apps/api/tests/integration/test_postgres_spec_remainder.py:191,196,205,218,219`; `apps/api/tests/integration/test_postgres_payments.py:564`; `apps/web/src/tests/checkout.test.tsx:100,254`; `apps/web/src/tests/adminConsole.test.tsx:877,891` | `rzp_test_…` literals | **Intentional fake fixtures** | Retained |
| `.github/workflows/ci-cd.yml:214` | `sb_publishable_…` build-time value | Public by design (the browser bundle needs it) | Retained, noted |
| `docs/TEST_BASELINE.md:92` | `sb_publishable_…` inside a documented command | Public by design, but docs should be placeholder-only | **REMEDIATED** → `sb_publishable_<publishable-key>` |
| `.env.example:43`, `.github/workflows/ci-cd.yml:59,148`, `apps/api/app/core/config.py:64`, `apps/api/tests/test_db_urls.py:21`, `apps/docs/api/openapi.json:2846` | `postgresql://<user>:<password>@` carrying the **local development role's password** | Not a secret: a localhost-only convention (§5.1) | Documented; exempted by the scanner **with a written reason**, and only when the host is `localhost`/`127.0.0.1` |

### 5.1 The local development database credential

Found only on the second pass, and only because the live-value check is content-based rather than
pattern-based. The local development PostgreSQL role's password is **also the role name** and is part
of the standard one-command setup, so it appears as the DSN's password segment in the files listed
above. It grants nothing on any host but the developer's own machine:

* the role is created by the local setup path, not by a migration or a deploy;
* the DSNs are `localhost` / `127.0.0.1` only, including in CI, where it addresses a
  service container on the runner itself;
* in the hosted environments the value is injected from the platform's environment store
  (`infra/render.yaml` declares the key with `sync: false`), never from a repository file.

It is **reported and exempted**, not silently dropped: `scripts/check_secrets.py` prints the file and
line it skipped and why, and a DSN whose host is anything other than `localhost`/`127.0.0.1` is
treated as a failure even if the password is identical. Rotating it is a developer-machine concern,
not a security incident; the corresponding hosted credential is tracked separately as **C-3**.

**Real credentials found in tracked files: none.** The only password-shaped string is the local-only
one above, and the only key-shaped strings are the fake fixtures and the public-by-design publishable
key enumerated in this table.

---

## 6. Remediation completed

| # | Change | File | Reason |
|---|--------|------|--------|
| R-1 | Normalised two `SUPABASE_SECRET_KEY` placeholder values to `REPLACE_WITH_YOUR_SUPABASE_SECRET_KEY` | `.env.example` | The old placeholders were shaped like real keys and tripped secret scanners |
| R-2 | Replaced the live project URL with `https://your-project.supabase.co` | `.env.example` | Template must be placeholder-only. The placeholder **must** equal the one in `apps/web/.env.example`, because `test_infra_contract.py::test_the_frontend_and_backend_name_the_same_project` compares the configured sources and ignores only `PLACEHOLDER_HOSTS = {your-project.supabase.co, example.supabase.co}` |
| R-3 | Genericised the comment naming the live project ref | `.env.example` | Template hygiene; the deployed value already lives in `infra/render.yaml` |
| R-4 | Redacted a publishable key literal in a documented command | `docs/TEST_BASELINE.md` | Documentation should be placeholder-only even for public keys |
| R-5 | Added `dist-preview/` to build-output ignores | `.gitignore` | `apps/dist-preview/` (320 KB generated bundle from `npm run preview:static`) was unprotected; `landing-preview.html` was already ignored in `apps/web/.gitignore` |
| R-6 | Verified ignore coverage for every env file variant, cache, build artefact and OS file | `.gitignore`, `apps/web/.gitignore` | §3 |
| R-7 | Repo-wide + staged-file secret scans | — | §5; produced the audit trail used for the first commit |
| R-8 | Re-ran the full backend suite and repaired the regression R-2 introduced | — | The first attempt broke an existing infrastructure guard; see below |
| R-9 | Added a committed, runnable scanner (`HEAD` / `--rev` / `--staged` / `--worktree`, non-zero exit on any unexplained match) | `scripts/check_secrets.py` | Turns the one-off scans of R-7 into a repeatable gate; P0-3 asks for a scanner, not just a scan. Validated by a negative control — a planted real-shaped key **and** a planted remote DSN both failed it, exit 1 |
| R-10 | Extended the live-value harvest to `DATABASE_URL` / `DIRECT_DATABASE_URL` password segments | `scripts/check_secrets.py` | The first pass harvested only `SECRET`/`PASSWORD`/`TOKEN`/`API_KEY`-style keys, so a DSN password was invisible to it — the omission that hid the local-dev credential of §5.1 |

**Change-control note (R-2/R-8).** The initial redaction used `<your-project-ref>.supabase.co`,
which is *not* in `PLACEHOLDER_HOSTS`. The verification gate caught it:
`test_infra_contract.py::TestSupabaseProjectIsConsistentEverywhere::test_the_frontend_and_backend_name_the_same_project`
failed (`1 failed, 996 passed, 2 skipped`). Rather than weaken or edit the test, the placeholder was
aligned with the frontend template's existing placeholder, and the affected test file was re-run to
green (`25 passed, 1 skipped`) before the full suite was repeated. No test was modified, skipped or
deleted to accommodate any change in this task.

---

## 7. Remaining external actions

These cannot be performed from inside the repository and **must not be reported as done**:

1. **C-1** — rotate/regenerate the Supabase secret key; update the Render and local `.env` values.
2. **C-2** — revoke the legacy `service_role` JWT in the Supabase dashboard.
3. **C-3** — reset the Supabase database password; update `DATABASE_URL` / `DIRECT_DATABASE_URL`
   everywhere they are configured (Render dashboard, local `.env`).
4. **C-4** — revoke and reissue the AI provider key; confirm the monthly USD ceiling still applies.
5. **C-5 (optional)** — rotate the publishable key; if rotated, `VITE_SUPABASE_ANON_KEY` must be
   updated in `apps/web/.env.local`, Vercel and `.github/workflows/ci-cd.yml` (build-time value).
6. **Dashboards outside the repo** — if any of the above values were also pasted into Render or
   Vercel environment settings, they must be updated there in the same window as the local `.env`,
   otherwise the API boots with a revoked key (storage signing and Admin API calls will 401/403).
7. **Local hygiene (optional)** — delete the stale `FIREBASE_*` keys from the local `.env`; they are
   read by nothing (`app/core/config.py` no longer defines those fields).
8. **Housekeeping** — `.env.example` contains a duplicate `DIRECT_DATABASE_URL=` assignment
   (lines 44 and 173). Values are identical, so behaviour is unaffected, but the file's own note
   warns that a second assignment silently wins. Left untouched here (unrelated to P0-1);
   reported for a later milestone.

**Nothing in this document should be read as evidence that C-1…C-4 have been rotated.**
