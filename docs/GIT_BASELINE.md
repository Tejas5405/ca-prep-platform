# Git Baseline — P0-3

**Scope:** give the project its own secure first history. No remote was added, no branch was pushed,
and no CI or deployment was touched.

**Date:** 2026-09-27

---

## 1. Result

| Property | Value |
|----------|-------|
| Repository root | `ca-prep-platform/` (the archived stack one directory up stays outside this repository) |
| Initialised | `git init` — the directory had **no** `.git` before this task, so initialisation was required |
| Branch | `main` |
| First commit | **`4a3090c`** — *chore: establish secure baseline for P0 remediation* |
| Files in first commit | **334** |
| Lines added | 103,832 |
| Tracked tree size | 6.3 MB (incl. 2.7 MB of `docs/verification/` screenshots) |
| `.git` size after commit | 3.7 MB |
| Author identity | `Tejas Raykar <tejasraykar@gmail.com>` (pre-existing Git config; nothing was re-configured) |
| Remote | **none configured** — deliberately, so the baseline cannot be published by accident |
| Working tree after commit | clean (`git status --porcelain` → 0 lines) |

---

## 2. What the first commit contains

| Group | Contents |
|-------|----------|
| Application | `apps/api` (FastAPI service, Alembic chain, tests), `apps/web` (React/Vite client, tests) |
| Infrastructure | `infra/render.yaml`, `.github/workflows/ci-cd.yml`, `scripts/` (dev stack, live verification) |
| Templates | `.env.example`, `apps/web/.env.example` — placeholders only |
| Documentation | The six audit deliverables (`docs/CURRENT_SYSTEM_AUDIT.md`, `TEST_BASELINE.md`, `API_CONTRACT_AUDIT.md`, `SECURITY_AUDIT.md`, `PRODUCTION_READINESS.md`, `NEXT_EXECUTION_PLAN.md`), `docs/architecture/`, `docs/SECURITY_REMEDIATION.md`, `docs/DB_REBASELINE.md`, and `docs/verification/` evidence |

**Deliberately excluded** (and why):

* `.env`, `apps/web/.env.local` and every other env variant — ignored by rule, verified by content
  rather than filename (§4).
* `apps/web/node_modules/`, `apps/web/dist/`, `apps/dist-preview/` — generated output.
* `caprep.db`, `*.dump`, archives — local database artefacts.
* `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, `.DS_Store`.
* The archived NestJS stack (`_archive_v2_nestjs_stack/`, one level above this repository).

---

## 3. Ignore-rule audit

Performed with `git check-ignore` on real paths (not by reading the file):

| Path | Ignored |
|------|---------|
| `.env`, `.env.local`, `.env.production`, `.env.production.local` | YES |
| `apps/api/.env`, `apps/web/.env`, `apps/web/.env.local` | YES |
| `apps/dist-preview/` | YES — **added in this task**; the directory was previously unprotected |
| `apps/web/dist/`, `apps/web/node_modules/` | YES |
| `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/` | YES |
| `.DS_Store` | YES |
| `.env.example`, `apps/web/.env.example` | NO (intentional — templates must stay tracked) |

Two existing tests keep this honest, and both pass:
`test_infra_contract.py::TestSecretsAreNeverCommitted::test_env_files_are_gitignored_but_examples_are_not`
(asserts the *rule lines*, including that `!.env.example` comes **after** `.env.*` so the negation
wins) and `...::test_the_gitignore_does_not_ignore_the_example_templates` (rejects over-broad rules
such as `*` or `*.example`).

---

## 4. Pre-commit secret verification

The commit was gated by two content scans over the **staged set**, both repeated against **`HEAD`**
after the commit.

### 4.1 Live-value scan (strongest check)

Every credential-shaped value is harvested from the git-ignored env files (`.env`,
`apps/web/.env.local`) — including the password segment embedded in a database URL — and searched
for **by content**, so a real credential is caught even where no pattern would recognise it. The
values are passed to the search on stdin and only **file names** are ever printed, so no value
appears on a command line, in a log or in a report. The check is reproducible:

```bash
python scripts/check_secrets.py            # the committed tree (HEAD)
python scripts/check_secrets.py --staged   # what `git commit` would take
python scripts/check_secrets.py --worktree # tracked + untracked files
```

| Check | Result |
|-------|--------|
| Distinct live values searched | **5** |
| Local-dev values deliberately exempted | 1 (see below) |
| Staged files containing a live value | **0** |
| Committed files (`HEAD`) containing a live value | **0** |
| Env files tracked in `HEAD` | `.env.example`, `apps/web/.env.example` only |

**Disclosed exception — the local PostgreSQL credential.** The local development role password is a
documented convention, not a secret: it appears in `.env.example:43`, as the hard-coded default in
`apps/api/app/core/config.py:64`, in `apps/api/tests/test_db_urls.py:21`, as the CI
service-container DSN (`.github/workflows/ci-cd.yml:59,148`) and in the generated
`apps/docs/api/openapi.json:2846`. It only works against a PostgreSQL bound to `localhost`, so it is
exempted from the live-value check **and reported** rather than silently skipped; a DSN pointing at
any other host still fails the scan. This finding exists because the check is content-based — an
earlier pass had omitted `DATABASE_URL` from the harvest markers and was silently incomplete.

### 4.2 Pattern scan (matches masked)

| Pattern | Matches in `HEAD` | Verdict |
|---------|-------------------|---------|
| `sb_secret_…` (12+ char suffix) | 3 | **Intentional fake fixtures** in `apps/api/tests/test_storage.py` (`:453` is a test *function name*; `:490`, `:492` are fake values asserting the secret-key plumbing) |
| `sb_publishable_…` | 2 | `.github/workflows/ci-cd.yml:214` — the real publishable key, **public by design** (the browser bundle needs it); `apps/web/.env.example:18` — `sb_publishable_xxxxxxxxxxxxxxxxxxxxxxxx`, an obviously fake placeholder |
| JWT (3-part), `rzp_live_`, `sk_live_`, `AIza…`, `AKIA…`, PEM private key, GitHub/Slack tokens | 0 | clean |
| `postgres://user:password@` | 6 | All six are the **localhost-only dev credential** at `.env.example:43`, `ci-cd.yml:59,148`, `app/core/config.py:64`, `tests/test_db_urls.py:21` and `openapi.json:2846` — allowed with that written reason, per §4.1 |

**Conclusion: the first commit contains no credential.** Every key-shaped string in the tree is a
publishable key (public by design, or an all-`x` placeholder), a fake test fixture, or the
localhost-only development DSN — and each one is recorded above with its reason rather than filtered
out silently.

### 4.3 The scanner, and how it was proven to work

`scripts/check_secrets.py` (stdlib only) was added by this task, because P0-3 asks for a scanner and
not merely a one-off scan. It runs the §4.1 and §4.2 checks in four modes (`HEAD`, `--rev REF`,
`--staged`, `--worktree`), classifies every match as allowed-with-reason or a failure, and exits
non-zero when anything is unexplained.

It was validated by a **negative control** — the failure path was demonstrated, not assumed:

| Control | Expected | Observed |
|---------|----------|----------|
| Plant a real-shaped `sb_secret_…` value | fail | `FAIL [supabase-secret]`, exit 1 |
| Plant a DSN with a non-placeholder password at `db.example.com` | fail | `FAIL [db-url-with-password]`, exit 1 |
| Remove both, re-scan `--worktree` | clean | `unexpected matches: 0`, exit 0 |
| `--staged` mode | clean | exit 0 |
| `HEAD` mode over 334 files | clean | exit 0 |

Two defects were found **by** that control run and only there: a localhost DSN was mis-classified
because the host follows the `@` and so lies outside the match, and `git grep`'s argument ordering
(`--cached`/`--untracked` must precede the pattern) produced a silent zero-match scan. Matching is
now done in Python, so git's POSIX-ERE limits cannot hide a hit again.

`scripts/` sits outside the repository's ruff gate (that gate runs in `apps/api`, whose config sets
`src = ["app", "tests", "alembic"]`). The scanner's CLI-inherent profile — `T201` (print is this
tool's report interface) and `S603`/`S607` (a single `subprocess.run` call with a fixed `git` argv) —
is suppressed where it occurs, each suppression carrying a written reason (file-level
`# ruff: noqa: T201`; inline `# noqa: S603, S607` on the `git()` helper), so
`uvx ruff@0.14.5 check ../../scripts/check_secrets.py` reports 0 findings. The two pre-existing
scripts in `scripts/` were not touched and still report findings there.

---

## 5. Rules for future commits

1. **Never** commit an env file other than the two templates. If the ignore rules are ever
   restructured, `test_env_files_are_gitignored_but_examples_are_not` is the guard that catches it —
   do not weaken that assertion.
2. Keep the templates placeholder-only: `apps/web/.env.example` uses `sb_publishable_xxxx…`;
   `.env.example` uses `REPLACE_WITH_YOUR_SUPABASE_SECRET_KEY`. Both are obviously fake, so a
   secret scanner stays quiet.
3. `.env.example`'s `SUPABASE_URL` placeholder must stay `https://your-project.supabase.co` — one of
   the two hosts in `test_infra_contract.py`'s `PLACEHOLDER_HOSTS` allow-list. Any other placeholder
   host makes `test_the_frontend_and_backend_name_the_same_project` fail. That happened during this
   task and was fixed by aligning the placeholder — never by editing the test.
4. Run the scanner before any commit that touches configuration. It is the maintained version of the
   two checks above — do not re-inline the heredoc:

   ```bash
   python scripts/check_secrets.py --staged    # the index: exactly what the commit will take
   python scripts/check_secrets.py             # after committing: the tree itself
   ```

   It exits non-zero while any unexplained credential-shaped string is present, so it can be used as
   a `pre-commit` hook or a CI step verbatim. Every allow-list entry carries a written reason, so
   adding one is a deliberate, reviewable act rather than tune-until-green.

5. **History hygiene:** because `.env` was excluded from the very first commit, there is no
   credential anywhere in the Git history to purge — no `filter-repo` / BFG cleanup is required for
   this baseline. A credential added later must be treated as compromised *even if deleted in a
   follow-up commit*, because the object stays in history.
6. No remote exists yet. When one is added, this baseline can be pushed as-is; pushing is a
   deployment-adjacent action and was explicitly out of scope here.

---

## 6. Commit layout

| Commit | Contents | Reason |
|--------|----------|--------|
| 1 — `4a3090c` | Application, infra, templates, audit docs, `SECURITY_REMEDIATION.md`, `DB_REBASELINE.md` | The secure baseline itself |
| 2 — this document + `docs/P0_EXECUTION_REPORT.md` + `scripts/check_secrets.py` + the `SECURITY_REMEDIATION.md` update (§5.1, R-9, R-10) | The evidence reports, the scanner they describe, and the findings the scanner produced | They cite commit 1's hash, so they cannot be inside it |

After commit 2 the working tree is clean, and no remote, CI workflow run or deployment has been
triggered by anything in this task.

---
