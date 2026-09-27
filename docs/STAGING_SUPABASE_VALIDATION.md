# Staging Supabase Validation

**Milestone:** P2A-3 — prove separation, prepare the staging database
**Baseline commit:** `4ac1b78`
**Nothing deployed. No migration applied. No development resource touched.**

---

## Headline

Four of six flags are `YES`. Two are `BLOCKED_EXTERNAL`, and **both are blocked
for the same single reason: there is no local staging configuration file.**

Project isolation, Auth and configuration-safety are proven. The database work
could not start — not because of a defect, but because
`Settings.database_url` has nothing staging-shaped to read.

| Flag | Value |
|---|---|
| `STAGING_SUPABASE_CREATED` | ✅ **YES** |
| `STAGING_SUPABASE_ISOLATED` | ✅ **YES** |
| `STAGING_DATABASE_IDENTIFIED` | ⛔ **BLOCKED_EXTERNAL** |
| `STAGING_AUTH_REACHABLE` | ✅ **YES** |
| `STAGING_STORAGE_VERIFIED` | ⛔ **BLOCKED_EXTERNAL** |
| `STAGING_MIGRATIONS_APPLIED` | ⛔ **BLOCKED_EXTERNAL** |

## 1. Development project identified

| Property | Value |
|---|---|
| Ref | `zyrmlnpvylhcpyaoizyz` |
| URL | `https://<dev-ref>.supabase.co` |
| Database | local Postgres on `localhost:5432`, database `caprep` |
| Where it lives | untracked `.env`, `apps/web/.env.local`, `infra/render.yaml` |

## 2. Staging project identified

| Property | Value |
|---|---|
| Ref | `vfewnfwyagcxtaxbmwqb` |
| URL | `https://<staging-ref>.supabase.co` |
| Database | **unknown** — no connection string available |
| Where it lives | **nowhere locally** — see §11 |

**No secret value appears in this document.** Only refs and host shapes.

## 3. Isolation result — **YES**

Proven behaviourally, not by string comparison:

| Key presented to the staging project | HTTP | Response |
|---|---|---|
| Staging publishable key | **200** | auth settings returned |
| **Development** publishable key | **401** | *"This API key might also be owned by another Supabase project."* |

A Supabase key is bound to exactly one project. Staging rejecting the development
key by name proves the two are genuinely distinct. Also confirmed: distinct refs,
and the staging publishable key differs from the development one.

**Recorded limitation:** this used the publishable key now being rotated (see
`docs/STAGING_SUPABASE_SETUP.md` §1). The *project*-level finding stands because
the ref and URL do not change on rotation; the key-level check should be repeated
once with the new key.

## 4. Database identity result — **BLOCKED_EXTERNAL**

**No staging `DATABASE_URL` exists anywhere on this machine.**

```
.env.staging              absent
apps/web/.env.staging     absent
.env.staging.local        absent
apps/api/.env.staging     absent
process environment       0 staging variables
```

Only `.env.staging.example` and `apps/web/.env.staging.example` are present — the
**placeholder templates** from P2A, containing `<STAGING_PROJECT_REF>` style
placeholders, not real values.

Therefore `SELECT current_database()` **was not run**. It cannot be: there is no
connection to open. The development database is `caprep` on `localhost:5432`, and
it was deliberately left alone.

**Phase 8 (`alembic upgrade head`) was not run.** Applying migrations is the
irreversible, hard-to-reverse action that must not proceed on an unproven target,
and the target is unproven. The head is known — `6c3f7b0a0c13` — and CI already
proves upgrade → downgrade → upgrade, so the chain is sound; only the target is
missing.

## 6. Storage result — **BLOCKED_EXTERNAL**

| Check | Result |
|---|---|
| `/storage/v1/status` | ✅ 200 |
| `/storage/v1/bucket` (anonymous) | ✅ 200, body `[]` |
| Bucket list with the secret key | ⛔ not run — key is being rotated |
| Upload / sign / download / delete | ⛔ not run |

The app expects three buckets: **`question-pdfs`**, **`question-media`**,
**`user-uploads`**. Whether they exist in the staging project is **unknown**: an
empty anonymous list is equally consistent with "no buckets" and "buckets exist
but are private". Distinguishing them requires the secret key, which should be
rotated first and must not be pasted into a conversation.

**No development file was copied and no probe object was created**, because
creating one requires the secret key.

## 7. Migration result — **NOT APPLIED**

| | |
|---|---|
| `alembic upgrade head` on staging | ⛔ **not run** — target database unproven |
| `alembic check` (repo) | ✅ no new operations |
| `alembic heads` | ✅ `6c3f7b0a0c13` |
| `alembic current` (development, read-only) | `6c3f7b0a0c13` |

Recorded explicitly: **the development database was not migrated, downgraded, or
written to in any way.** `alembic current` is a read-only query against it.

## 8. Seed-data status — mechanism found, deliberately unused

**A seed mechanism already exists and is safe:**

| File | Purpose |
|---|---|
| `apps/api/app/seed.py` | `python -m app.seed` / `--check` (report only) |
| `apps/api/app/seed_data.py` | synthetic curriculum, questions, mock papers |

Properties that make it suitable for staging, from the module's own documentation:

- **Idempotent by construction** — upserts on natural keys, deterministic uuid5 ids
  where no natural key exists. Re-running is a no-op, which is what makes an
  environment reproducible.
- **Synthetic reference content**, not exported development data. The only user it
  creates is a fixed `content@seed.invalid` placeholder.
- **`--check` changes nothing**, so it is safe against staging to report gaps.
- Its own tests pass: **29 passed**.

**It was not run against staging** — it needs a database connection, and there is
none. Against development it was not run either; that database is not this
milestone's to write to.

### Synthetic data still required for later E2E


## 9. Security result — **CLEAN**

| Check | Result |
|---|---|
| `check_secrets.py --worktree` | ✅ CLEAN |
| `check_secrets.py --staged` | ✅ CLEAN |
| `check_secrets.py --rev HEAD` | ✅ CLEAN |
| `git status --short` | ✅ 0 changes |
| Staging **key material** anywhere in git history | ✅ **0 matches** (4 patterns checked) |
| Staging **project ref** in git history | 2 matches, both prose in `docs/STAGING_SUPABASE_SETUP.md` |

The ref is public information — it appears in every client-side URL by design. No
`sb_secret_` or `sb_publishable_` value is in the repository or its history.

### Phase 6 — frontend configuration safety ✅

| Check | Result |
|---|---|
| `NEXT_PUBLIC_*` anywhere in the repo | ✅ **0 occurrences** |
| Frontend env prefix actually used | `VITE_` only |
| Any secret given a `VITE_` prefix | ✅ none |

Frontend variables, all public by design: `VITE_SUPABASE_URL`,
`VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE_URL`, `VITE_APP_ENV`, `VITE_SITE_URL`,
`VITE_SUPPORT_EMAIL`. The `NEXT_PUBLIC_*` values supplied earlier belong to a
Next.js project; this is React 19 + Vite, so they would have been silently
ignored. **Corrected names are in `.env.staging.example`.**

## 10. Remaining external blockers

| # | Blocker | Blocks | Action |
|---|---|---|---|
| 1 | **No local staging config file** | Phases 3, 8, 9 | create `.env.staging` from the template |
| 2 | **No staging `DATABASE_URL`** | database identity, migrations | from the staging Connect dialog |
| 3 | **Secret key awaiting rotation** | storage verification, seed run | rotate, then set locally |
| 4 | **Bucket existence unknown** | storage | verify after rotation |
| 5 | Staging Redis | later milestone | not this one |
| 6 | Razorpay TEST keys | later milestone | not this one |
| 7 | Hosting decisions | later milestone | not this one |

**Blocker 1 is the root cause of 2, 3 and 4.** Creating one gitignored file with
the staging values unblocks this entire milestone.

## 11. How to unblock — and the one rule to keep

Create `.env.staging` (already gitignored) from the template:

```bash
cp .env.staging.example .env.staging    # already gitignored
$EDITOR .env.staging                     # fill in; do not commit
```

The single check that matters before any migration runs:

```
current_database()  must NOT be caprep, caprep_test, caprep_v2_test
SUPABASE_URL ref    must NOT be zyrmlnpvylhcpyaoizyz
```

**Do not paste values into this conversation.** The `sb_secret_` key and the
connection string are both credentials, and both are already compromised or
unverified. Set them on disk and tell me only the outcomes of the two checks
above, neither of which is secret:

- the database **name** returned by `SELECT current_database()`
- the staging **project ref**

With those I can prove database identity and apply migrations to staging only.

## 12. Regression results

| Gate | Result |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ 154 files formatted |
| `alembic check` | ✅ No new upgrade operations detected |
| `pytest` | ✅ **821 passed**, 246 skipped |
| `npm test` | ✅ **248 / 248**, 19 files |
| `npm run typecheck` / `lint` / `build` | ✅ all pass |
| `check_secrets.py` ×3 | ✅ CLEAN |

No test was modified, skipped or weakened. Documentation only.

The seed covers reference content only. Not covered, and not invented here:

| Need | Status |
|---|---|
| student / editor / admin test accounts | ⛔ not created — needs Supabase Auth on staging |
| synthetic question documents (PDFs) | ⛔ not created |
| test subscription / payment rows | ⛔ not created — Razorpay TEST is a later milestone |

`scripts/live_verify.py` has a `seed_role_user()` helper that creates role users
from a database URL. It was **not run**: it writes real rows and needs a proven
staging target.


## 5. Auth result — **YES**

All read-only, **no secret sent**:

| Check | Result |
|---|---|
| `/auth/v1/.well-known/jwks.json` | ✅ 200, 1 key, `alg=ES256`, `kty=EC` |
| `/auth/v1/settings` with staging key | ✅ 200 |
| `/auth/v1/health` | reachable (401 unauthenticated, as expected) |
| App-derived `jwks_url` vs live | ✅ identical |
| `SUPABASE_JWKS_URL` used by the app? | ❌ 0 occurrences — derived, not configured |

`ES256` is what `PyJWKClient` expects, so the backend will verify staging tokens
with **no code change**.

**Backend configuration points at staging** when `SUPABASE_URL` is the staging URL:
`security.py` derives both the JWKS URL and the expected issuer from that single
value, so pointing it at staging redirects verification to staging.

**No development user was reused and no test user was created.**
