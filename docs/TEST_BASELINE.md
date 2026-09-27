# Test Baseline

Executed on **27 September 2026** in a fresh environment (Python 3.13.0 venv from `requirements-dev.txt`, Node 22 with `npm ci` from `apps/web/package-lock.json`, local PostgreSQL 16.14 via Homebrew).

Every number below was produced by the command beside it. Nothing here was skipped to look green: all database-backed tests actually connected to a live PostgreSQL.

## Backend — `apps/api`

### 1. Full test suite — PASS (995 passed, 4 skipped, 0 failed)

```bash
cd apps/api
TEST_DATABASE_URL=postgresql://caprep@127.0.0.1:5432/caprep_audit \
  /tmp/caprep-venv/bin/python -m pytest -o addopts="" -q
# 995 passed, 4 skipped, 37 warnings in 360.21s (0:06:00)
```

- `caprep_audit` was created empty and migrated with `alembic upgrade head` before the run (see §5).
- 4 skips: the `postgres`-marked runtime skips when a specific external dependency in that test is not configured (matches the prior recorded run's "3 skipped" pattern, +1 from newly added tests).

### 2. First run failure diagnosis (environment, NOT code)

The very first attempt in this workspace produced **37 failed / 222 errors**, all traced to:

```
sqlalchemy: ValueError: the greenlet library is required to use this function. No module named 'greenlet'
```

The fresh venv was missing `greenlet` (a SQLAlchemy async dependency not explicitly listed in `requirements*.txt`; it arrived locally via another wheel). Installing `greenlet` and re-running produced the clean 995/4/0 result above. **Classification: B — environment issue.** No code defect was implicated; the same tests then passed unmodified.

Note: this is itself a reproducibility finding — a fresh install from `requirements-dev.txt` does NOT include `greenlet`, so the database suite can fail on a clean machine. See `NEXT_EXECUTION_PLAN.md` P1-7.

### 3. ruff check — FAIL (29 errors)

```bash
ruff check app tests alembic
# Found 29 errors. [*] 7 fixable with the --fix option.
```

Breakdown (by rule): 20× E501 line-too-long (17 in `app/api/v1/studio.py`, plus single lines in `app/services/platform_defaults.py`, `app/main.py`, `tests/integration/test_postgres_studio.py`), 4× UP035/UP007 (typed-annotation modernisation in `alembic/versions/20260926_2002_6c3f7b0a0c13_campus_tools_and_review_state.py`), 2× I001 import-sort, 1× C420 dict-comprehension, plus assorted E501 (102–162 char) variants.

Files affected: `app/api/v1/studio.py` (20), `alembic/versions/..._campus_tools_and_review_state.py` (4–5), `tests/integration/test_postgres_studio.py` (3), `app/services/platform_defaults.py` (1), `app/main.py` (1).

**Classification: A — existing code defect (drift between last lint pass and the newest files). CI would fail today.**

### 4. ruff format --check — FAIL (5 files)

```bash
ruff format --check app tests alembic
# 5 files would be reformatted, 143 files already formatted
```

Files: `app/api/v1/studio.py`, `app/models/user.py`, `app/services/question_history.py`, `tests/integration/test_postgres_studio.py`, `alembic/versions/20260926_2002_6c3f7b0a0c13_campus_tools_and_review_state.py`.

**Classification: A — existing code defect.**

### 5. alembic check (model ↔ migration drift) — PASS

```bash
alembic upgrade head   # on a fresh database — OK
alembic downgrade base # — OK (full down chain executes)
alembic upgrade head   # — OK (up chain re-executes)
alembic check          # → No new upgrade operations detected.
```

- 15 migrations, linear, `0001_initial` → `6c3f7b0a0c13`.
- Single warning: `Computed default on document_pages.search_vector cannot be modified` — known, documented, benign.
- **Failure on the pre-existing dev databases:** `alembic current` on `caprep`/`caprep_test` errors `Can't locate revision identified by '0014_drop_duplicate_queue_index'` — that migration file is absent from this archive. **Classification: A/C — stale workspace state; not a defect in the on-disk chain.** (P0-4.)

### 6. mypy — NOT COUNTED

`mypy app` is `continue-on-error: true` in CI (advisory). It exceeded the session's command window; no result is claimed. Existing state is not a gate.

## Frontend — `apps/web`

### 7. typecheck — PASS

```bash
npm run typecheck   # tsc -b  → exit 0
```

### 8. lint — PASS

```bash
npm run lint        # eslint .  → exit 0
```

### 9. production build — PASS

```bash
VITE_SUPABASE_URL=https://zyrmlnpvylhcpyaoizyz.supabase.co \
VITE_SUPABASE_ANON_KEY=sb_publishable_<publishable-key> \
npm run build       # tsc -b && vite build → ✓ built in 1.61s
```

Per-route chunks emitted (20+ files, largest `index` 406.22 kB / 122.09 kB gzip). Source maps on.

### 10. vitest — FLAKY (plus one deterministic failure)

```bash
npm test            # 248 tests total
```

| Run | Result | Notes |
| --- | --- | --- |
| Run 1 (cold, 90.7 s) | **10 failed / 238 passed** | failures across landing (4), publicPages (1), login (1), libraryPages (1), adminConsole (1), adminAndQuestionDetail (1), checkout (1) |
| Run 2 (warm, 41.9 s) | **2 failed / 246 passed** | only checkout + publicPages "feature grid" |
| Isolated re-run | each individual failing test **passes alone**, EXCEPT checkout |

- **Deterministic (fails in isolation too):** `checkout.test.tsx` → "names the missing environment variables when the API has no gateway keys". Assertion `findByText(/cannot take payments yet/i)` fails because the page shows the API's 503 `detail` ("Payments are not enabled on this deployment.") and only falls back to the "cannot take payments yet" copy when the problem has no `detail` (which the API never sends). **Classification: A — real frontend test/page mismatch.** (P2-1.)
- **Flaky (passes isolated, fails in full parallel run, different set per run):** the other ~8 failures, incl. `publicPages` "does not list video or AI inside the shipped feature grid" (failed in both full runs, passes alone). **Classification: E — test isolation/order/environment.** Root cause not yet isolated. (P1-5.)
- Earlier recorded sessions reported a green frontend run; the current tree is not stable under `npm test`.

## Failure classification summary (per the audit's Phase 3 categories)

| # | Gate | Result | Category |
| --- | --- | --- | --- |
| 1 | pytest (with greenlet installed) | PASS 995/4/0 | — |
| 2 | pytest (fresh venv, no greenlet) | 37F/222E | B — environment |
| 3 | ruff check | FAIL 29 | A — code defect |
| 4 | ruff format --check | FAIL 5 files | A — code defect |
| 5 | alembic check (fresh DB) | PASS | — |
| 5b | alembic on old dev DBs | FAIL | C — stale workspace state |
| 6 | mypy | not counted | advisory in CI |
| 7 | npm typecheck | PASS | — |
| 8 | npm lint | PASS | — |
| 9 | npm build | PASS | — |
| 10 | npm test | FLAKY / 1 deterministic | A + E |

## Golden commands (for re-verification)

```bash
# backend
cd apps/api
TEST_DATABASE_URL=postgresql://caprep@127.0.0.1:5432/caprep_v2_test python -m pytest -o addopts="" -q
ruff check app tests alembic && ruff format --check app tests alembic
alembic upgrade head && alembic check

# frontend
cd apps/web
npm run typecheck && npm run lint && npm test && npm run build
```

Environment issues to avoid on a fresh machine: install `greenlet` before the backend suite (`pip install greenlet`), and unset any stray `DEBUG`/`ENVIRONMENT` shell vars that pydantic-settings would otherwise try to parse (this workspace's shell exported `DEBUG=release`, which broke config loading until unset).