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

## 6. State

```
LOCAL_GREEN       = YES      (1039 passed, 0 failed — on this machine)
CI_CONFIGURED     = YES      (workflow ran; 4 jobs defined)
CI_EXECUTED       = YES      (run 36349686508 completed)
CI_GREEN          = NO       ← 2 of 4 jobs failed
```

`CI_GREEN` is **NO**. Local success does not imply CI success — this run is the
proof, and it is why the two were tracked as separate states from the start.

`Migrations are reversible` was **skipped** because it depends on the `api` job, so
that gate remains **unexecuted** rather than passing.

Working tree: clean at the time of writing. No code, workflow, schema or test was
modified.

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
