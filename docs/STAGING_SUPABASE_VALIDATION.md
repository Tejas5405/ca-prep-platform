# Staging Supabase Validation

**Milestone:** P2A-4 — identity, migrations, seed against STAGING
**Baseline commit:** `e7d0366`
**Nothing deployed. No migration applied. No development resource touched.**

---

## Headline

**`.env.staging` does not exist.** The milestone that was supposed to load it,
prove database identity, and apply migrations could not start, because the file
the task depends on is not on disk.

This is not a subtle failure and not a partial result. Phases 1–5 and 7–9 are
**blocked at the first step**, so:

- no `DATABASE_URL` was read
- no `SELECT current_database()` was run
- **`alembic upgrade head` was NOT run against anything**
- no bucket was created, listed or probed
- no seed was run
- no backend was started

Per the task's own instruction — *"If the database identity cannot be proven:
STOP"* — execution stopped at Phase 1. Nothing was guessed or substituted.

| Flag | Value |
|---|---|
| `STAGING_SUPABASE_CREATED` | ✅ **YES** |
| `STAGING_SUPABASE_ISOLATED` | ⛔ **BLOCKED_EXTERNAL** — re-provable in seconds once config exists |
| `STAGING_DATABASE_IDENTIFIED` | ⛔ **BLOCKED_EXTERNAL** |
| `STAGING_MIGRATIONS_APPLIED` | ⛔ **BLOCKED_EXTERNAL** |
| `STAGING_AUTH_REACHABLE` | ✅ **YES** (proven by public endpoints, independent of local config) |
| `STAGING_STORAGE_VERIFIED` | ⛔ **BLOCKED_EXTERNAL** |
| `STAGING_SEED_VERIFIED` | ⛔ **BLOCKED_EXTERNAL** |

## 1. Evidence that `.env.staging` is absent

Searched exhaustively. Every candidate location was checked:

| Location | Result |
|---|---|
| `.env.staging` (repo root) | **absent** |
| `apps/web/.env.staging` | absent |
| `apps/api/.env.staging` | absent |
| `.env.staging.local` | absent |
| `~/Downloads/.env.staging` | absent |
| `~/.env.staging`, `/tmp/.env.staging` | absent |
| Shell environment (staging vars) | **0** |
| `find` for `.env.staging*`, depth 4 | only the two `.example` files |

The only files found are the **unfilled templates** written in P2A:

```
.env.staging.example
apps/web/.env.staging.example
```

Verified still placeholders, not filled values:

```
SUPABASE_URL=https://<STAGING_PROJECT_REF>.supabase.co
DATABASE_URL=postgresql+psycopg://postgres.<STAGING_PROJECT_REF>:<STAGING_DB_PASSWORD>@…
```

## 2. Why nothing was substituted

The development `DATABASE_URL` in `.env` points at **local Postgres on
`localhost:5432`, database `caprep`**. It is:

- explicitly named in the task as a database that must **not** be used for staging
- not the staging database
- the one database that must stay untouched

Running `alembic upgrade head` against it would have been destructive and outside
the milestone. Connecting to it "just to read `current_database()`" was also
avoided: the task requires the *staging* identity, and reading the development
database's name proves nothing about staging while risking a stray write.

The task's Phase 2 instruction is explicit: *"If equal: STOP immediately. Do not
run migrations. Do not touch the database."* Isolation is unprovable without the
staging URL, so stopping is the compliant outcome.

## 3. What remains provable without local config

`STAGING_AUTH_REACHABLE` survives because the staging project's public endpoints
are reachable directly and need no local file. Verified read-only, no secret sent:

| Check | Result |
|---|---|
| `/auth/v1/.well-known/jwks.json` | ✅ 200, 1 key, `alg=ES256`, `kty=EC` |
| `/auth/v1/settings` with staging publishable key | ✅ 200 |
| **Development key against the staging project** | ✅ **401** — *"might also be owned by another Supabase project"* |
| `/storage/v1/status` | ✅ 200 |
| App-derived `jwks_url` vs live | ✅ identical |

`STAGING_SUPABASE_ISOLATED` is marked **BLOCKED_EXTERNAL** rather than YES
because the definitive proof must be re-run against the *rotated* keys. The
project-level fact — staging ref differs from development — is unchanged by
rotation, and confirming it takes one command once the config exists.

## 4. Seed readiness — verified, not executed

`apps/api/app/seed.py` was inspected and is suitable for staging:

| Property | Evidence |
|---|---|
| Idempotent by construction | upserts on natural keys; deterministic uuid5 ids otherwise |
| Synthetic content, not exported dev data | only user is a fixed `content@seed.invalid` placeholder |
| **`--check` is genuinely read-only** | `main()` returns at `if check:` **before** any `seed_all()` call — confirmed by reading lines 382–405 |
| Own tests pass | 29 passed |

**It was not run.** `--check` needs a database connection to report counts, and
there is none for staging. Running the write path without a proven target is
exactly what the milestone forbids.

The two-run idempotency check (run → run again → no duplication) is therefore
**outstanding**, and is the one thing that genuinely needs a live staging
database.

## 5. Storage — buckets unverified

The app expects three buckets: **`question-pdfs`**, **`question-media`**,
**`user-uploads`**. Whether they exist in the staging project is **unknown**:
the anonymous bucket list is `[]`, equally consistent with "no buckets" and
"buckets exist and are private". Distinguishing them needs the secret key, which
is in no local file.

No bucket was created, no probe object was uploaded, and no bucket was made
public. Per the task: private buckets must never be opened to solve an access
problem.

## 6. Security result — CLEAN

| Check | Result |
|---|---|
| `check_secrets.py --worktree` | ✅ CLEAN |
| `check_secrets.py --staged` | ✅ CLEAN |
| `check_secrets.py --rev HEAD` | ✅ CLEAN |
| `.env.staging` ignored by git | ✅ **YES** (`.gitignore:19`) |
| `.env.staging` tracked | ✅ **NO** |
| Tracked `.env*` files | 4, **all `.example`** |
| Staging key material in git history | ✅ **0 matches** |

The gitignore side is already correct — a filled `.env.staging` could not be
committed even by accident. That was set up in P2A; this is the first time it has
been checked against a file of that name.

## 7. Regression — all green

| Gate | Result |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ 154 files formatted |
| `alembic check` | ✅ No new upgrade operations detected |
| `pytest` Redis **ON** | ✅ **821 passed**, 246 skipped |
| `pytest` Redis **OFF** | ✅ **821 passed**, 246 skipped |
| `npm test` | ✅ **248 / 248**, 19 files |
| `npm run typecheck` / `lint` / `build` | ✅ all pass |

No test was modified, skipped or weakened. Documentation only.

## 8. To unblock — one file

```bash
cd "/Users/tejasraykar/Downloads/CA Version 2/ca-prep-platform"
cp .env.staging.example .env.staging    # already gitignored
$EDITOR .env.staging                     # paste the STAGING values; do not commit
```

Minimum required to make Phases 2–5 runnable:

| Variable | Source |
|---|---|
| `ENVIRONMENT=staging` | already in the template |
| `SUPABASE_URL` | staging project URL |
| `SUPABASE_SECRET_KEY` | rotated key, from the staging project |
| `DATABASE_URL` | staging Connect dialog (pooler, port 6543) |
| `DIRECT_DATABASE_URL` | staging Connect dialog (direct, port 5432) — **required**, because migrations must not run through the pooler |

Once that file exists, the sequence is mechanical: derive the ref, prove it
differs, read `current_database()`, then `alembic upgrade head`, then
`alembic check`, then `python -m app.seed --check` and the two-run idempotency
check.

**Do not paste any of these values into the conversation.** After creating the
file, send only: the staging project ref, and the database name from
`SELECT current_database()`. Neither is a secret, and both are all I need to
confirm identity before touching anything.



---
