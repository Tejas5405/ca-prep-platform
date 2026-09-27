# Staging Deployment Inputs

**Milestone:** P2A — prepare for an isolated staging environment
**Status:** audit complete. **Nothing deployed. No provider account created.**

Classification key:

| Label | Meaning |
|---|---|
| **READY** | Already in the repository, verified, needs no input |
| **CODE_CHANGE_REQUIRED** | The repository is missing something; needs a focused change (not done in P2A) |
| **MISSING_OWNER_INPUT** | A decision or a value only the owner can supply |
| **MISSING_EXTERNAL_RESOURCE** | Infrastructure that must be provisioned |

---

## 1. Inputs

### Already in place

| # | Input | Status | Evidence |
|---|---|---|---|
| 1 | GitHub remote | ✅ **READY** | `origin` → `Tejas5405/ca-prep-platform`, `main` tracking |
| 2 | Remote CI, green | ✅ **READY** | runs `36350781773`, `36351190711` — all 4 jobs |
| 3 | Self-contained test suite | ✅ **READY** | 248 web / 821 backend with no `.env.local` |
| 4 | `infra/render.yaml` blueprint | ✅ **READY** | API + worker + Postgres; Redis block commented out |
| 5 | Worker Dockerfile with OCR binaries | ✅ **READY** | installs `tesseract-ocr`, `tesseract-ocr-eng`, `poppler-utils` |
| 6 | Migration chain | ✅ **READY** | head `6c3f7b0a0c13`; `alembic check` clean |
| 7 | `.env.staging.example` | ✅ **READY** | added in P2A; 24 real variable names, placeholders only |
| 8 | `apps/web/.env.staging.example` | ✅ **READY** | added in P2A; 6 real variable names |
| 9 | Staging isolation guards | ✅ **READY** | `Settings.staging_isolation_problems()`, live-money validator |
| 10 | Secret scanner | ✅ **READY** | CLEAN on worktree, staged and `HEAD` |
| 11 | `infra/docker/docker-compose.yml` | ✅ **READY** | local PG + Redis, `noeviction` |

### Requires a code change — deliberately not made in P2A

| # | Input | Status | Why deferred |
|---|---|---|---|
| 12 | Declare `VITE_SITE_URL` / `VITE_SUPPORT_EMAIL` in `ImportMetaEnv` | ⬜ **CODE_CHANGE_REQUIRED** | They are read through `as string \| undefined` casts, so a typo silently falls back to a **production** default (`https://caprep.in`, `support@caprep.in`). Real staging risk, but a change to production frontend typing — its own focused commit |
| 13 | SPA rewrite config (`vercel.json` or equivalent) | ⬜ **CODE_CHANGE_REQUIRED** | A client-routed SPA 404s on deep link and refresh. **Cannot be written until the frontend host is chosen** (input 22) — the config is provider-specific |
| 14 | Expose `staging_isolation_problems()` on `/health` | ⬜ **CODE_CHANGE_REQUIRED** | The guard exists and is tested, but nothing surfaces it at runtime. Deliberately not wired in P2A: `/health` feeds the deploy probe, and changing its response shape is a behaviour change |

### Requires an owner decision

| # | Decision | Status | Notes |
|---|---|---|---|
| 15 | Backend hosting provider + region | ⬜ **MISSING_OWNER_INPUT** | `render.yaml` assumes Render (`singapore`). The worker needs a Docker-capable runtime for OCR |
| 16 | Worker hosting | ⬜ **MISSING_OWNER_INPUT** | Must be a worker-type service, Docker runtime, and **not** Render's native Python runtime (no OCR binaries) |
| 17 | Frontend hosting provider | ⬜ **MISSING_OWNER_INPUT** | `Vercel` is assumed throughout the docs; unconfirmed |
| 18 | Region for all three | ⬜ **MISSING_OWNER_INPUT** | Should be one region; a database in `singapore` and a frontend in `eu` adds latency and a cross-region egress bill |
| 19 | Staging domain names | ⬜ **MISSING_OWNER_INPUT** | Temporary provider URLs are acceptable for a first pass; a real domain is needed for stable CORS and OAuth redirect allow-listing |
| 20 | Observability choice | ⬜ **MISSING_OWNER_INPUT** | No Sentry/PostHog/uptime monitor (DG-5). Needed before Phase 15 |
| 21 | Whether the AI key may be shared with dev | ⬜ **MISSING_OWNER_INPUT** | The recommendation is no; a dedicated staging key is one dashboard step |

### Requires provisioning — the actual blockers

| # | Resource | Status | Blocks |
|---|---|---|---|
| 22 | **Staging Supabase project** | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phases 3, 4, 11, 12, 13, 14 — auth, storage, and the project identity that keeps staging off real users |
| 23 | **Staging database** (inside 22) | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phase 2, 10 |
| 24 | **Staging Auth users** (inside 22) | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phase 3, 12, 13 |
| 25 | **Staging storage buckets**, private (inside 22) | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phase 4 |
| 26 | **Staging Redis** | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phase 5, 10 — the worker cannot start without it |
| 27 | **Razorpay TEST credentials** | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phase 8 entirely |
| 28 | **Staging AI credential** | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phase 7 |
| 29 | Render staging environment | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phases 9, 10, 11 — depends on 15, 18 |
| 30 | Frontend staging project | ⬜ **MISSING_EXTERNAL_RESOURCE** | Phase 11 — depends on 17, 18 |

### Count

| | |
|---|---|
| ✅ READY | **11** |
| ⬜ CODE_CHANGE_REQUIRED | **3** |
| ⬜ MISSING_OWNER_INPUT | **7** |
| ⬜ MISSING_EXTERNAL_RESOURCE | **9** |

## 2. What the repository already guarantees

Worth stating, because it is what makes the remaining work purely provisioning
rather than engineering:

- **A clean clone passes every gate** with no `.env.local` — verified in P2A and
  in CI. The suite is not carrying a hidden local dependency.
- **Migration head is known and reversible** — CI proves upgrade → downgrade →
  upgrade.
- **The worker image carries its OCR binaries** — the silent-degradation failure
  that motivated `apps/api/Dockerfile` cannot recur on a Docker runtime.
- **Staging cannot charge a real card** — a `rzp_live_` key with
  `ENVIRONMENT=staging` refuses to construct, tested.
- **A local/shared staging resource is reported** rather than silently accepted.
- **No secret is in Git** — scanner clean on three scopes, and both new templates
  were checked to contain no real ref, key, token or password.
