# CI Failure Analysis

**Milestone:** repository ownership + remote CI · **Run:** `36349686508`
**Commit:** `7537bb5cac34d3aa7dd74e987e08f9fcf7e0f6fe` (merge commit)
**Classification: NOT a code defect. Both failures are CI_CONFIGURATION.**

No code, workflow, schema or test was changed in response to this run. Per the
milestone's Phase 9, this document records the failure and stops.

---

## 1. Run summary

| Field | Value |
|---|---|
| Repository | `https://github.com/Tejas5405/ca-prep-platform` |
| Branch | `main` |
| Workflow | `CI` (`.github/workflows/ci-cd.yml`) |
| Run ID | `36349686508` |
| Commit SHA | `7537bb5cac34d3aa7dd74e987e08f9fcf7e0f6fe` |
| Trigger | push to `main` |
| Overall conclusion | **failure** |

| Job | Status | Failed step |
|---|---|---|
| `Security Scanner` | ✅ **success** | — |
| `API (lint, format, schema, test)` | ❌ **failure** | `Test backend suite` |
| `Web (lint, typecheck, test, build)` | ❌ **failure** | `Test` |
| `Migrations are reversible` | ⏭️ skipped | (depends on `api`) |

**Everything that did run passed**, including the gates that matter most:

- Security scanner: **CLEAN**
- `ruff check`: **pass**
- `ruff format --check`: **pass**
- `alembic upgrade head`: **pass**
- `alembic check` (no drift): **pass**
- `npm ci`, `typecheck`, `eslint`: **pass**

The two failures are in the *test* step of each job, and both have a single
identified cause.

---

## 2. Failure A — `Web` › `Test`

**Error (verbatim):**

```
FAIL src/tests/inbox.test.ts [ src/tests/inbox.test.ts ]
Error: Supabase is not configured. Missing: VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY.
Copy apps/web/.env.example to apps/web/.env.local and fill in the values from the
Supabase dashboard (Project settings -> API keys).
 ❯ src/lib/supabase.ts:55:9
    54|   if (missing.length > 0) {
    55|     throw new Error(
 ❯ src/lib/api.ts:14:1
 ❯ src/lib/inbox.ts:13:1

 Test Files  3 failed | 16 passed (19)
      Tests  190 passed (190)
```

**Failing files:** `src/tests/adminConsole.test.tsx`, `src/tests/landing.test.tsx`,
`src/tests/inbox.test.ts`.

**Reproducible locally:** **yes** — in a clean clone of the pushed commit.

```
$ git clone --depth 1 --branch main https://github.com/Tejas5405/ca-prep-platform.git
$ cd cleanco/apps/web && npm ci && npm test
 Test Files  3 failed | 16 passed (19)
      Tests  190 passed (190)
exit=1
```

### Root cause

`src/lib/supabase.ts` deliberately throws at **module load** when the Supabase
config is missing — the code says why:

> *Fail loudly at module load if the config is missing. The alternative is a
> runtime failure on the first sign-in attempt, which presents as "login does not
> work" with nothing in the console pointing at the cause.*

That design is correct and must not be weakened. The problem is **where the
values come from in CI**.

`ci-cd.yml` sets them on **one step only**:

```yaml
      - name: Build
        run: npm run build
        env:                                    # ← only this step

---

## 3. Failure B — `API` › `Test backend suite`

**Error (verbatim):**

```
_ TestSupabaseProjectIsConsistentEverywhere.test_the_frontend_and_backend_name_the_same_project _

    def test_the_frontend_and_backend_name_the_same_project(self):
        found = self._project_hosts()
>       assert len(found) >= 2, f"expected several sources to compare, got {found}"
E       AssertionError: expected several sources to compare, got
E         {'infra/render.yaml (API service)': 'https://zyrmlnpvylhcpyaoizyz.supabase.co'}
E       assert 1 >= 2

tests/test_infra_contract.py:276: AssertionError

1 failed, 787 passed, 251 skipped, 13 warnings in 3.26s
```

**Reproducible locally:** **yes** — same clean clone:

```
$ cd cleanco/apps/api && python3 -m pytest -q
1 failed, 787 passed, 251 skipped
```

### Root cause

This test enforces a genuinely important invariant: the frontend, the backend
`render.yaml`, and the root `.env.example` must all name the **same** Supabase
project, because a mismatch produces sign-in that succeeds in the browser and a
401 on every API call.

`_project_hosts()` gathers candidates from three places:

| Source | Present in a clean checkout? |
|---|---|
| `infra/render.yaml` (API service) | ✅ committed — the only one found |
| `apps/web/.env.local` | ❌ **gitignored** |
| `apps/web/.env.example` | ✅ committed, but holds a **placeholder** host, filtered out |
| `.env.example` (root) | ✅ committed, but holds a **placeholder** host, filtered out |

So on a clean checkout exactly **one** real source remains, and the test's

---

## 4. Classification summary

| # | Failure | Classification | Confidence |
|---|---|---|---|
| A | `Web` › `Test` — `supabase.ts` throws at import | **CI_CONFIGURATION** | High — reproduced in a clean clone, cause visible in `ci-cd.yml` |
| B | `API` › `Test backend suite` — `_project_hosts()` finds 1 source | **CI_CONFIGURATION** + **CODE** (assertion under-specified) | High — reproduced in a clean clone |

**Ruled out explicitly:**

| Hypothesis | Verdict | Why |
|---|---|---|
| `DEPENDENCY` | ❌ | `npm ci` succeeded; every `apt-get`/`pip install` step passed |
| `ENVIRONMENT` (OS, Python, Node version) | ❌ | Python 3.12, Node 20.19.0 and Linux ran every step that matters; lint, typecheck, migrations and drift all passed |
| `PLATFORM` | ❌ | the same commands pass locally; the difference is a *file's presence*, not the platform |
| `FLAKE` | ❌ | reproduced deterministically in a clean clone |
| `CODE` (product defect) | ❌ | 190/190 web tests and 787 backend tests passed; no behavioural assertion failed |

### The single root cause behind both

**One untracked, gitignored local file — `apps/web/.env.local` — is load-bearing
for the local test suite, and nothing in the repository declares that.**

- The web tests need `VITE_SUPABASE_*` at import time to construct the client.
- The API contract test counts that same file as a second project-URL source.

Local green was therefore partly a property of *this machine*, not of the
repository. That is a real and important finding, and it is why this run was worth
doing: **it is the first evidence that the local suite was not self-contained.**

---

## 5. What a fix would look like — NOT APPLIED

Recorded for your decision. Both options touch either the workflow or a test, and
per the milestone neither may be changed without a targeted instruction.

**Option 1 — supply the values to the test step (CI_CONFIGURATION only).**

```yaml
      - name: Test
        run: npm test
        env:                                    # added
          VITE_SUPABASE_URL: https://zyrmlnpvylhcpyaoizyz.supabase.co
          VITE_SUPABASE_ANON_KEY: sb_publishable_ci_placeholder
```

Fixes A. Does **not** fix B, and arguably papers over it.

**Option 2 — make the tests independent of untracked files (the better fix).**

- Set the two `VITE_SUPABASE_*` values in `vitest.config.ts` via `test.env` (or
  `define`), so the suite is self-contained by construction and a missing
  `.env.local` cannot change any test outcome.
- For B, relax the assertion to what the invariant actually needs: *if* two or more
  real sources are discovered, they must agree. With one source, assert that the
  committed `render.yaml` value is not a placeholder, and report the reduced
  coverage rather than failing.

Option 2 is preferable because it makes the suite reproducible from a clean
checkout — which is what CI *is*. It is, however, a change to a test, so it needs
your explicit go-ahead.

**Deliberately NOT done:** no `verify=False`, no skipped test, no weakened
assertion, no removal of the loud `supabase.ts` guard, no re-run for a green
result.

---

## 6. State at the time of run `36349686508`

Recorded here as history; superseded by the final result at the end of this
document.

```
LOCAL_GREEN       = YES      (1039 passed, 0 failed — on this machine)
CI_CONFIGURED     = YES      (workflow ran; 4 jobs defined)
CI_EXECUTED       = YES      (run 36349686508 completed)
CI_GREEN          = NO       ← 2 of 4 jobs failed
```

`assert len(found) >= 2` fails.

The test file even documents the trap it walked into:

> *`.env.local` is gitignored, so it is absent in CI. Local development is exactly
> where the mismatch is easiest to create, so it is checked when present rather
> than skipped entirely.*

The guard skips the *absent* file, then asserts on the count of what remains — so
a checkout without the untracked file fails. Locally the file exists, giving two
sources, which is why 1039/1039 passed here.

**Classification: `CI_CONFIGURATION`**, with a secondary **`CODE`** component: the
assertion is under-specified. It requires ≥2 *discovered* sources rather than
requiring the sources it does find to *agree*, so it fails on a legitimately
minimal checkout for a reason unrelated to the invariant it protects.

          VITE_SUPABASE_URL: https://…
          VITE_SUPABASE_ANON_KEY: sb_publishable_ci_placeholder
```

The `Test` step at lines 182-183 has **no `env:` block**, so the three test files
that transitively import `src/lib/supabase.ts` (via `api.ts` / `inbox.ts`) throw
during module resolution.

### The detail that matters

`Tests 190 passed (190)` — **zero individual tests failed**. The 3 "failed" entries
are *files* that could not be **loaded**, so the suite never ran those 58 tests.
This is a load-time configuration gap, not 58 behavioural regressions.

Locally the same suite passes 248/248 because `apps/web/.env.local` exists on this
machine. It is gitignored (`apps/web/.gitignore:7`), so CI never has it — the local
green result was **carried by an untracked file**, which is precisely why it did
not transfer.

**Classification: `CI_CONFIGURATION`.** The workflow supplies build-time config to
the build step but not to the test step.

---

# Resolution — commit `2b25ed5` (`fix: make the test suite self-contained for clean CI`)

Both root causes are as diagnosed above. One of them — Issue B — was a genuine
weakness in the test, and the frontend issue was a genuine reproducibility
defect. Both are fixed at the source rather than in the workflow.

## Why the workflow-only fix was rejected

Adding the same `env:` block to the `Test` step would have turned CI green. It
was rejected because it makes the wrong thing true:

```
Clean clone + test.env in the workflow  -> tests pass
Clean clone + no workflow edit           -> tests still fail
```

Green would have depended on a value being threaded through a YAML file rather
than on the repository being self-contained, and the local suite would have
stayed broken for anyone without `.env.local`. It also would not have fixed
Issue B at all, so it would have been a *partial* fix wearing the appearance of
a complete one — the exact failure mode this milestone exists to prevent.

## Issue A — frontend test environment

**Root cause:** `lib/supabase.ts` throws at module load without `VITE_SUPABASE_*`
(by design), and the values came from gitignored `apps/web/.env.local`.

**Fix:** the test runner now supplies them, in `vitest.config.ts`:

```ts
test: {
  env: {
    VITE_SUPABASE_URL: 'https://test-project.supabase.co',
    VITE_SUPABASE_ANON_KEY: 'sb_publishable_vitest_fake_key',
  },
}
```

Chosen deliberately:

- **Fakes, never real.** The ref `test-project` cannot collide with the real
  project, and the key is publishable, not a secret. No credential is committed.
- **Syntactically real**, so `new URL(url).hostname` in `lib/supabase.ts` runs the
  production code path rather than a stub.
- **Test configuration, not runtime.** `lib/supabase.ts` is untouched and its
  loud module-load guard is preserved — that guard is correct and is what makes
  this class of misconfiguration visible instead of silent.
- `supabaseClient.test.ts` still calls `vi.stubEnv` and takes precedence for that
  file, so the test that asserts on the values directly is unaffected.

## Issue B — the infrastructure contract test

**Root cause:** `assert len(found) >= 2` tested an environmental quantity
(how many files happened to exist) instead of the invariant (do the values
agree). It failed on a non-contradictory clean checkout, passed locally only
because of the untracked file, and — worst — would have **passed on two sources
that disagree**.

**Fix:** the rule is now stated in terms of the invariant, extracted to a
module-level `assert_project_urls_consistent` so it can be tested against
synthetic configurations:

| Discovered sources | Behaviour | Rationale |
|---|---|---|
| 0 | **fail** — "no configuration source names a Supabase project" | missing configuration must be loud |

## Clean-checkout verification (mandatory)

A fresh clone of the pushed commit, patched with the fix, with **no
`.env.local` and no ignored files**:

| Step | Result |
|---|---|
| `npm ci` (from lockfile) | ✅ exit 0 |
| `npm test` | ✅ **248 / 248**, 19 files |
| `python3 -m pytest` | ✅ **798 passed**, 251 skipped |
| `check_secrets.py --worktree` | ✅ **CLEAN** |
| tracked `.env` files | only `.env.example` + `apps/web/.env.example` |

The 5-test difference from the developer checkout (803 vs 798) is **not** a
regression. Those five are untracked-file-dependent infra tests that skip
themselves by design when the file is absent:

```
SKIPPED tests/test_infra_contract.py:451: no Supabase database URL in this checkout
SKIPPED tests/test_infra_contract.py:496: no legacy anon JWT in this checkout
SKIPPED tests/test_infra_contract.py:642: no local web env in this checkout
SKIPPED tests/test_infra_contract.py:737: no local .env with a Supabase secret
SKIPPED tests/test_infra_contract.py:773: no local .env with a Supabase secret
```

Nothing fails in either environment. No further hidden local dependency was
found, so no additional workaround was needed.

## Regression results (developer checkout)

| Gate | Result |
|---|---|
| `npm test` (run 1) | ✅ 248 / 248 |
| `npm test` (run 2) | ✅ 248 / 248 — deterministic |
| `npm run typecheck` | ✅ exit 0 |
| `npm run lint` | ✅ exit 0 |
| `npm run build` | ✅ built in 458 ms |
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ 153 files formatted |
| `alembic check` | ✅ No new upgrade operations detected |
| `pytest` Redis **ON** (`127.0.0.1:6379`) | ✅ **803 passed**, 246 skipped |
| `pytest` Redis **OFF** (unreachable `127.0.0.1:6399`) | ✅ **803 passed**, 246 skipped |
| `check_secrets.py --worktree` / `--staged` / `--rev HEAD` | ✅ **CLEAN** ×3 |

No regression in any existing gate. Redis ON/OFF remains deterministic, as the
`conftest.py` isolation work intended.

**Scope check:** 2 files changed — `apps/web/vitest.config.ts` and
`apps/api/tests/test_infra_contract.py`. No application code, no workflow, no
schema, no API contract, no production configuration, no test skipped, no
`verify=False`, no weakened assertion. Pre-existing helpers in the Python file
were verified byte-identical to `HEAD` by AST extraction.

## New CI run — **PASSED**

| Field | Value |
|---|---|
| Run ID | **`36350781773`** |
| Commit | `9e0451f` (docs; contains fix `2b25ed5`) |
| Workflow | `CI` — `.github/workflows/ci-cd.yml` |
| Trigger | push to `main` |
| **Conclusion** | ✅ **success** |

| Job | Result |
|---|---|
| `Security Scanner` | ✅ **success** |
| `Web (lint, typecheck, test, build)` | ✅ **success** |
| `API (lint, format, schema, test)` | ✅ **success** |
| `Migrations are reversible` | ✅ **success** — ran this time, no longer skipped |

Every job passed, including `Migrations are reversible`, which was **skipped** in
run `36349686508` because the `api` job failed. It is now genuinely executed.

This run also confirms on Linux/Python 3.12 that the frontend suite, the backend
suite, the security scan, `ruff`, `alembic upgrade head`, `alembic check` and a
migrations upgrade-then-downgrade cycle all pass **from a clean checkout with no
`.env.local`** — the exact condition that broke the previous run.

## Final result

```
LOCAL_GREEN       = YES    248 web / 803 backend, Redis ON and OFF
CI_CONFIGURED     = YES    4 jobs, all executed
CI_EXECUTED       = YES    run 36350781773 completed
CI_GREEN          = YES    all 4 jobs passed
```

**`CI_GREEN = YES` is now genuinely proven** by an actual remote run, not
inferred from local results.

The first remote run (`36349686508`, red) and this one (`36350781773`, green)
differ by two files: `apps/web/vitest.config.ts` and
`apps/api/tests/test_infra_contract.py`. The workflow is byte-for-byte unchanged
across both runs, so the green result comes from the repository becoming
self-contained rather than from CI being configured differently.

Not done, per the stop condition: no deployment, no staging, no Razorpay, no
Supabase production change, no production database change, no new features.

| 1 | **pass** | nothing to disagree with; the clean-clone case |
| ≥2, all agreeing | **pass** | the invariant holds |
| ≥2, any conflict | **fail** — "Supabase project URLs disagree" | the mistake the test exists to catch |

**Ten new tests** cover: zero sources, one source, two matching, trailing-slash
agreement, two conflicting, three sources with one conflict, placeholder
recognition, a real host not being treated as a placeholder, placeholder-only
configuration yielding no sources, and a guard that discovery still finds the
committed `render.yaml` value.

The old assertion could not have tested any of this: from a normal checkout it
executes exactly one path. Security intent is preserved — conflicting values
still fail, and a checkout naming no project now fails **for the correct
reason** rather than by accident.
