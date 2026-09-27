# Staging Supabase Setup

**Milestone:** P2A-2 — connect staging config and prove isolation
**Baseline commit:** `c0228ed`
**Status: BLOCKED — the Supabase staging credentials were not supplied.**

---

## Summary

This milestone could not proceed past step 1. The material described as a
"Supabase credential" is a **Google OAuth client**, not a Supabase project
credential, and it contains none of the five values the isolation proof needs.

**No Supabase project was created, contacted, or verified. No database was
connected to. No migration was run. No credential value was written to any file,
printed, or committed.**

The one genuinely useful thing it did contain — a leaked Google client secret — is
handled in §1 and needs your action.

## 1. Security issue — rotate the exposed Google client secret

The pasted material is a **Google OAuth 2.0 web client**, identified by three
things, none of which are Supabase:

| Field | Value shape | Belongs to |
|---|---|---|
| `client_id` | `…@apps.googleusercontent.com` | Google |
| `client_secret` | prefixed `GOCSPX-` | Google Cloud |
| `project_id` | `projecttt-4b226` | Google Cloud project |
| `auth_uri` / `token_uri` | `accounts.google.com`, `oauth2.googleapis.com` | Google |

A Supabase staging credential is a different shape entirely: a project ref, a
`https://<ref>.supabase.co` URL, an `sb_publishable_…` key, an `sb_secret_…` key,
and a Postgres connection string. **None were present.**

### Action required by the owner

A `GOCSPX-` client secret was shared in plaintext. Treat it as compromised:

1. Google Cloud Console → project `projecttt-4b226`
2. **APIs & Services → Credentials** → the affected OAuth client
3. **Regenerate the client secret**
4. Update the consumer of it (see §2)

**Verification that nothing leaked:** the value does not appear in any committed
file (`git grep` over `HEAD` returns nothing), the working tree is clean, and all
three secret scans report **CLEAN** on worktree, staged and `HEAD`. It was never
written to disk.

## 2. Open question — where should the Google client secret live?

This is flagged rather than assumed, because it changes the staging design.

If Google sign-in is meant to run through **Supabase's hosted OAuth**, then
Supabase performs the authorization-code exchange and the Google client secret
belongs in the **Supabase dashboard** (Authentication → Providers → Google). The
application would never hold it, and no `GOOGLE_*` variable would exist.

If instead the application is meant to perform the exchange itself, it would need
a server-side `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` pair — and this
codebase has **no such configuration today**:

```
git grep -liE 'google.*client_id|GOOGLE_CLIENT|client_secret' HEAD -- apps/
  → no matches
```

Sign-in in this application is Supabase-hosted (`src/lib/supabase.ts` builds a
`GoTrueClient` and calls `auth.signInWithPassword` / `signInWithOAuth`). **The

## 4. Step 1 — actual configuration variables (the one completed step)

Read from the code, not assumed. **Names and classification only; no values.**

### Supabase — server side

| Variable | Component | Secret? | Browser-visible? |
|---|---|:--:|:--:|
| `SUPABASE_URL` | API, worker | no | **yes** — also set as `VITE_SUPABASE_URL` |
| `SUPABASE_SECRET_KEY` | API, worker | **yes** | no |
| `SUPABASE_SERVICE_ROLE_KEY` | — | **yes** | no — legacy alias, CLI only |
| `STORAGE_BUCKET` | API, worker | no | no |
| `DATABASE_URL` | API, worker, Alembic | **yes** | no |
| `DIRECT_DATABASE_URL` | Alembic only | **yes** | no |

### Supabase — client side

| Variable | Secret? | Browser-visible? |
|---|:--:|:--:|
| `VITE_SUPABASE_URL` | no | yes, by design |
| `VITE_SUPABASE_ANON_KEY` | no | yes, by design — publishable key only |

### Other integrations

| Variable | Secret? | Browser-visible? |
|---|:--:|:--:|
| `REDIS_URL` | **yes** | no |
| `AI_PROVIDER_API_KEY` | **yes** | no |
| `RAZORPAY_KEY_ID` | no (public by design) | no — backend passes to Checkout |
| `RAZORPAY_KEY_SECRET` | **yes** | no |
| `RAZORPAY_WEBHOOK_SECRET` | **yes** | no |
| `CORS_ORIGINS` | no | no |

`SUPABASE_ANON_KEY` appears in `infra/render.yaml` and the Supabase CLI but is
**not** a `Settings` field; the browser uses `VITE_SUPABASE_ANON_KEY`. Consistent
with the current guidance to use a publishable key in client code and a secret key
only server-side, which this repository already follows.

## 5. What is needed to unblock

Five values, from the **staging** project's Connect dialog and Settings → API
Keys. **Do not send them here.** Set them locally or in the provider dashboard;
this milestone needs only that they exist.

| # | Needed | For |
|---|---|---|
| 1 | Staging project ref (from the URL) | Proving the project differs from development |
| 2 | `https://<ref>.supabase.co` | `SUPABASE_URL` / `VITE_SUPABASE_URL` |
| 3 | Publishable key (`sb_publishable_…`) | `VITE_SUPABASE_ANON_KEY` |
| 4 | Secret key (`sb_secret_…`) | `SUPABASE_SECRET_KEY` |
| 5 | Staging Postgres connection string | `DATABASE_URL` (+ `DIRECT_DATABASE_URL` if pooled) |

With 1–5 in place, steps 2–7 become executable: database identity, project
isolation, Auth/JWKS reachability, storage inspection, and key safety.

### No migrations

Per the instruction, `alembic upgrade head` will **not** be run against staging
until `DATABASE_URL` is *proven* to point at the new project — read
`SELECT current_database()` plus project identity first, and confirm the database
name is not `caprep`, `caprep_test`, or `caprep_v2_test`. The current local
database is `caprep` on `localhost:5432` and must not be touched.

## 6. Regression results (this milestone)

Run to confirm the blocked status changed nothing:

| Gate | Result |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ 154 files formatted |
| `alembic check` | ✅ No new upgrade operations detected |
| `pytest` | ✅ **821 passed**, 246 skipped |
| `npm test` | ✅ **248 / 248**, 19 files |
| `npm run typecheck` / `lint` / `build` | ✅ exit 0 |
| `check_secrets.py` ×3 | ✅ **CLEAN** |

No test was modified. No code, configuration, or schema changed in this
milestone — only this document.

## 7. Stop-condition flags

| Flag | Value | Basis |
|---|---|---|
| `STAGING_SUPABASE_CREATED` | **UNVERIFIED** | not evidenced by the material supplied |
| `STAGING_DATABASE_IDENTIFIED` | **NO** | no staging connection string supplied |
| `STAGING_SUPABASE_ISOLATED` | **NO** | cannot compare a ref that was not supplied |
| `STAGING_AUTH_REACHABLE` | **NO** | no staging project URL to reach |
| `STAGING_STORAGE_VERIFIED` | **NO** | cannot inspect a project not identified |
| `SECRETS_CLEAN` | **YES** | scanner CLEAN on worktree, staged and `HEAD`; the leaked value is not in the repository |

`STAGING_SUPABASE_CREATED` is recorded as **UNVERIFIED** rather than YES: a
Google Cloud project ID is not evidence of a Supabase project. Set it once a
Supabase staging project ref exists.

first design matches the existing architecture** and requires no application
change. Recommend confirming before any staging deploy.

## 3. A variable name in the request that does not exist

The request listed `CORS_ALLOWED_ORIGINS`. **This codebase has no such
variable:**

```
git grep -c 'CORS_ALLOWED_ORIGINS' HEAD   → 0 occurrences
```

The real name is **`CORS_ORIGINS`** (`cors_origins` in
`app/core/config.py`, env name `CORS_ORIGINS`), as recorded in
`docs/STAGING_ENVIRONMENT_REFERENCE.md` and `.env.staging.example`. No new
variable was invented to satisfy the request.
