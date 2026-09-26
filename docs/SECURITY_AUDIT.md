# Security Audit

**Prepared 27 September 2026.** Per the audit mandate: this document names
**which** secrets are at risk and **why**, and **never prints, echoes, or copies a
secret value**. Where a real value was present in the archive it is described by
presence/length/prefix only. No credentials were rotated during this audit and no
unrelated code was changed.

## 1. Secret / configuration files discovered

| File | Present | Contents (structural) | Verdict |
| --- | --- | --- | --- |
| `ca-prep-platform/.env` | **YES — in the archive** | populated: see §6 | **CRITICAL — do not ship, do not commit** |
| `ca-prep-platform/.env.example` | YES | template, placeholders | SAFE to version (intended) |
| `apps/web/.env.local` | **YES — in the archive** | `VITE_*` only (publishable values) | gitignored; publishable by design |
| `apps/web/.env.example` | YES | template, placeholders | SAFE to version (intended) |
| `infra/render.yaml` | YES | secret keys use `sync: false` (no values) | SAFE to version |
| `.github/workflows/ci-cd.yml` | YES | build-time **placeholder** publishable key only | SAFE |
| `.config/`, `.pki/`, `syslibs/`, `tools/`, `uploads/`, `_archive_v2_nestjs_stack/` | YES (workspace root) | runtime/test scaffolding, retired stack | do not package |

## 2. Hardcoded secret locations

A regex sweep of `apps/api/app`, `apps/api/tests`, `apps/web/src`, `scripts`,
`infra`, `docs` found **no hardcoded production secrets**. The only matches are:

- Test fixtures using intentionally fake Razorpay/Supabase values
  (`tests/test_billing.py`, `tests/test_payments_api.py`,
  `tests/integration/test_postgres_*.py`) — safe, asserted as fake.
- Docstrings/comments naming key *formats* (`sb_secret_...`, `eyJ...`) — not values.

The `.env` files are the only place real credentials exist, and they are
gitignored (see §3).

## 3. Git-ignore status

- `.gitignore` (repo root) correctly excludes: `.env`, `.env.*`, `*.local`, then
  negates `!.env.example`. `apps/web/.gitignore` likewise. This is the right
  shape: templates tracked, real env files never.
- **However: there is no `.git` directory anywhere in the archive** and no remote
  is recorded. Two consequences:
  1. Nothing in this archive can prove the `.env` values were never committed or
     shared through a repository (the project's own comments state both Supabase
     keys and the Gemini key previously transited a chat — treat them as
     publicly known).
  2. When git is initialised, the `.env`/`.env.local` files MUST NOT be added.
     Recommended: `git init`, commit the `.gitignore` first, then add the rest,
     and run a secret scan on the first commit.

## 4. Browser-exposed environment variables

From `apps/web/.env.local` / `.env.example` — every `VITE_` name is inlined into
the bundle by Vite, so ONLY these may be exposed:

| Variable | Value class | Exposure risk |
| --- | --- | --- |
| `VITE_SUPABASE_URL` | project URL | public by design (needed by the auth client) |
| `VITE_SUPABASE_ANON_KEY` | publishable key (`sb_publishable…` in the local file) | public by design (anon key; the browser talks to Auth only, never PostgREST/Storage) |
| `VITE_API_BASE_URL` | empty locally (same-origin `/api` proxy); real Render URL in prod | public by design |
| `VITE_SITE_URL` / `VITE_SUPPORT_EMAIL` / `VITE_APP_ENV` | public site config | public by design |

**None of the server secrets are `VITE_`-prefixed** — structurally impossible for
Vite to inline them from `apps/web`, and `apps/api/tests/test_infra_contract.py`
asserts the built bundle contains the publishable key and NOT the secret key.

## 5. Server-only environment variables

These must exist only in the backend environment (Render) / local `.env` and must
never be prefixed `VITE_` or placed in `apps/web/`:

- `SUPABASE_SECRET_KEY` (and its alias `SUPABASE_SERVICE_ROLE_KEY`) — Postgres
  service-role / BYPASS RLS; storage signing.
- `DATABASE_URL` / `DIRECT_DATABASE_URL` — connection strings (may embed passwords).
- `REDIS_URL` — Redis auth/port.
- `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` (key id and webhook secret are
  semi-public but belong server-side per this codebase's model).
- `AI_PROVIDER_API_KEY` — Gemini billing token.
- `RESEND_API_KEY`, `POSTHOG_API_KEY`, `SENTRY_DSN` — optional integrations.
- `FIREBASE_SERVICE_ACCOUNT_JSON` — **stale**: identity moved to Supabase (SA-09);
  the field no longer exists in `app/core/config.py`. The `FIREBASE_PROJECT_ID`
  in the archived `.env` is leftover and should be removed.

## 6. Recommended credential rotation list — ACTION REQUIRED

The archive's `ca-prep-platform/.env` contains **real values** for the following.
Per the project's own comments (and §3.2), all of these transited at least one
chat and must be treated as compromised. **Rotate all of them before any
production Go-Live, in this order:**

1. `SUPABASE_SECRET_KEY` — current opaque secret key (`sb_secret…`). Rotate in
   Supabase → Project Settings → API Keys. This key can read/write every row and
   every storage object.
2. `SUPABASE_SERVICE_ROLE_KEY` — legacy service_role JWT (a fallback the code
   still accepts). Revoke/retire (Supabase retires these at end of 2026 anyway).
3. `AI_PROVIDER_API_KEY` — Gemini key. Revoke in Google AI Studio and issue a new
   one. Confirm first that no artifact in this workspace printed it (none was
   found; the key is referenced by `.env` only).
4. `SUPABASE_ANON_KEY` (legacy JWT form) — the anon key is public by design, but
   **this JWT form predates the publishable-key system** and is not wired into the
   frontend; replace with the modern publishable key if the frontend ever needs
   one, and remove the stale JWT.
5. `DATABASE_URL` / `REDIS_URL` in the archived `.env` are local-development
   values; ensure production values are set directly in Render and never in a
   file that could be archived again.
6. All `RAZORPAY_*`, `RESEND_API_KEY`, `POSTHOG_API_KEY`, `SENTRY_DSN` are
   **empty** in the archive — obtain test-mode Razorpay keys when integrating,
   production keys only on the platform dashboard.

Older keys that may exist in chat history but are not in the archive:
`FIREBASE` service-account material (identity is Supabase now — do not reuse).

## 7. Security risks discovered

| # | Risk | Severity | Status |
| --- | --- | --- | --- |
| S-1 | Real secret values shipped inside the delivered ZIP (`.env`) | **Critical** | Rotation required (§6); remove `.env` from any future handout |
| S-2 | No git repository/history in the delivery — cannot prove secret hygiene | High | `git init`; commit `.gitignore` first; run a secret scanner on the first commit |
| S-3 | `SUPABASE_SERVICE_ROLE_KEY` fallback accepted by the API (`config.py` alias + `supabase_storage._build_headers`) | Medium | Deliberate migration shim; remove once the legacy key is rotated/retired (end-2026) |
| S-4 | Stale Firebase config in the archived `.env` | Low | Remove `FIREBASE_PROJECT_ID` / service-account keys |
| S-5 | `apps/web/.env.local` archived with a real publishable key + project URL | Low | Publishable by design; still remove from the handout for hygiene |
| S-6 | CORS allow-list is enforced at boot in production (empty/`*` refused) | — | Good; keep `CORS_ORIGINS` explicit at deploy time |
| S-7 | Storage/file buckets private, backend-brokered signed URLs; upload validation before URL minting | — | Good; re-verify against the real project before Go-Live |

## 8. Positive controls verified during the audit

- Password-free architecture: no password hashes in PostgreSQL (Supabase Auth
  owns credentials) — confirmed in `app/models/user.py`.
- Role can only be elevated via the Supabase Admin API claim write
  (`app/integrations/supabase_auth.py`); a missing secret key fails the
  promotion **visibly** (`claim_updated`), never silently.
- `user_metadata` is never read for authorization; only `app_metadata`/namespaced
  claim — regression-tested (`tests/test_security.py`).
- Authentication is mandatory everywhere except whitelisted public GETs and the
  HMAC-protected Razorpay webhook — asserted by HTTP tests and the prior live
  RBAC sweep.
- The browser's Supabase client is auth-only (`GoTrueClient`); no PostgREST or
  Storage access from the browser, so there is exactly one authorization boundary.
- Server never trusts client-supplied amounts, user_ids, roles, or payment
  confirmations (verified in `billing.py`, `identity.py`, `permissions.py`,
  `payments.py`).