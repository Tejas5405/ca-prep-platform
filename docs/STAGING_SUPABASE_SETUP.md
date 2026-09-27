# Staging Supabase Setup

**Milestone:** P2A-2 — connect staging config and prove isolation
**Baseline commit:** `a25d89c`
**Status: project isolation PROVEN. Database, storage contents and bucket list
NOT verified — see §5.** The earlier blocked attempt is in §A.

---

## 🔴 1. Rotate the secret key that was pasted here

A **live `sb_secret_…` key was shared in plaintext in this conversation.** It is
the most sensitive credential in the stack: Postgres `service_role` with
`BYPASSRLS`, so it bypasses row-level security entirely and can read and write
every table in the project, including user rows and payments.

**Treat it as compromised and rotate it in the Supabase dashboard
(Settings → API Keys) before it is used anywhere.** The `GOCSPX-` Google secret
pasted earlier needs the same treatment.

The value was **never written to any file** in this repository, was not committed,
and was **not used** to call any endpoint. Verification below used only the
public URL and the **publishable** key, both public by design — they appear in
the browser bundle.

| Exposed item | Severity | Action |
|---|---|---|
| `sb_secret_…` (Supabase) | **Critical** — full DB access, bypasses RLS | Rotate now |
| `GOCSPX-…` (Google OAuth) | **High** | Rotate now |
| `sb_publishable_…` (Supabase) | None by design | No action |
| Project ref / URL | None by design | No action |

Verified clean: not in any committed file or history, tree clean, all three
secret scans CLEAN.

## 2. Supplied variables — naming corrections

Three of the four names supplied are **not read by this application**. Real names
come from `app/core/config.py` and `apps/web/src/vite-env.d.ts`:

| Supplied | Read? | Correct name |
|---|:--:|---|
| `SUPABASE_URL` | ✅ | `SUPABASE_URL` (backend) / `VITE_SUPABASE_URL` (frontend) |
| `SUPABASE_PUBLISHABLE_KEY` | ❌ | `VITE_SUPABASE_ANON_KEY` / `SUPABASE_SECRET_KEY` |
| `SUPABASE_SECRET_KEY` | ✅ | `SUPABASE_SECRET_KEY` |
| `SUPABASE_JWKS_URL` | ❌ | **not configurable** — derived in `security.py` |

`SUPABASE_JWKS_URL` is deliberate, not an oversight: `security.py:284` derives the
JWKS URL from the project URL so the two cannot drift.

```python
@property
def jwks_url(self) -> str:
    return f"{self._project_url()}/auth/v1/.well-known/jwks.json"
```

Confirmed the derivation matches the live endpoint exactly (§4, check 3).
**Do not add a `SUPABASE_JWKS_URL` variable** — it would create a second source
of truth for something already derived.


---

## 3. Project isolation — PROVEN

| Check | Result |
|---|---|
| Development ref | `zyrmlnpvylhcpyaoizyz` |
| Staging ref | `vfewnfwyagcxtaxbmwqb` |
| Different project | ✅ **YES** |
| Staging publishable key identical to dev | ✅ **NO** — different key |

### The decisive test

Both keys were presented to the **staging** project:

| Key presented to staging | HTTP | Body |
|---|---|---|
| Staging publishable key | **200** | Auth settings returned |
| **Development** publishable key | **401** | *"This API key might also be owned by another Supabase project."* |

A Supabase key is bound to exactly one project, and staging rejects the
development key by name. **Stronger than comparing refs** — it proves the two
projects are genuinely distinct, not merely differently named.

## 4. Auth, JWKS and endpoint reachability

All read-only probes. **No secret was sent.**

| # | Check | Result |
|---|---|---|
| 1 | `/auth/v1/.well-known/jwks.json` | ✅ **200**, 1 key, `alg=ES256`, `kty=EC` |
| 2 | `/auth/v1/health`, `/auth/v1/settings` | reachable; 401 without a key, as expected |
| 3 | App-derived JWKS URL vs live | ✅ **identical** |
| 4 | `/rest/v1/` | reachable |
| 5 | `/storage/v1/status` | ✅ **200** |
| 6 | `/storage/v1/bucket` (anon) | ✅ **200**, body `[]` |

`ES256` is what `PyJWKClient` expects, so the backend will verify staging tokens
with no code change.

**Bucket list is empty.** For anon that is expected and proves nothing either
way — it may mean no buckets exist, or that they exist and are private. It
**cannot be distinguished without the secret key**, which is why §5 lists bucket
creation as an owner action.

## 5. Not verified, and why

| Item | Status | Reason |
|---|---|---|
| Staging database identity | **NOT VERIFIED** | no `DATABASE_URL` supplied; needs `SELECT current_database()` |
| Staging buckets exist | **NOT VERIFIED** | needs the secret key; anon list is `[]` either way |
| Storage upload/sign/download | **NOT VERIFIED** | needs the secret key; a synthetic object would be uploaded and deleted |
| `ENVIRONMENT=staging` boots | **PARTIAL** | `Settings` constructed against the staging ref; `staging_isolation_problems()` empty. **No server started** |
| Migrations | **NOT RUN** | deliberately withheld until the database is proven |

## 6. Gap found — the guard would not have caught the earlier mistake

The most important finding, and a real weakness in code I wrote in P2A.

`Settings.staging_isolation_problems()` checks local addresses, loopback and
production-looking bucket names. It does **not** compare the Supabase ref against
the development one. Verified directly:

```
Settings(environment='staging',
         supabase_url='https://zyrmlnpvylhcpyaoizyz.supabase.co')  # the DEV ref
  → problems: []   # nothing flagged
```

A staging deployment pointed at the **development** project would have passed the
guard silently — exactly the mistake that appeared twice in this conversation.
The failure would surface only when a synthetic staging test created a real user
in the development project.

**Not fixed here, deliberately.** The obvious fix — hardcoding the development
ref and refusing it in staging — is what the P2A brief prohibits ("do not
hardcode infrastructure URLs into application code"). The correct fix is an
**allow-list of staging refs supplied by configuration**, a design decision that
needs the staging ref to be stable and needs its own change.

Recorded for P2B as a required code change. Until then, **project isolation is
enforced by procedure, not by code.**

## 7. Owner actions to finish this milestone

| # | Action | Unblocks |
|---|---|---|
| 1 | **Rotate the `sb_secret_` key** now | everything — the current one is exposed |
| 2 | **Rotate the `GOCSPX-` Google secret** | the Google sign-in provider |
| 3 | Create the staging database (or confirm the project's DB) + give me `DATABASE_URL` | database identity proof |
| 4 | Create the three **private** buckets in the staging project | storage verification |
| 5 | Set the rotated secret locally in `.env.staging` — do not send it | storage verification |

No value needs to be sent to me again. The project ref and publishable key are
public and already verified; everything remaining can be done on your side and
confirmed by running commands locally.

---

# §A — History: the two earlier blocked attempts

Retained for context. Superseded by §1-§7 above.

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

## 7. Stop-condition flags (superseded)

These reflected the state when only a Google OAuth client had been supplied.
**The current values are in §8 below**, after a real staging project was verified.

| Flag | Value at the time | Basis |
|---|---|---|
| `STAGING_SUPABASE_CREATED` | **UNVERIFIED** | not evidenced by the material supplied |
| `STAGING_DATABASE_IDENTIFIED` | **NO** | no staging connection string supplied |
| `STAGING_SUPABASE_ISOLATED` | **NO** | cannot compare a ref that was not supplied |
| `STAGING_AUTH_REACHABLE` | **NO** | no staging project URL to reach |
| `STAGING_STORAGE_VERIFIED` | **NO** | cannot inspect a project not identified |
| `SECRETS_CLEAN` | **YES** | scanner CLEAN; the leaked value is not in the repository |

`STAGING_SUPABASE_CREATED` was recorded as **UNVERIFIED** rather than YES: a
Google Cloud project ID is not evidence of a Supabase project.

---

## 8. Stop-condition flags — current

| Flag | Value | Basis |
|---|---|---|
| `STAGING_SUPABASE_CREATED` | ✅ **YES** | staging ref `vfewnfwyagcxtaxbmwqb`; JWKS served; auth settings returned 200 with the staging key |
| `STAGING_DATABASE_IDENTIFIED` | ❌ **NO** | no `DATABASE_URL` supplied; `SELECT current_database()` not run |
| `STAGING_SUPABASE_ISOLATED` | ✅ **YES** | dev key rejected by staging with 401; distinct ref and key |
| `STAGING_AUTH_REACHABLE` | ✅ **YES** | `/auth/v1/.well-known/jwks.json` 200, ES256; app-derived URL matches |
| `STAGING_STORAGE_VERIFIED` | ⚠️ **PARTIAL** | storage API 200 and anonymous-safe, but bucket existence unconfirmed and no upload/sign/download performed |
| `SECRETS_CLEAN` | ✅ **YES** | scanner CLEAN on all scopes; no pasted value is in the repository or its history |

**Four of six proven. The two that remain both need the rotated secret key or a
database connection string — neither of which should be sent to me.**

### Rotated-key caveat

Even once the key is rotated and storage is verified, the isolation proof in §3
should be re-run against the **new** project's keys, because §3 used the key that
is now being rotated. The project ref and URL will not change, so the *project*
isolation finding stands; only the key-level check would need repeating.


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
