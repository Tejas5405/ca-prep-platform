# Staging Deployment Status

**Milestone:** P2A — prepare for an isolated staging environment
**Prior milestone:** P2 (blocked at Phase 0) — recorded in §A below
**Baseline commit:** `51df3e0` → P2A
**Status:**

```
STAGING_READY_FOR_DEPLOYMENT = NO   ← STAGING_BLOCKED_EXTERNAL
```

**Nothing has been deployed.** P2A completed the configuration and audit work
that does not require infrastructure. The blockers in §A are unchanged, and no
code change has made any of them go away — because none of them can be.

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

# §A — P2 (blocked at Phase 0): the pre-flight findings

Everything in this section was established by the P2 pre-flight and is
**unchanged** by P2A. It is retained because it is the evidence for
`STAGING_BLOCKED_EXTERNAL`.

## A.1 What does not exist

No staging resource of any kind existed. Verified, not assumed:

| # | Component | Finding |
|---|---|---|
| 1 | **Frontend** | No `vercel.json`, no SPA rewrite, **no deployed URL** (DG-1, DG-6) |
| 2 | **Backend** | `render.yaml` is a blueprint only; sets `ENVIRONMENT: production` literally (DG-8) |
| 3 | **Worker** | Depends on Redis, which is commented out of the blueprint (DG-4) |
| 4 | **Database** | **Local** Postgres only (DG-2) |
| 5 | **Auth + Storage** | **One real shared Supabase project** (DG-3) |
| 6 | **Redis** | **Local** only (DG-4) |
| 7 | **Payments** | All three `RAZORPAY_*` are **EMPTY** |
| 8 | **Observability** | No error tracker or uptime monitor (DG-5) |

No `STAGING_*` variable existed anywhere in the repository.

## A.2 Credential state at P2 (local dev `.env`, values never printed)

`DATABASE_URL` → `localhost:5432` (local) · `REDIS_URL` → local · `SUPABASE_URL`
→ real shared project · `SUPABASE_SECRET_KEY` populated · `AI_PROVIDER_API_KEY`
populated · **`RAZORPAY_KEY_ID` / `_SECRET` / `_WEBHOOK_SECRET` all EMPTY** ·
`RESEND_API_KEY` empty.

---

# §B — P2A results

## B.1 Deliverables

| File | Status |
|---|---|
| `docs/STAGING_ENVIRONMENT_REFERENCE.md` | ✅ created — every variable, classified, read from the code |
| `docs/STAGING_DEPLOYMENT_INPUTS.md` | ✅ created — 11 READY, 3 CODE_CHANGE_REQUIRED, 7 MISSING_OWNER_INPUT, 9 MISSING_EXTERNAL_RESOURCE |
| `docs/STAGING_DEPLOYMENT_STATUS.md` | ✅ this file, updated |
| `.env.staging.example` | ✅ created — 24 variables, placeholders only |
| `apps/web/.env.staging.example` | ✅ created — 6 variables |
| `apps/api/app/core/config.py` | ✅ 2 guards, 18 tests |
| `apps/api/tests/test_staging_guards.py` | ✅ 18 cases |

## B.2 Two findings worth the owner's attention

**1. Razorpay TEST vs LIVE is decided by key prefix, not host.** Both modes use
`api.razorpay.com`; only `rzp_test_` vs `rzp_live_` distinguishes them. The
guard keys on that prefix, so a staging box holding live keys **refuses to
boot**. A URL-based guard — the obvious implementation — would have been a no-op
or would have rejected the correct configuration. Caught before it shipped.

**2. Two frontend variables are undeclared and fall back to production values.**
`VITE_SITE_URL` and `VITE_SUPPORT_EMAIL` are read through casts because they are
absent from `ImportMetaEnv`. A typo in either silently yields
`https://caprep.in` / `support@caprep.in` — so a staging deploy could emit
production URLs, or mail a real person. Recorded as `CODE_CHANGE_REQUIRED`;
**not** fixed here, because it is a production frontend typing change and P2A is
configuration-only. It is two lines and should be its own commit before a
staging frontend is deployed.

## B.3 Regression results (P2A)

| Gate | Result |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ 154 files formatted |
| `alembic check` / `heads` | ✅ no new operations / `6c3f7b0a0c13` |
| `pytest` Redis **ON** | ✅ **821 passed**, 246 skipped |
| `pytest` Redis **OFF** | ✅ **821 passed**, 246 skipped |
| `npm test` | ✅ **248 / 248**, 19 files |
| `npm run typecheck` / `lint` / `build` | ✅ exit 0 |
| `check_secrets.py` ×3 | ✅ **CLEAN** |

821 = the previous 803 + 18 new guard cases. No existing test was modified,
skipped or weakened.

> One suite failure appeared mid-milestone and was **my own local artifact**: an
> earlier build of `apps/web/dist` had been made with the Vitest fake key, so
> `test_the_built_bundle_carries_the_publishable_key_and_no_secret` correctly
> reported that the real publishable key was absent from the bundle. Fixed by
> rebuilding from the real local values. A useful reminder that `dist/`-scanning
> tests assert against whatever was last built, not against source.

## B.4 Readiness matrix — recomputed, still nothing claimed

| Flag | Value | Basis |
|---|---|---|
| `STAGING_DEPLOYED` | **NO** | no staging host exists |
| `STAGING_AUTH_VERIFIED` | **NO** | no staging Auth project |
| `STAGING_DATABASE_VERIFIED` | **NO** | no staging database |
| `STAGING_STORAGE_VERIFIED` | **NO** | no staging buckets |
| `STAGING_REDIS_VERIFIED` | **NO** | no staging Redis |
| `STAGING_WORKER_VERIFIED` | **NO** | no deployed worker |
| `STAGING_OCR_VERIFIED` | **NO** | requires a deployed worker |
| `STAGING_AI_VERIFIED` | **NO** | no staging-scoped credential |
| `STAGING_PAYMENT_TEST_VERIFIED` | **NO** | Razorpay TEST keys absent |
| `STUDENT_E2E_VERIFIED` | **NO** | no staging URL |
| `ADMIN_E2E_VERIFIED` | **NO** | no staging URL |
| `SECURITY_VERIFIED` | **NOT_TESTED** | inspects a deployed bundle |
| `OBSERVABILITY_VERIFIED` | **NOT_TESTED** | nothing deployed to observe |

Unchanged and still proven:

```
LOCAL_GREEN     = YES    (248 web, 821 backend, Redis ON and OFF)
CI_CONFIGURED   = YES
CI_EXECUTED     = YES
CI_GREEN        = YES    (runs 36350781773 and 36351190711)
```

**STAGING_READY_FOR_DEPLOYMENT = NO.** Production readiness is not claimed and
is not assessable.

## B.6 P2A-2 — Supabase staging connection: BLOCKED

An attempt to connect the staging configuration to a new Supabase project could
not proceed: the material supplied was a **Google OAuth client**, not a Supabase
credential, and contained none of the five values the isolation proof requires.

- A `GOCSPX-` Google client secret was exposed in plaintext. **It is not in the
  repository** — `git grep` over `HEAD` finds nothing, the tree is clean, and all
  three secret scans are CLEAN. It still needs rotating at source, because being
  out of the repository is not the same as not being exposed.
- Full findings, the open question about where a Google client secret should
  live, and the five values needed to unblock: **`docs/STAGING_SUPABASE_SETUP.md`**.

No database was connected, no migration was run, and no development resource was
touched. All gates re-verified green: 821 backend, 248 web, secrets CLEAN ×3.

## B.7 P2A-3 — Supabase staging validation: 4 of 6 proven

Full detail in **`docs/STAGING_SUPABASE_VALIDATION.md`**. Summary:

```
STAGING_SUPABASE_CREATED    = YES
STAGING_SUPABASE_ISOLATED   = YES
STAGING_AUTH_REACHABLE      = YES
STAGING_STORAGE_VERIFIED    = BLOCKED_EXTERNAL
STAGING_DATABASE_IDENTIFIED = BLOCKED_EXTERNAL
STAGING_MIGRATIONS_APPLIED  = BLOCKED_EXTERNAL
```

**Project isolation is proven behaviourally**, not by comparing refs: the staging
publishable key returns 200 from the staging project while the **development** key
returns 401 with *"This API key might also be owned by another Supabase project."*
Auth is reachable: JWKS serves one `ES256` key, which is what `PyJWKClient`
expects, and the app-derived `jwks_url` matches the live endpoint exactly.

The three blocked flags share **one root cause: there is no local staging
configuration file.** `.env.staging` does not exist, only the placeholder
templates. So there is no staging `DATABASE_URL` to run
`SELECT current_database()` against, and no rotated secret key to enumerate
buckets with.

**`alembic upgrade head` was deliberately not run.** Applying migrations is the
irreversible action in this milestone, and the target database is unproven. The
head is known (`6c3f7b0a0c13`) and CI already proves upgrade → downgrade →
upgrade, so the chain is sound; only the target is missing. The development
database was not written to in any way.

A **seed mechanism already exists** (`apps/api/app/seed.py`): idempotent by
construction, synthetic reference content rather than exported development data,
with a safe `--check` mode. It was not run — no staging connection to run it
against — but it means the later E2E milestone will not need a new seed system.

Also confirmed for Phase 6: `NEXT_PUBLIC_*` appears **nowhere** in the repository
(0 occurrences), the frontend uses `VITE_` only, and no secret carries a `VITE_`
prefix.

## B.8 P2A-4 — identity, migrations, seed: BLOCKED at Phase 1

This milestone was supposed to load `.env.staging`, prove database identity, and
apply migrations to staging. **`.env.staging` does not exist on this machine.**

Verified across every plausible location — repo root, `apps/web`, `apps/api`,
`~/Downloads`, `$HOME`, `/tmp`, the shell environment, and a depth-4 `find`. All
absent. The only matches are the **unfilled `.example` templates** from P2A, still
containing `<STAGING_PROJECT_REF>` placeholders. The only Supabase ref anywhere on
this machine is the development one.

```
STAGING_SUPABASE_CREATED    = YES
STAGING_AUTH_REACHABLE      = YES
STAGING_SUPABASE_ISOLATED   = BLOCKED_EXTERNAL
STAGING_DATABASE_IDENTIFIED = BLOCKED_EXTERNAL
STAGING_MIGRATIONS_APPLIED  = BLOCKED_EXTERNAL
STAGING_STORAGE_VERIFIED    = BLOCKED_EXTERNAL
STAGING_SEED_VERIFIED       = BLOCKED_EXTERNAL
```

Consequently **`alembic upgrade head` was not run against anything**, no bucket
was created or probed, no seed was executed, and no backend was started. The
development database was not connected to even read-only: it is `caprep` on
`localhost`, named in the task as a database that must not be touched, and reading
its name would prove nothing about staging.

Two things were still verified usefully:

- **The seed's `--check` mode is genuinely read-only.** `main()` returns at
  `if check:` before any `seed_all()` call, confirmed by reading the code rather
  than trusting the docstring. The outstanding item is the two-run idempotency
  check, which genuinely needs a live staging database.
- **`.env.staging` is already gitignored** (`.gitignore:19`) and all four tracked
  `.env*` files are `.example` templates. A filled staging config could not be
  committed even by accident.

Full evidence, including the exact search performed and the unblock procedure, in
**`docs/STAGING_SUPABASE_VALIDATION.md`**.

Regression: ruff check and format clean, alembic check clean, **821 backend with
Redis both ON and OFF**, 248/248 web, typecheck, lint, build pass, secrets CLEAN
×3. No test modified, skipped or weakened.

## B.9 P2A-4b — staging templates fixed after being exercised

`.env.staging` is still absent (the credentials were rotated and never reached
me), so Phases 2–9 remain blocked. But rather than leave the templates
unverified, they were run. Doing so found **two defects that would have broken the
owner's next attempt**:

1. **`Settings` never reads `.env.staging`.** `env_file` is hardcoded to `.env`,
   so a filled `.env.staging` is ignored and the run silently uses **development**
   configuration — connecting to `localhost` while looking healthy. Values must be
   exported with `set -a; . ./.env.staging; set +a`.
2. **The templates were not sourceable.** Placeholders were written as
   `<STAGING_PROJECT_REF>`, and `<` is a shell redirection operator, so eight
   lines across both templates were syntax errors and sourcing aborted part-way.
   Placeholders are now `__NAME__`.

Both are invisible on review and no existing gate loads these files, so
`TestStagingTemplatesAreLoadable` was added — 9 cases across the two templates,
**mutation-verified** twice: reintroducing the `<NAME>` form fails two of them,
and substituting a realistic password for a placeholder fails the scanner check.

A third defect surfaced while fixing the second: the `__NAME__` form parsed
cleanly but produced a syntactically valid DSN, so `check_secrets.py --worktree`
failed with 2 unexpected matches — the scanner correctly treating a placeholder
as a live database credential. `check_secrets.py` already recognises `your` as a
placeholder marker, so the final convention is **`your-NAME`**. The scanner was
not weakened and no allow-list entry was added.

Backend test count 821 → **830**; Redis ON and OFF both green. Full detail in
**`docs/STAGING_SUPABASE_VALIDATION.md`** §8.

## B.10 P2A-5 — environment selection is now explicit and fails closed

**Status: `STAGING_READY_FOR_DEPLOYMENT` unchanged (still BLOCKED_EXTERNAL), but
the silent-fallback hazard is closed.** `.env.staging` is still absent — the
credentials were rotated and never reached me — so the database phases remain
blocked, but the mechanism that made that state *dangerous* is fixed.

`Settings.model_config` hardcoded `env_file=".env"`, which made this possible with
no warning:

```text
ENVIRONMENT=staging → .env read anyway → development DATABASE_URL
                    → service starts on localhost → /health returns 200
```

Selection is now driven by the `ENVIRONMENT` variable: `development` reads `.env`,
`staging` reads `.env.staging`, `production` reads `.env.production`, `test` reads
no file, and an unrecognised value reads no file. `staging` and `production`
**refuse to start** when their file is absent, raising a dedicated
`ConfigurationError` whose message names the file and states the reason. No
`set -a; source` is required — the earlier approach depended on shell state.

**29 new tests**, each in a `tmp_path` workspace so the developer's real `.env` is
never touched, and **mutation-verified**: restoring the old hardcoded behaviour
makes 9 of them fail, including the critical "staging + `.env` present +
`.env.staging` absent must FAIL" case.

Two behaviours worth recording because they were got wrong first and are now
pinned by tests:

- An **unset** `ENVIRONMENT` must read `.env`, or a contributor who exports nothing
  silently loses their configuration.
- A **typo** like `stagng` raises `ValidationError` because `environment` is a
  `Literal`. That is better than the quiet fallback originally planned, and it is
  asserted so it cannot drift into something laxer.

Full detail, per-environment tables and the precedence rules:
**`docs/ENVIRONMENT_CONFIGURATION.md`**.

Regression: **859 backend** (830 + 29) with Redis ON and OFF, 248/248 web, ruff,
format, alembic check, typecheck, lint and build clean, secrets CLEAN ×3. No
existing test modified or weakened.






## B.5 Next milestone

P2B begins when the owner has provisioned the nine external resources, in this
order — the first removes the largest risk:

1. **Staging Supabase project** (+ database, Auth, private buckets)
2. **Staging Redis**
3. **Razorpay TEST keys**
4. **Staging AI key**
5. **Hosting decisions** → Render staging environment, frontend project
6. **Domains** → CORS allow-list, OAuth redirect URLs

P2A deliberately created **no** local mock environment and **no** fake
infrastructure. The value now comes from testing the real integrations.


## A.3 What P2A changed, and what it did not

| Changed in P2A | Effect on the blockers |
|---|---|
| `.env.staging.example`, `apps/web/.env.staging.example` | **None.** They record the names; the values must still be provisioned |
| `Settings.staging_isolation_problems()` | Turns a silent shared-resource mistake into a reported one. Does not create a staging database |
| `_staging_must_not_take_live_money` | Prevents a staging box from charging a real card. Does not create Razorpay TEST keys |
| `docs/STAGING_ENVIRONMENT_REFERENCE.md`, `docs/STAGING_DEPLOYMENT_INPUTS.md` | Turns "blocked" into a specific, ordered provisioning list |

**None of the nine `MISSING_EXTERNAL_RESOURCE` items moved.** That is the honest
outcome: P2A was the work that could be done without infrastructure, and it is
now done.


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
