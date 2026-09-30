# Roadmap

## Built and verified

| Area | State |
|---|---|
| Rate limiting | Redis fixed-window, Lua-atomic, per-route limits, fail-closed on payment paths, Lua script under real Redis |
| Auth correctness | Supabase issuance + PostgreSQL RBAC; the `SupabaseAuthAdmin` no-`settings` defect fixed and covered end-to-end |
| Observability | Sentry via lifespan, no-op without a DSN, 11 tests |
| Module structure | All 7 oversized route modules split; every file under `app/api/v1/` below 600 lines (largest 555) |
| Schema | `SET` course level, `study_plans` + `study_plan_items`, `applicable_attempts` `TEXT[]` with GIN, keyset composite indexes — all reversible, `alembic check` clean |
| Pagination | Keyset cursors with an explicit tiebreaker, stable under concurrent inserts, 400 (never 500) on a corrupt cursor |
| Content | Link-first copyright posture, verifier-enforced publishing, OCR fallback with documented failure mode |
| Deployment | Render blueprint, non-root Docker image with the OCR toolchain, 10-check post-deploy smoke test |
| Tests | 1013 fast + 252 PostgreSQL integration, all green; ruff clean; mypy ratchet at 35 |

Every route module split was verified against a byte-diffed OpenAPI document, an
ordered route list, and the real-Postgres suite — the last of which caught two
classes of silent breakage the fast suite structurally cannot see.

## Not built

Stated plainly, because a roadmap that implies otherwise is worse than none:

- **No study-planner API routes.** The schema and models exist and are unused.
- **No staging environment.** No Render project has been provisioned; no
  Supabase staging project exists.
- **No live payment credentials.** Razorpay is integrated and tested against
  doubles; no live key has been configured.
- **No production deploy.** The repository is deploy-ready; it has not been
  deployed.
- **No UI.** The backend is complete for the loops in `01-overview.md`; the
  browser SPA is not built in this repository.

## Next steps, in order

1. **Provision staging on Render** from `infra/render.yaml`, then wire
   `REDIS_URL` by hand on both services (`06-deployment.md`) and run
   `scripts/smoke_test.sh` against it.
2. **Create the staging Supabase project** and set `SUPABASE_URL`,
   `SUPABASE_SECRET_KEY` and `STORAGE_BUCKET` to match. The API refuses
   `rzp_live_` keys in staging, so this cannot be done by accident.
3. **Go live on payments** once staging has exercised a full order → webhook →
   subscription cycle with Razorpay *test* keys.
4. **Build the SPA** against `openapi.json`.
5. **Study planner API routes**, when the feature is scheduled — the schema is
   ready and waiting.
