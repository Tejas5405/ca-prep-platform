# Staging Architecture

**Milestone:** repository ownership + staging readiness · **Baseline commit:** `49adb42`
**Status:** design document. **Nothing in this document has been deployed.**

This describes the *intended* staging topology. It is written before any
infrastructure exists, so the sequence in `docs/STAGING_DEPLOYMENT_PLAN.md` can be
checked against a stated intent rather than against whatever happened to be built.

Related: `docs/ENVIRONMENT_ISOLATION.md` (naming and ownership),
`docs/STAGING_DATA_POLICY.md` (what data may exist),
`docs/PRODUCTION_READINESS_MATRIX.md` (status of every requirement).

---

## 1. Component topology

```
                    ┌──────────────────────────────┐
   Browser ────────▶│  Frontend  (Vercel)          │
                    │  React 19 + TS, static CDN   │
                    └──────────────┬───────────────┘
                                   │ HTTPS, Bearer JWT
                                   ▼
                    ┌──────────────────────────────┐
                    │  Backend   (Render web)      │
                    │  FastAPI, /health, /api/v1   │
                    └──┬────────┬────────┬─────────┘
                       │        │        │
        ┌──────────────┘        │        └────────────────┐
        ▼                       ▼                         ▼
┌────────────────┐   ┌──────────────────┐      ┌────────────────────┐
│ Supabase Auth  │   │ PostgreSQL 16    │      │ Supabase Storage   │
│ JWKS + users   │   │ staging instance │      │ 3 private buckets  │
└────────────────┘   └──────────────────┘      └────────────────────┘
        ▲                       ▲                         ▲
        │  verify iss/aud        │                         │ signed URLs
   ┌────┴───────────────────────┴─────────────────────────┴────┐
   │  RQ Worker  (Render worker, Docker runtime)               │
   │  python -m app.workers.rq_worker                          │
   └────┬──────────────────────────────┬──────────────────────┘
        │                              ▼
        │                     ┌──────────────────┐
        │                     │ Google Gemini    │
        │                     │ grounded answers │
        │                     └──────────────────┘
        ▼                              ▲
┌────────────────┐                     │
│ Redis          │            ┌────────┴───────────┐
│ staging only   │            │ Razorpay — TEST    │
└────────────────┘            └────────────────────┘
```

---

## 2. Component matrix

*Assumed* in the Provider column means the owner has not chosen that provider; it
is recorded as a `DEPLOYMENT_GAP` in §5 rather than invented here.

| # | Component | Provider | Environment | Required variables | Network | Auth | Failure mode | Data sensitivity |
|---|---|---|---|---|---|---|---|---|
| 1 | **Frontend** | Vercel — *assumed* (DG-1) | `caprep-web-staging` | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | public internet; outbound HTTPS to backend + Supabase | none (static assets); user auth via Supabase in the browser | build fails → previous deploy stays live | **public**; the anon key is designed to be public |
| 2 | **Backend API** | Render `type: web` | `caprep-api-staging` | `ENVIRONMENT`, `DEBUG=false`, `DATABASE_URL`, `REDIS_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `STORAGE_BUCKET`, `CORS_ORIGINS`, `AI_PROVIDER_API_KEY` | inbound from CDN; outbound to Supabase, Postgres, Redis, Gemini, Razorpay | Supabase JWT (JWKS, ES256) per request | 5xx → RFC 7807 envelope carrying `X-Request-Id`; `/health` for probes | **high** — holds the Supabase secret key |
| 3 | **Database** | Render managed Postgres 16 — *assumed* (DG-2) | dedicated staging instance, db `caprep_staging` | `DATABASE_URL`, `DIRECT_DATABASE_URL` | private network to backend + worker | SCRAM password in the connection string | connection refused → `/health/db` 503 | **high** — synthetic personal data only |
| 4 | **Authentication** | Supabase Auth | staging project (DG-3) | `SUPABASE_URL` | HTTPS | signed JWT; API verifies `iss`, `aud`, `exp` and signature against the JWKS | bad/expired token → 401; JWKS fetch is certifi-backed (F-02) | **high** — identity provider |
| 5 | **Storage** | Supabase Storage | staging project | `SUPABASE_SECRET_KEY`, `STORAGE_BUCKET` | HTTPS, server-side only | `sb_secret_…` is Postgres `service_role` with `BYPASSRLS` | missing key → `/health/storage` reports `not_configured` (200) | **high** — uploaded PDFs; must never be public |
| 6 | **Redis** | Render Redis — *assumed* (DG-4) | `caprep-redis-staging` | `REDIS_URL` | private network to backend + worker | connection string | unreachable → `/health/redis` 503; enqueue failures caught → `enqueued: false` | **low** — no source of truth |
| 7 | **RQ worker** | Render `type: worker`, **Docker** runtime | `caprep-worker-staging` | as the API, minus CORS | private network | connection strings | OCR failure is *contained*: the text layer is kept and the job still succeeds | **high** — same data access as the API |
| 8 | **OCR** | Tesseract + Poppler inside the worker image | in-process | none (system binaries) | none — local subprocess | n/a | missing binary → silently degrades to PyMuPDF | **low** — derived text |
| 9 | **AI provider** | Google Generative Language | external SaaS | `AI_PROVIDER_API_KEY`, `AI_PROVIDER_MODEL`, optional `AI_PROVIDER_FALLBACK_MODEL` | outbound HTTPS from the backend | API key, redacted from logs | failure → `None` → quotations-only answer; fallback fires only if configured **and different** | **medium** — user query text leaves the system |
| 10 | **Payments** | Razorpay, **TEST mode** | test dashboard only | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | outbound HTTPS; inbound webhook | HMAC signature over the raw body | missing keys → graceful 503 naming the variables; bad signature → 401 | **high** — synthetic only |
| 11 | **Logging** | Render log drain → stdout JSON | both services | none | n/a | n/a | log loss is silent; no alerting wired (DG-5) | **medium** — request ids, paths, error text |
| 12 | **Secrets** | Render dashboard (`sync: false`) | per service | all `sync: false` vars | never in git | provider-held | a missing secret surfaces as a named 503/401 at runtime, not at deploy | **critical** |
| 13 | **CI/CD** | GitHub Actions | per commit | none today | GitHub-hosted runners | GitHub token | a red workflow blocks merge | **n/a** |

### Two relationships worth calling out

---

## 3. Environment separation

Each environment is fully independent. No value is shared in one direction or the
other.

| Concern | LOCAL | STAGING | PRODUCTION |
|---|---|---|---|
| Database | `caprep` (local PG) | `caprep_staging` (staging instance) | production instance |
| Test database | `caprep_v2_test` | `caprep_v2_staging_probe` | **none** |
| Supabase project | dev project | **staging project** | production project |
| Storage buckets | dev buckets | **staging buckets** | production buckets |
| Redis | `localhost:6379` | staging instance | production instance |
| `ENVIRONMENT` | `development` | `staging` | `production` |
| `DEBUG` | `true` | `false` | `false` |
| `CORS_ORIGINS` | `localhost:5173` | staging frontend origin | production frontend origin |
| Razorpay | **unset** | **TEST keys** | live keys (not yet requested) |
| AI key | dev key | staging key, small ceiling | production key |
| Data | seed + synthetic | **synthetic only** | real |

### Public vs secret

**Public — safe in the browser bundle, and only these:**

| Variable | Why it is public |
|---|---|
| `VITE_SUPABASE_URL` | the project URL identifies the project and authorises nothing alone |
| `VITE_SUPABASE_ANON_KEY` | RLS-scoped; the Auth API is the only service it can reach |
| `SUPABASE_URL` | the issuer, not a credential; it ships in the bundle |
| `STORAGE_BUCKET` | a bucket *name*; access still requires a signed URL |
| `ENVIRONMENT`, quota/limit knobs | non-sensitive tuning |

**Secret — server-side only, never in git, never in the bundle:**

| Variable | Consequence of disclosure |
|---|---|
| `SUPABASE_SECRET_KEY` | Postgres `service_role` with `BYPASSRLS` — full data access |
| `DATABASE_URL`, `DIRECT_DATABASE_URL` | database credentials |
| `REDIS_URL` | queue and cache access |
| `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | payment forgery / webhook forgery |
| `AI_PROVIDER_API_KEY` | billed usage |
| `RESEND_API_KEY` | sending mail as the domain |

`tests/test_infra_contract.py` already enforces the browser side of this: exactly
`VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` may appear, nothing matching
`SECRET`/`SERVICE_ROLE`/`PASSWORD`/`PRIVATE_KEY`, and the built bundle is scanned.

**Rules that are not negotiable:**

- Never reuse production credentials in local development.
- Never point staging at a local or production database.
- Never run a migration from a process other than the deploy pipeline.
- Never expose a storage bucket publicly.

---

## 4. Migration strategy for staging

| Stage | Command | Who runs it |
|---|---|---|
| CI | `alembic upgrade head` then `alembic check` | GitHub Actions, ephemeral DB |
| CI | `upgrade head` → `downgrade base` → `upgrade head` | existing `migrations-are-reversible` job |
| Staging deploy | `alembic upgrade head` as `preDeployCommand` | Render, once per deploy |
| Production | **strategy not yet decided — see §4.1** | — |

**CI verifies; the deploy pipeline applies.** Migrations are never run from the
application's startup path: with more than one instance that races, and multiple
workers would each try to apply the same revision.

### 4.1 Rollback philosophy

**`alembic downgrade` is never run against staging or production as an
experiment.** It is a destructive operation whose correctness is only asserted in
CI against a throwaway database.


---

## 5. Deployment gaps

Found by inspecting the repository. Each is recorded rather than filled in, because
inventing provider settings produces configuration that looks authoritative and
has never been executed.

| ID | Gap | Evidence | Impact | Owner action |
|---|---|---|---|---|
| **DG-1** | **No `vercel.json` (or any SPA rewrite config) exists** | `find` for `vercel.json`/`apps/web/vercel.json` returns nothing; only prose references to Vercel in docs | A client-side-routed SPA deployed to a static host 404s on deep links and on browser refresh unless a rewrite sends unknown paths to `index.html` | Confirm the frontend host, then add the rewrite config for it |
| **DG-2** | **Staging database is not provisioned** | `infra/render.yaml` declares `caprep-postgres` for production only; no staging equivalent | Staging has nowhere to run migrations | Provision a staging Postgres instance, database `caprep_staging` |
| **DG-3** | **No separate staging Supabase project** | `SUPABASE_URL` is a single literal in `render.yaml` and `vite.config`/`.env` | Staging would authenticate against production identities, and staging storage would be production storage | Create a staging Supabase project with its own Auth users and buckets |
| **DG-4** | **Redis is commented out of the blueprint** | `infra/render.yaml` lines 153-160: the `caprep-redis` block is commented out with "create it in the dashboard" | `REDIS_URL` is injected `fromService: caprep-redis`, which will fail if the service does not exist | Provision Redis, or the worker cannot start |
| **DG-5** | **No log alerting or uptime monitor** | No Sentry/PostHog/UptimeRobot configuration in the repo; `SENTRY_DSN` and `POSTHOG_API_KEY` appear only as empty `.env.example` entries | A 5xx spike in staging or production is discovered by a user, not by a monitor | Choose a monitor and an error tracker; wire both |
| **DG-6** | **Frontend API base URL is not configured for any environment** | `vite.config.ts` proxies `/api` only in `dev`; there is no `VITE_API_BASE_URL` and no production value for it | The deployed bundle has no way to reach the backend unless the client is origin-relative behind a proxy | Decide proxy-vs-absolute and add the variable |
| **DG-7** | **No CI secrets configured** | `.github/workflows/ci-cd.yml` uses only placeholder Supabase values for the web build | Real CI cannot exercise the live auth path; acceptable, but should be a conscious decision | Optionally add a staging Supabase secret for an integration job |
| **DG-8** | **No staging environment branch or Render environment** | `infra/render.yaml` sets `ENVIRONMENT: production` literally on both services | A staging deploy would report itself as production, which changes CORS and boot strictness | Add a Render `staging` environment with its own env vars |

### What is already in place and needs no work

| Area | Status |
|---|---|
| Backend build / start commands | `pip install -r requirements.txt`; `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2` — correct, and `$PORT` is required by Render |
| Health endpoint | `/health`, mounted at the root (not under `/api/v1`) so probes do not depend on the API version |
| Migration strategy | `preDeployCommand: alembic upgrade head` — runs once per deploy, not in the startup path |
| Worker service type | `type: worker` with `startCommand: python -m app.workers.rq_worker`; Docker runtime so the OCR binaries exist |
| CORS | explicit allow-list; the app refuses to boot in production if empty or `*` |
| Frontend build | `tsc -b && vite build`, identical to what CI runs, so a CI pass means a deployable bundle |
| Environment variable contract | `.env.example` is complete; `.env` is gitignored and untracked |
| Local containers | `infra/docker/docker-compose.yml` for Postgres + Redis, with a deliberate `noeviction` policy |

### Not gaps — deliberate choices

| Item | Why it is not a gap |
|---|---|
| No Dockerfile for the frontend | it is a static bundle; the host serves `dist/` |
| API and worker not containerised locally | `docker-compose.yml` documents this: hot reload and a debugger are worth more than a rebuild cycle for two developers |
| `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_ANON_KEY` in `.env.example` | listed for the Supabase CLI; the application reads `SUPABASE_SECRET_KEY` |
| No `vercel.json` build settings | Vercel infers them from `apps/web`; only the SPA rewrite is genuinely missing (DG-1) |

The important asymmetry: **database rollback is not application rollback.** The
two are independent decisions, and choosing wrongly loses data:

| Situation | Application | Database | Rationale |
|---|---|---|---|
| Bad deploy, schema fine | roll back the app | leave the schema | the forward migration is additive and harmless |
| Bad migration, already applied | roll back the app | leave the schema | reversing the schema can drop data the newer app wrote |
| The migration itself is wrong | roll back the app | **write a new forward migration** | reversing re-applies the error |
| A destructive change shipped | roll back the app | leave the schema | `downgrade` cannot restore dropped columns' data |

**The policy is therefore: roll back the application, never the schema.** Schema
changes are corrected by a new forward migration, and `alembic downgrade` remains a
CI-only verification tool. This is a deliberate policy, not an oversight — the
`migrations-are-reversible` job exists to catch migrations that *cannot* be
reversed, not to authorise reversing them in production.

The production strategy must additionally specify: a verified backup, a **tested
restore**, and who may authorise a forward fix under time pressure. None of that
is decided yet, and it is listed as a blocker in
`docs/PRODUCTION_READINESS_MATRIX.md`.


**The worker must use the Docker runtime, not Render's native Python.** The OCR
fallback shells out to `tesseract-ocr` and `pdftoppm`. Neither exists in the native
runtime, `pip install pytesseract pdf2image` succeeds without them, and the
pipeline is built to *contain* an OCR failure — so scanned PDFs would produce zero
drafts with no error on the job and a healthy-looking worker. This is already
encoded in `infra/render.yaml` and asserted by `tests/test_infra_contract.py`.

**`SUPABASE_URL` is a literal on all three deployable units** and must equal the
frontend's `VITE_SUPABASE_URL` and the project inside `DATABASE_URL`. Drift means
sign-in succeeds in the browser and the API rejects every token with 401 — which
is indistinguishable, from the server side, from a real attack.


Two independent flows, both worth stating because conflating them is how
background work gets silently dropped:

- **Request path (synchronous, latency-sensitive):** browser → frontend → backend
  → Postgres / Supabase Auth / Supabase Storage.
- **Job path (asynchronous, failure-tolerant):** backend enqueues → Redis → RQ
  worker → Postgres + Storage + Tesseract → Gemini.
