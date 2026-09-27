# Staging Environment Reference

**Milestone:** P2A — prepare for an isolated staging environment
**Baseline commit:** `51df3e0` → this document
**Status: nothing deployed.** No value below is a real credential, and no secret
value appears anywhere in this repository.

Every variable name here was read out of the code — `apps/api/app/core/config.py`
for the backend, `apps/web/src/vite-env.d.ts` and `import.meta.env` usage for the
frontend — not assumed. Where a name is *not* a real field, it says so.

---

## 1. Backend / worker / migration variables

Server-side. None of these may reach the browser.

| Variable | Component | Secret? | Required in staging | Browser-visible? | Purpose |
|---|---|:--:|:--:|:--:|---|
| `ENVIRONMENT` | API, worker | no | ✅ | no | `staging` activates isolation guards and non-production CORS strictness |
| `DEBUG` | API | no | ✅ (`false`) | no | Must be `false` outside development |
| `DATABASE_URL` | API, worker, Alembic | **yes** | ✅ | no | Pooled connection to the staging database |
| `DIRECT_DATABASE_URL` | Alembic only | **yes** | ✅ (when pooled) | no | Direct (non-pooler) connection; the pooler cannot hold the migration advisory lock |
| `SUPABASE_URL` | API, worker | no | ✅ | **yes** (also `VITE_SUPABASE_URL`) | Project identity; drives JWKS URL, issuer and audience verification, and storage endpoint |
| `SUPABASE_SECRET_KEY` | API, worker | **yes** | ✅ | no | `sb_secret_…` = Postgres `service_role` with BYPASSRLS; mints signed URLs |
| `SUPABASE_SERVICE_ROLE_KEY` | — | **yes** | no | no | **Legacy alias** for the above, still honoured. CLI use only |
| `SUPABASE_ANON_KEY` | — | no | no | n/a | **Not a `Settings` field.** Referenced only by `infra/render.yaml` and the Supabase CLI; the browser uses `VITE_SUPABASE_ANON_KEY` |
| `REDIS_URL` | API, worker | **yes** | ✅ | no | Job queue, cache, rate-limit counters |
| `STORAGE_BUCKET` | API, worker | no | ✅ | no | Private bucket for uploaded question PDFs |
| `RAZORPAY_KEY_ID` | API | no (public by design) | ⬜ | via backend only | Publishable; the backend supplies it to Checkout |
| `RAZORPAY_KEY_SECRET` | API | **yes** | ⬜ | no | Signs API calls and verifies Checkout responses |
| `RAZORPAY_WEBHOOK_SECRET` | API | **yes** | ⬜ | no | Verifies webhook authenticity |
| `RAZORPAY_BASE_URL` | API | no | optional | no | Default `https://api.razorpay.com/v1`. Overridable for a mock |
| `AI_PROVIDER_API_KEY` | API | **yes** | ⬜ | no | Google Generative Language credential |
| `AI_PROVIDER_MODEL` | API | no | ⬜ | no | Primary model; default `gemini-3.8-flash` |
| `AI_PROVIDER_FALLBACK_MODEL` | API | no | ⬜ | no | **Must differ** from the primary or startup fails |
| `AI_MONTHLY_CEILING_USD` | API | no | ⬜ | no | Hard spend ceiling; there is no unlimited mode |
| `AI_FREE_QUERIES_PER_DAY` | API | no | ⬜ | no | Free-tier daily allowance |
| `CORS_ORIGINS` | API | no | ✅ | no | Explicit allow-list of the staging frontend origin |
| `BOOTSTRAP_ADMIN_EMAILS` | API | no | ⬜ | no | Seeds the first staging administrator; use synthetic addresses |
| `RESEND_API_KEY` | API | **yes** | ⬜ | no | Transactional email |
| `SENTRY_DSN` | API, web | no | ⬜ | web only | Error reporting (DG-5, unconfigured) |
| `POSTHOG_API_KEY` | API, web | no | ⬜ | web only | Product analytics (DG-5, unconfigured) |

⬜ = optional *in principle*, but see `docs/STAGING_DEPLOYMENT_INPUTS.md` for
which ones block a specific verification phase.

## 2. Frontend variables (build-time, inlined into the bundle)

| Variable | Component | Secret? | Required in staging | Browser-visible? | Purpose |
|---|---|:--:|:--:|:--:|---|
| `VITE_SUPABASE_URL` | web | no | ✅ | **yes, by design** | Must equal the API's `SUPABASE_URL` |
| `VITE_SUPABASE_ANON_KEY` | web | no | ✅ | **yes, by design** | Publishable key; the secret key is never in the bundle |
| `VITE_API_BASE_URL` | web | no | ✅ | yes | Absolute staging API origin. Empty ⇒ relative `/api/v1` (local dev proxy) |
| `VITE_APP_ENV` | web | no | ✅ | yes | `staging` shows the auth-error console link, hidden in production |
| `VITE_SITE_URL` | web | no | ⬜ | yes | Absolute origin for canonical/meta links. **Not declared in `vite-env.d.ts`**; defaults to `https://caprep.in` |
| `VITE_SUPPORT_EMAIL` | web | no | ⬜ | yes | Support address in the UI. **Not declared in `vite-env.d.ts`**; defaults to `support@caprep.in` |

**Any `VITE_`-prefixed variable is public**, including in source maps. That is the
one rule this table exists to make unmissable.

### Phase 4 finding — two variables are undeclared

`VITE_SITE_URL` and `VITE_SUPPORT_EMAIL` are read in `src/lib/site.ts` through
`as string | undefined` casts because they are **absent from the
`ImportMetaEnv` interface**. Consequences:

1. A typo in either name is not a build error — it silently falls back to a
   **production** default (`https://caprep.in`, `support@caprep.in`). A staging

---

## 3. Environment matrix

Which variable sets apply where. **No values, only names and classification.**

| Variable | Local | CI | Staging | Production | Client/Server |
|---|:--:|:--:|:--:|:--:|---|
| `ENVIRONMENT` | `development` | `test` | `staging` | `production` | Server |
| `DEBUG` | `true` | unset | `false` | `false` | Server |
| `DATABASE_URL` | ✅ local Postgres | ephemeral service PG16 | ⬜ **needed** | ✅ managed | Server |
| `DIRECT_DATABASE_URL` | unset | set by CI | ⬜ **needed if pooled** | ✅ | Server (Alembic) |
| `SUPABASE_URL` | ✅ dev project | placeholder literal | ⬜ **staging project** | ✅ | **Both** |
| `SUPABASE_SECRET_KEY` | ✅ dev | not set (scanner only) | ⬜ **staging** | ✅ | Server only |
| `REDIS_URL` | ✅ local Redis | service Redis 7 | ⬜ **staging** | ✅ | Server only |
| `STORAGE_BUCKET` | `question-pdfs` | n/a | ⬜ **staging buckets** | ✅ | Server only |
| `RAZORPAY_KEY_ID` | **empty** | not set | ⬜ **TEST keys** | ✅ | Server (passes to Checkout) |
| `RAZORPAY_KEY_SECRET` | **empty** | not set | ⬜ **TEST** | ✅ | Server only |
| `RAZORPAY_WEBHOOK_SECRET` | **empty** | not set | ⬜ **TEST** | ✅ | Server only |
| `AI_PROVIDER_API_KEY` | ✅ dev key | not set | ⬜ **staging key** | ✅ | Server only |
| `CORS_ORIGINS` | `localhost:5173,3000` | unset | ⬜ **staging origin** | ✅ | Server only |
| `VITE_SUPABASE_URL` | dev project | placeholder literal | ⬜ **staging project** | ✅ | **Client (public)** |
| `VITE_SUPABASE_ANON_KEY` | dev publishable | placeholder literal | ⬜ **staging** | ✅ | **Client (public)** |
| `VITE_API_BASE_URL` | unset (dev proxy) | unset | ⬜ **staging API origin** | ✅ | **Client (public)** |
| `VITE_APP_ENV` | `development` | unset | `staging` | `production` | **Client (public)** |

Read across the Staging column: **every row is ⬜.** That is the honest current
state, and it is why nothing can be deployed yet.

## 4. Environment separation: what now prevents a mistake

Two guards were added to `Settings` in this milestone, both tested
(`apps/api/tests/test_staging_guards.py`, 18 cases).

| Guard | Behaviour | Why not a startup crash |
|---|---|---|
| `Settings.staging_isolation_problems()` | **Reports** a staging box whose `DATABASE_URL`, `DIRECT_DATABASE_URL`, `REDIS_URL` or `SUPABASE_URL` is a local/loopback address, or whose `STORAGE_BUCKET` looks like production | A developer machine legitimately uses localhost, so the guard is staging-only; and a misconfigured staging box should boot far enough for `/health` to *say what is wrong* |
| `_staging_must_not_take_live_money` | **Refuses to construct** a staging `Settings` holding a `rzp_live_` Razorpay key | The reverse is safe to leave to the operator |

The payment guard discriminates on the **key id prefix**, not the base URL.
Razorpay serves TEST and LIVE from the same host and tells them apart by
`rzp_test_` vs `rzp_live_`, so a URL-based check would have been either a no-op
or — worse — would have rejected the exact configuration staging is supposed to
use. This was caught during implementation, before it shipped.

Not hardcoded: the checks are structural (loopback, pooler port, name shape), so
they do not encode anyone's infrastructure and cannot drift from it.

## 5. Templates created

| File | Contents |
|---|---|
| `.env.staging.example` | 24 backend/worker/migration variables, placeholders only |
| `apps/web/.env.staging.example` | 6 frontend variables, plus the list of what must never go there |

Both were verified to contain **no** real project ref, key, token or password, and
every name was checked against the actual `Settings` fields and
`ImportMetaEnv` interface rather than assumed.

   deploy that misspells the variable would emit production URLs into its own
   pages, which is how a staging environment ends up mailing a real user.
2. They therefore have no type-level guard, unlike the four declared variables.

**Not changed in this milestone.** Declaring them is a two-line, low-risk fix,
but it is a code change to production frontend typing, and P2A is configuration
only. Recorded here and in `docs/STAGING_DEPLOYMENT_INPUTS.md` as
`CODE_CHANGE_REQUIRED`, to be done in its own focused commit.
