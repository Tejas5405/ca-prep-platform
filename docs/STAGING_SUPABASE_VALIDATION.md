# Staging Supabase Validation

**Milestone:** P2A-6 — Supabase staging initialization (re-run of P2A-4)
**Baseline commit:** `bd02ba5`
**Nothing deployed. No migration applied. No development resource touched.**

---

## P2A-6 addendum — still blocked at Phase 1, but one fact changed

P2A-6 was requested on the basis that the owner had created `.env.staging`.
**It has not.** The file does not exist in the repository root, `apps/web`,
`apps/api`, `$HOME`, `/tmp`, or the environment. The blocker is identical to
P2A-4's and is unchanged by it.

```text
STAGING_SUPABASE_CREATED    = YES
STAGING_SUPABASE_ISOLATED   = YES
STAGING_AUTH_REACHABLE      = YES
STAGING_DATABASE_IDENTIFIED = BLOCKED_EXTERNAL
STAGING_MIGRATIONS_APPLIED  = BLOCKED_EXTERNAL
STAGING_STORAGE_VERIFIED    = BLOCKED_EXTERNAL
STAGING_SEED_VERIFIED       = BLOCKED_EXTERNAL
```

**What did change:** P2A-4 recorded that the only Supabase ref on this machine
was the development one. That is **superseded**. The staging project
`vfewnfwyagcxtaxbmwqb` from P2A-2 exists, is a different project from
`zyrmlnpvylhcpyaoizyz`, and is reachable — JWKS serves one `ES256` key and
`/auth/v1/health` answers (401 without a key, correct for anon).

`STAGING_SUPABASE_ISOLATED = YES` therefore stands on the **behavioural** proof
recorded in `STAGING_SUPABASE_SETUP.md` §3, not on comparing two strings:
staging rejected the development publishable key by name, which only happens
when the two keys belong to different projects.

### Why project isolation is not database identity

These are different claims, and conflating them is exactly the error to avoid:

| Claim | Proven by | Status |
|---|---|---|
| The staging **project** is not the dev project | key rejection, JWKS, `/auth/v1/*` | ✅ YES |
| The **database** behind staging is a staging DB | `SELECT current_database()` | ❌ **BLOCKED** — no `DIRECT_DATABASE_URL` |

An isolated project can still be pointed at any database. Until
`current_database()` is read from the staging connection, the migration target is
unproven, and `alembic upgrade head` stays unrun. It is the irreversible action
in this milestone; the head `6c3f7b0a0c13` is known and CI already proves
upgrade → downgrade → upgrade, so only the **target** is missing.

### Non-sensitive facts recorded

| Item | Value |
|---|---|
| Staging project ref | `vfewnfwyagcxtaxbmwqb` |
| Development project ref | `zyrmlnpvylhcpyaoizyz` (must never be used in staging) |
| Staging database name | **unknown** — not yet provable |
| Migration head (expected) | `6c3f7b0a0c13` |
| Seed result | not run — no staging connection |
| Health result | not run — no staging connection |

No secret, password, connection string, or token appears above, and none will.

### Gates re-verified at `bd02ba5`

859 backend tests with Redis both reachable and unreachable, 248/248 web,
ruff, format, alembic check, typecheck, lint, build, and `check_secrets` CLEAN on
all three scopes. No existing test modified, skipped, or weakened.

### Still outstanding

Rotate the exposed `sb_secret_` and `GOCSPX-` Google client secrets. They are not
in the repository and the scanner is clean, but they were exposed by being
pasted.

---

## P2A-4 record (retained)

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

## 8. Two real defects found in the staging templates

Because the milestone was blocked anyway, the templates were exercised rather than
left unverified. Doing so found two bugs that would have hit the owner directly.

### 8.1 `Settings` never reads `.env.staging`

`Settings.model_config` hardcodes `env_file=".env"`. Verified empirically: with
only `.env.staging` present in the working directory, `Settings()` still loaded
the **development** `.env`. A filled `.env.staging` file is therefore **not picked
up automatically** — its values must be exported:

```bash
set -a; . ./.env.staging; set +a
```

Without that, a staging run silently operates on development configuration. This
is the more dangerous of the two findings, because nothing errors: the process
starts, connects to `localhost`, and looks healthy.

### 8.2 The templates were not sourceable at all

Placeholders were written as `<STAGING_PROJECT_REF>`. **`<` is a shell
redirection operator**, so every such line is a syntax error:

```
.env.staging: line 132: syntax error near unexpected token `newline'
```

Sourcing would abort part-way through, loading *some* variables and silently
missing the rest. Eight lines were affected across both templates.

**The first fix then broke a different gate.** `__NAME__` parsed cleanly, but it
also produced a syntactically valid DSN, so `check_secrets.py --worktree` began
failing with `2 unexpected matches` — the scanner correctly treating a
placeholder as a live database credential.

`check_secrets.py` already recognises `your` as a placeholder marker, so the
final convention is **`your-NAME`**, satisfying both constraints at once: valid
shell, and allow-listed as the documented fake it is. The scanner was **not**
weakened and no allow-list entry was added.

**All three defects were found only by actually running the thing.** None is
detectable by reading the files, and no existing gate loads them.

### 8.3 Regression coverage added

`TestStagingTemplatesAreLoadable` — 9 new cases:

| Check | Guards against |
|---|---|
| template exists | a deleted template |
| `bash -n` parses it | the `<NAME>` redirection bug |
| no `<NAME>` on an assignment line | the same, with a clearer error |
| still carries `your-` markers | a template filled with real values |
| **the secret scanner stays clean** | a placeholder that reads as a live credential |

**Both mutation-verified.** Reintroducing `<NAME>` fails two tests; substituting a
realistic password for the placeholder fails the scanner test. Neither passes
vacuously. `bash` is resolved via `shutil.which` and made absolute, so a `bash`
earlier on `PATH` cannot decide a security-shaped check.

Test count: 821 → **830**.

## 9. To unblock — one file, one command

```bash
cd "/Users/tejasraykar/Downloads/CA Version 2/ca-prep-platform"
cp .env.staging.example .env.staging
$EDITOR .env.staging                     # fill in; do not commit
```

Minimum to make Phases 2–5 runnable:

| Variable | Source |
|---|---|
| `ENVIRONMENT=staging` | already in the template |
| `SUPABASE_URL` | staging project URL |
| `SUPABASE_SECRET_KEY` | the **rotated** key |
| `DATABASE_URL` | staging Connect dialog — **pooler, port 6543** |
| `DIRECT_DATABASE_URL` | staging Connect dialog — **direct, port 5432** |

`DIRECT_DATABASE_URL` is not optional for migrations. `resolve_migration_url()`
prefers it over `DATABASE_URL` precisely because a transaction pooler cannot hold
the session that `alembic upgrade` needs — running DDL through the pooler is the
classic way to get a half-applied schema.

**And the loading step matters (§8.1):**

```bash
set -a; . ./.env.staging; set +a
```

Without it the run uses development configuration.

**Do not paste any values here.** Once the file exists, send only:

- the staging **project ref**
- the database **name** from `SELECT current_database()`

Neither is a secret, and both are all that is needed to confirm identity before
touching anything.




---
