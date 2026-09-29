# Current Readiness Gap Audit

**Audit date:** 2026-09-28
**Repository:** `Tejas5405/ca-prep-platform` @ `1da39d1`
**Method:** every status below is backed by a command run against the current
repository. Nothing is inferred from a document, and nothing is carried over from
an earlier report without re-verification.

**This is an audit only. No application code was written, no refactor performed,
no migration touched, and no database connected.**

---

## 0. A finding that changes how the rest of this document must be read

**The Professionalization Plan and the Implementation Blueprint describe a
NestJS/Prisma application. This repository is a FastAPI/SQLAlchemy application.**

| Source | Framework |
|---|---|
| `blueprint_extracted_text.txt` | 44 mentions of NestJS / `@nestjs/*`, Prisma schema, CASL, `@nestjs/throttler` |
| `CA_Preparation_Platform_Implementation_Blueprint.pdf` | same NestJS build order |
| **This repository** | `apps/api/requirements.txt` → `fastapi==0.141.1`, SQLAlchemy, Alembic |

The blueprint's own Phase 0 is *"Turborepo, Node, Python service, Docker Compose,
linting, .env.example, CI skeleton"*; its Phase 1 is *"Prisma schema, NestJS auth,
roles/CASL"*. **This repository has completed the equivalents of both in a
different stack** — Alembic migrations, a role model, an API contract test, a CI
skeleton.

**Consequence:** the plan's "Phase 0" and "Phase 1" are *not* this project's
Phase 0 and Phase 1. Items phrased as plan phases are mapped below to the
**substantive requirement** they describe, and each mapping is stated in the row.
Where an item is a NestJS-specific mechanism it is marked `NOT_APPLICABLE` with
the FastAPI equivalent named — because "no rate limiting" and "no
`@nestjs/throttler`" are one finding, and reporting the second while missing the
first is the error this audit exists to prevent.

---

## 1. Phase 0

| Area | Requirement | Current Evidence | Status | Priority | Exact Remaining Work |
|---|---|---|---|---|---|
| Credentials | No leaked or distributed credentials | `check_secrets` **CLEAN** on worktree, staged, HEAD. `git ls-files` → 0 tracked `.env`. `.env.staging` mode `600`, gitignored, untracked, **0 commits** ever touching it | **DONE** | — | Rotate the two credentials exposed in chat (`sb_secret_` key, staging DB password). Outside the repo; does not change code status |
| Env debris | No archive/environment debris in the repo | `git ls-files` matches for `archive\|debris\|backup\|.bak\|dump.rdb` → **0**. `dump.rdb` exists only in the parent workspace, untracked | **DONE** | — | None |
| Gateway secrets | Gateway/provider secrets not stored in plaintext | `app/services/gateway_config.py` has **no** `encrypt`/`decrypt`/`Fernet`/`cipher` — a secret saved through it is stored as written | **NOT_DONE** | P1 | Encrypt at rest, or store a provider *reference* rather than the secret. Needs a key-management decision first |
| Admin bootstrap | Bootstrap admin requires a **verified** email | `config.py:473 is_bootstrap_admin_email()` matches the **email string alone**. `identity.py:75` grants admin on that match. `email_verified` / `email_confirmed` appear **nowhere** in app code; no test covers it | **NOT_DONE** | **P0** | Carry `email_verified` from the Supabase JWT, require it before the admin grant, test that an unverified address is refused |
| Rate limiting | Per-IP / per-endpoint limits, `429` + `X-RateLimit-*` | `config.py:300` has `rate_limit_per_minute = 100` but **no middleware consumes it** (middleware files: 0). Only 2 ad-hoc `429` returns, in `assistant.py` and `mocks.py` | **PARTIAL** | **P0** | A real Redis-backed limiter wired as middleware; the config field is currently dead. Blueprint also names login 5/15min and admin upload 5/hour |
| Monitoring | Sentry initialised | `sentry_dsn` is a **config field only** (`config.py:248`), with **no `sentry_sdk` call in `app/`**. `sentry-sdk` is in requirements | **NOT_DONE** | P1 | `sentry_sdk.init()` at startup gated on `sentry_dsn`; test that a configured DSN initialises and an unset one does not |


## 2. Phase 1

| Area | Requirement | Current Evidence | Status | Priority | Exact Remaining Work |
|---|---|---|---|---|---|
| Duplicate enum | Single `PointsReason` | **Two live definitions with different members**: `models/enums.py:198` (8: `CHAPTER_COMPLETE`, `MOCK_COMPLETE`, `STREAK_DAY`…) and `services/gamification.py:25` (7 different: `DAILY_LOGIN`, `DOUBT_ANSWERED`…). mypy flags the crossover at `admin.py:1083` | **NOT_DONE** | **P0** | Collapse to one enum. **Behavioural, not cosmetic** — the member sets barely overlap, so points events silently fail to match |
| Types | mypy clean | `[tool.mypy]` **is** configured (`pyproject.toml:84`) with a documented permissive ramp. `mypy app` → **38 errors in 16 files**. Not in CI | **NOT_DONE** | P1 | Fix 38, then add mypy to CI so it cannot regress. The ramp is intentional — do not switch strict on globally |
| `campus.py` | Decompose oversized module | **1531 lines, 38 routes, 42 functions.** Carries a per-file ruff `E501` exemption (`pyproject.toml:57`) | **PARTIAL** | P2 | Large but one coherent domain, tests green, contract-tested. **Readability change, not a defect.** Do last, one route group at a time |
| Dead features | Frozen/disabled features removed | 1 TODO/FIXME marker in app code | **DONE** | — | None material |
| Indexes | Indexes per plan | 88 `index=True`/`Index(` in models; 89 `create_index` in migrations — consistent | **DONE** | — | None. Check specific hot-path indexes only if a query plan justifies it |
| Docs | Consolidate documentation | 37 files in `docs/` | **PARTIAL** | P2 | Consolidate. The staging series alone is 9 files on one blocked milestone — condense to one status + one reference |
| LICENSE | Licence file | **MISSING**; GitHub API reports `license: None`; repo is **public** | **NOT_DONE** | P1 | Add a licence. **The owner must choose** — a legal decision, not an engineering one |
| CHANGELOG | Changelog | **MISSING** | **NOT_DONE** | P2 | Generate from git history at first release |
| CONTRIBUTING | Contribution guide | **MISSING** | **NOT_DONE** | P2 | Write once the workflow is settled |
| SECURITY | Security policy | **MISSING** | **NOT_DONE** | P2 | Write `SECURITY.md`, including the credential-rotation procedure used throughout this project |
| Releases | Tags / releases | **0 tags, 0 releases**, `main` only | **NOT_DONE** | P2 | Tag at first release. Branch protection is a **GitHub setting, not code** — unverifiable from here, needs a manual check |

## 3. Verification gates — all run just now

| Gate | Result | Evidence |
|---|---|---|
| Secret scanner | ✅ **CLEAN ×3** | worktree, staged, HEAD |
| Alembic check | ✅ | `No new upgrade operations detected` |
| Migration head | ✅ | `6c3f7b0a0c13` — matches the expected head |
| Migration reversibility | ✅ | `migrations-are-reversible` job green in CI (upgrade → downgrade) |
| Test-database isolation | ✅ | `tests/test_database_isolation.py` — 9 tests, 19 passed |
| Backend suite | ✅ | **922 passed**, 246 skipped, Redis **ON and OFF** |
| Frontend | ✅ | 248/248, typecheck, lint, build |
| Ruff | ✅ | check + format clean |
| CI on `1da39d1` | ✅ | **all 5 checks green** |
| Staging Supabase separation | ✅ | staging ref present; **dev ref `zyrmlnpvylhcpyaoizyz` absent** from `.env.staging`; mode `600` |
| Staging preflight | ❌ `NOT_PROVEN` | 2 gates PASS (ref isolated, direct host belongs to staging); `current_database()` cannot connect |
| Staging database identity | ❌ `BLOCKED_EXTERNAL` | Direct host IPv6-only, this machine has no IPv6 route (not even `::1`). `DATABASE_URL` (IPv4 pooler) unset |

---

## 4. What is already complete

Do not redo any of this.

- **Secret hygiene.** The strongest area in the repository. A purpose-built
  scanner, CLEAN on all three scopes, gating CI, plus gitignored `600` env files
  that `git add .` refuses.
- **Environment isolation.** Fail-closed selection (`staging`/`production` refuse
  to start without their file), 29 tests, mutation-verified.
- **Migration discipline.** Single head, no drift, reversibility proven in CI on
  every push.
- **Test isolation.** Dedicated suite; the dev database cannot be reached by tests.
- **Authorization + contract.** Role/permission matrix tests and an OpenAPI
  contract test, both green.
- **Staging groundwork.** Project created, isolation proven *behaviourally*
  (staging rejects the dev key by name), preflight gate built and correct.

## 5. What genuinely remains

| # | Item | Why it matters |
|---|---|---|
| 1 | **Bootstrap admin requires a verified email** | Admin is granted on an unverified address string. Anyone who can sign up with the configured address becomes admin. **Highest-severity finding here** |
| 2 | **Duplicate `PointsReason`** | Two enums with disjoint members; mypy proves the mismatch. Points events silently fail to match |
| 3 | **Rate limiting** | The config field is dead code. A `100/min` value nothing enforces is worse than none, because it reads as protection |
| 4 | **mypy 38 errors + CI gate** | Known; mechanical, but must be ratchet-style |
| 5 | **Sentry init** | `sentry_dsn` configured, never initialised — a placebo field |
| 6 | **Gateway secret storage** | Plaintext at rest |
| 7 | **LICENSE** | Public repo, no licence — legally ambiguous |
| 8 | Docs, CHANGELOG, CONTRIBUTING, SECURITY, tags | Housekeeping, P2 |

## 6. What is blocked by external infrastructure

- **Staging database identity, migrations, seed, staging Auth/Storage/health.**
  Blocked on `DATABASE_URL`. The direct host is IPv6-only and this machine has no
  IPv6 route; the pooler is IPv4 and its hostname cannot be derived (Supabase
  wildcards it, and the management API needs the service key).
- **Staging Redis, Razorpay TEST keys, Render, Vercel.** Not provisioned.
- **Branch protection, GitHub secrets, environment reviewers.** Account settings;
  unverifiable from the repository.

## 7. What should NOT be touched


## 8. Recommended execution order

1. **Bootstrap admin email verification** (P0, security) — small, isolated, high value
2. **Collapse `PointsReason`** (P0, correctness) — mypy pinpoints the crossover
3. **Wire rate limiting** (P0) — the field exists; make it real
4. **mypy 38 → 0, then add to CI** (P1) — *after* 1–2, which change signatures
5. **Sentry init + gateway encryption** (P1)
6. **LICENSE** (P1, owner decision) → CHANGELOG / CONTRIBUTING / SECURITY / first tag (P2)
7. **Docs consolidation** (P2)
8. **`campus.py` decomposition** (P2, last, one group at a time)
9. **Staging**, once `DATABASE_URL` exists

Items 1–3 share a property that makes them one milestone: each is a **security
control the code already claims to have** and does not. The config field, the
enum, the email match — all present, all inert. That is a more urgent class of
defect than anything missing outright.

## 9. Files likely to change next milestone

```text
apps/api/app/core/identity.py            # require verified email before admin
apps/api/app/core/security.py            # carry email_verified from the JWT
apps/api/app/models/enums.py             # canonical PointsReason
apps/api/app/services/gamification.py    # import it, delete the duplicate
apps/api/app/repositories/*.py            # signature updates from the enum merge
apps/api/app/main.py                     # Sentry init + rate-limit middleware
apps/api/app/core/rate_limit.py          # NEW
apps/api/tests/test_identity.py          # NEW
apps/api/tests/test_rate_limit.py        # NEW
```

## 10. Tests required for each remaining item

| Item | Test that must exist before it is called done |
|---|---|
| Bootstrap verification | An unverified address matching `BOOTSTRAP_ADMIN_EMAILS` is **refused**; a verified one is granted. Must fail if the check is removed |
| `PointsReason` | A points event using a member in **both** old enums (`QUESTION_CORRECT`) resolves to one value; plus `gamification.PointsReason is models.PointsReason` |
| Rate limiting | N+1 from one IP → `429`; `X-RateLimit-*` present; a different IP unaffected; limits reset after the window. Removing the middleware must fail these |
| mypy | `mypy app` exits 0 in CI; the error count must never increase |
| Sentry | A configured `sentry_dsn` initialises the SDK; an unset one does not and startup still succeeds |
| Gateway encryption | A secret written is not readable in plaintext in the database |

---

## Recommendation

The three P0 items are not missing features. Each is a control the codebase
already *appears* to have — an enum that exists twice, a rate limit configured
but never enforced, an admin grant that trusts an unverified string — and each is
inert in production. That is more dangerous than an absent control, because a
reader of the code will believe it is protected.

They are also small, localised, and provable with mutation-style tests.

```text
NEXT_IMPLEMENTATION_MILESTONE: P0 security controls — verify bootstrap admin email,
collapse the duplicate PointsReason, and enforce the configured rate limit (with
mutation-verified tests for each).
```

The plan is right that no architectural rewrite is needed:

- **Router → service → repository layering.** 22 routers, 13 model modules, clean
  boundaries, contract-tested. Working.
- **The migration chain.** 89 indexes, single head, reversible in CI.
- **Environment-selection work.** Fail-closed by design, mutation-verified.
  Recent, and the tests are load-bearing.
- **`campus.py`.** Large but coherent and green. Splitting it is churn, not
  remediation — and it is the item most likely to introduce a regression.
- **The permission matrix.** Tested and green; a refactor here is pure downside.

