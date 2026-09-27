# Environment Configuration

**Milestone:** P2A-5 — explicit, fail-closed environment selection
**Baseline commit:** `78dccd8`
**The rule this document exists to state:**

> **Staging and production never fall back to the development `.env`.**

---

## 1. The problem this fixed

`Settings.model_config` carried `env_file=".env"` unconditionally. That made the
following sequence possible **with no warning of any kind**:

```text
ENVIRONMENT=staging
      ↓
.env read anyway, because it is hardcoded
      ↓
development DATABASE_URL, development Supabase project
      ↓
the service starts, connects to localhost, /health returns 200
```

Nothing errored, because the file it fell back to was perfectly valid. An operator
could fill in `.env.staging`, set `ENVIRONMENT=staging`, and still be running
against development — and the application would look healthy throughout.

This was found by exercising the staging templates rather than reading them, and it
had to be fixed **before** any staging credential was written to disk.

## 2. How selection works now

The file is chosen by `resolve_env_file()`, from the `ENVIRONMENT` variable. **No
shell state and no `set -a; source` is needed** — the earlier approach put that
burden on the operator and on shell quoting.

| `ENVIRONMENT` | File read | If missing |
|---|---|---|
| unset | `.env` | fine — field defaults apply |
| `development` | `.env` | fine — a contributor can run before creating any file |
| `staging` | `.env.staging` | **startup failure** |
| `production` | `.env.production` | **startup failure** |
| `test` | *none* | fine — CI supplies real variables |
| anything else | *none* | **startup failure** — `Literal` rejects it |

`ENV_FILE` overrides the mapping, for a deployment that wants a differently named
file without a code change.

### Precedence, unchanged

Highest to lowest: **explicit keyword arguments** → **real environment
variables** → **the selected file**. Only the file choice became
environment-driven; the ordering operators rely on is untouched.


## 4. Per-environment summary

### Development

- **Selected by:** `ENVIRONMENT=development`, or leaving it unset
- **File:** `.env` (gitignored, created by the developer)
- **If missing:** works; field defaults point at `localhost`
- **Secrets:** in `.env`, never committed

### Staging

- **Selected by:** `ENVIRONMENT=staging` in the process environment
- **File:** `.env.staging` (gitignored)
- **If missing:** **refuses to start** — never reads `.env`
- **Secrets:** in `.env.staging` on the host, or as real environment variables

`ENVIRONMENT` must be set by the **process**, not by the file. The file is chosen
*before* it is read, so a file declaring `ENVIRONMENT=staging` cannot select
itself — the safer design, and the reason the tests set it explicitly.

### CI

- **Selected by:** `ENVIRONMENT=test` (already set in `ci-cd.yml`)
- **File:** none
- **If missing:** n/a — no file is read
- **Secrets:** CI-provided variables; the web build uses documented placeholders

### Production

- **Selected by:** `ENVIRONMENT=production` (set literally in `infra/render.yaml`)
- **File:** `.env.production` if present, otherwise real variables only
- **If missing:** **refuses to start**
- **Secrets:** the host's dashboard, via `sync: false` so Render never stores them

A production deployment receives every value as a real environment variable, so it
normally reads no file at all. The `is_file()` check means the file is optional in
practice while the *intent* stays explicit.

**Never commit:** `.env`, `.env.staging`, `.env.production` — only `.example`
templates are tracked.

## 5. The worker

`app/workers/rq_worker.py` has its own `get_settings()` call and is a separate
process, so it is tested separately rather than assumed to follow. It uses the same
`Settings`, so the same rules apply: a staging worker with no `.env.staging` refuses
to start and will not silently use the development database.

## 6. The frontend is a separate system

No `Settings` field is `VITE_`-prefixed, and that is asserted by a test. Vite
inlines anything prefixed `VITE_` into the client bundle, so a `VITE_`-prefixed
secret is a **published** secret. The frontend has its own variables, read at
**build** time:

```text
VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_API_BASE_URL,
VITE_APP_ENV, VITE_SITE_URL, VITE_SUPPORT_EMAIL
```

All are public by design. The secret halves — `SUPABASE_SECRET_KEY`,
`DATABASE_URL`, `REDIS_URL`, `RAZORPAY_*_SECRET`, `AI_PROVIDER_API_KEY` — are
server-side only and must never carry a `VITE_` prefix.

`NEXT_PUBLIC_*` appears **nowhere** in this repository: that is Next.js
convention, and this is React 19 + Vite.

## 7. Regression coverage

`apps/api/tests/test_environment_selection.py` — **29 cases**, each in a `tmp_path`
workspace so the developer's real `.env` is never involved.

| Requirement | Tests |
|---|---|
| development loads development config | 3, including "works with no file at all" |
| staging loads staging config | 1 |
| **staging + `.env` present + `.env.staging` absent → FAIL** | 2, one asserting the error names the file and the reason |
| staging never reads `.env` | 1 |
| CI variables work with no file | 1 |
| production does not fall back | 2 |
| worker follows the same rules | 4 |
| frontend stays separate | 3 |

**Mutation-verified.** Restoring the old behaviour — hardcoding `.env` and
disabling the fail-closed check — makes **9 of these fail**, including the critical
fallback regression. They are not decorative.

Two implementation details that were got wrong first and are now pinned:

- An unset `ENVIRONMENT` must read `.env`, or a contributor who exports nothing
  loses their configuration.
- A typo such as `stagng` raises `ValidationError`, because `environment` is a
  `Literal` — **better** than the quiet fallback originally planned, and asserted
  so it cannot silently regress into something laxer.

## 8. What remains true

- No secret is committed. `check_secrets.py` CLEAN on worktree, staged and `HEAD`.
- `.env.staging` and `.env` are gitignored; only `.example` templates are tracked.
- The staging templates remain shell-loadable, and their placeholders remain
  recognisable as placeholders to the scanner.

## 3. Failure mode

A missing staging file raises `ConfigurationError`, a dedicated type rather than a
bare `RuntimeError`, so startup handling and tests can tell "this deployment is not
configured" from "this deployment crashed". The message names the missing file and
states the reason:

> `ENVIRONMENT=staging requires the configuration file '.env.staging', which does
> not exist in … Refusing to start rather than fall back to a development
> configuration: a staging service reading .env would connect to the development
> database and would look healthy while doing it.`

It is deliberately **not** a `ValidationError`: nothing is invalid, the
configuration *source* is absent, and the fix is to create a file or export
variables.
