# CI Readiness — Gate Status

Tracks LOCAL_GREEN → CI_CONFIGURED → CI_EXECUTED → CI_GREEN per the CI
baseline milestone. A state is YES only with evidence cited below.

| Metric | Status | Evidence |
|---|---|---|
| LOCAL_GREEN | YES | Backend 998 passed + 1 skipped (`pytest -q -rs`,
  `caprep_rehearse_test`, 54.30s); `ruff check .` clean;
  `ruff format --check .` clean (148 files); `alembic upgrade head` to
  `6c3f7b0a0c13` + `alembic check` no drift; web 248 passed
  (`vitest run`) with `tsc -b`, `eslint .`, `vite build` clean;
  `check_secrets.py --worktree` CLEAN with negative controls verified. |
| CI_CONFIGURED | YES | `.github/workflows/ci-cd.yml` (YAML-validated) runs the
  four gates: `security` → `api` + `web`, then
  `migrations-are-reversible`. `greenlet==3.5.1` pinned in
  `apps/api/requirements-dev.txt`. |
| CI_EXECUTED | NO | No git remote exists in this checkout
  (`git remote -v` is empty), so no remote Actions run could be triggered
  or observed from here. |
| CI_GREEN | NO | Blocked on CI_EXECUTED. |

## Gate detail

### Security — `python scripts/check_secrets.py --worktree` → CLEAN (exit 0)

17 documented allow-list hits, 0 unexpected. Negative controls re-verified
this milestone: planted live-shaped `sb_secret_…` → exit 1
(`FAIL [supabase-secret]`); planted remote DSN with password → exit 1
(`FAIL [db-url-with-password]`). No secrets are printed to logs.

### Backend — plain `pytest -q -rs` → 998 passed, 1 skipped

Fresh database `caprep_rehearse_test`: `alembic upgrade head` from empty
lands on `6c3f7b0a0c13`; `alembic check` → "No new upgrade operations
detected." Ruff and format checks run as `ruff check .` /
`ruff format --check .`. `mypy app` is intentionally NOT a CI gate
(it runs advisory/`continue-on-error` on HEAD and is outside the
milestone's gate list). No `--cov` in CI: `fail_under = 80` predates this
milestone while the suite totals lower, so coverage stays informational.

Skip: `tests/test_infra_contract.py:288` (needs a Supabase remote URL;
absent offline — expected).

### Frontend — 248 passed, 19 files

`npm ci` from the committed lockfile; `npm run typecheck` (`tsc -b`),
`npm run lint` (`eslint .`), `npm test` (`vitest run`), `npm run build`
(`tsc -b && vite build`) — all exit 0.

## What changed

1. `.github/workflows/ci-cd.yml`: new `security` job (scanner, first);
   `api`/`web` gate on it; canonical `alembic check` replaces the
   autogenerate-grep drift check; `TEST_DATABASE_URL` exported;
   backend test step is plain `pytest -q -rs`.
2. `apps/api/requirements-dev.txt`: pinned `greenlet==3.5.1` (async
   engine under `asyncpg` in clean venvs — the CI case).
3. `docs/CI_BASELINE.md`: the eleven inspection answers plus canonical
   gates and local-environment findings (stray `DEBUG=release` export,
   sibling-checkout DB stamps, `pytest.ini` precedence).
4. This file + `docs/CI_EXECUTION_REPORT.md`: rehearsal evidence.
