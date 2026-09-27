# Repository Ownership and CI Status

**Milestone:** repository ownership + remote CI + staging architecture
**Baseline commit:** `49adb42` · **Assessed:** 2026-09-28

This is the factual report for Phases 1–3: what the repository's remote status
actually is, what the pre-push security gate found, and what CI can and cannot
claim today.

---

## 1. Phase 1 — Repository remote

### Result: `REMOTE_REQUIRED`

```
$ git remote -v
(no output)
```

| Check | Result |
|---|---|
| `git remote -v` | **empty — no remote configured** |
| `git branch -vv` | `* main 49adb42 …` — **no upstream tracking branch** |
| `git status` | clean |
| `git log --oneline --decorate -10` | 10 commits, all on `main`, `HEAD -> main` |
| Repository identity | **cannot be verified** — there is no remote to identify it against |
| Permissions | **cannot be verified** — no remote, no token, no access |
| Remote URL | **unknown** |

### What was deliberately not done

| Not done | Why |
|---|---|
| A remote was not created | `git remote add` with a guessed URL would push this repository — including its full history and commit messages — to a destination nobody chose. Guessing is not an acceptable substitute for asking. |
| Nothing was pushed | there is nowhere to push to |
| History was not rewritten | no authorisation, and no reason |
| No other checkout was modified | the sibling `CA Version 1` tree is not this repository's to change |

### Owner action required

```bash
cd "/Users/tejasraykar/Downloads/CA Version 2/ca-prep-platform"
git remote add origin <URL-SUPPLIED-BY-OWNER>

---

## 2. Phase 2 — Pre-push security gate

All four scans were run. **All CLEAN.**

| Scan | Result | Detail |
|---|---|---|
| `check_secrets.py --worktree` | **CLEAN** | 17 allow-listed fake fixtures, **0 unexpected matches** |
| `check_secrets.py --staged` | **CLEAN** | 0 unexpected matches |
| `check_secrets.py --rev HEAD` | **CLEAN** | 0 unexpected matches |
| `check_secrets.py --rev <root commit>` | **CLEAN** | 11 allow-listed fixtures, 0 unexpected matches — the whole reachable history |

### Tracked-file audit

```
$ git ls-files | grep -iE '\.env|credential|secret|\.pem$|\.key$|id_rsa|\.p12$'
.env.example
apps/web/.env.example
scripts/check_secrets.py
```

Only **templates** and the scanner itself. No `.env`, no key material, no
credentials file, no certificate.

`.gitignore` ignores `.env` and `.env.*` with a single negation for
`!.env.example` — so a real `.env` cannot be committed by accident while the
templates stay reviewable.

### No historical credentials found

The full-history scan covers every commit reachable from `HEAD` back to the root
commit. It found no historical credential. **No history rewrite is needed, and none
was performed.**

---

## 3. Phase 3 — Remote CI

### Result: `BLOCKED_EXTERNAL`

| Requirement | Status | Reason |
|---|---|---|
| `LOCAL_GREEN` | **YES** | all gates verified locally at `49adb42` |
| `CI_CONFIGURED` | **YES** | `.github/workflows/ci-cd.yml` defines 4 jobs |
| `CI_EXECUTED` | **NO** | no remote, so the workflow has never run |
| `CI_GREEN` | **UNKNOWN** | cannot be claimed without an execution |

**`CI_GREEN` is not claimed.** Saying "CI is green" without a run would be the
exact failure mode this milestone exists to prevent.

### The workflow that will run

`.github/workflows/ci-cd.yml`, on push and PR to `main`/`develop`:

| Job | Needs | Steps |
|---|---|---|
| `security` | — | `check_secrets.py --worktree` |
| `api` | `security` | ruff check → ruff format → `alembic upgrade head` → `alembic check` → `pytest -q -rs`, against PostgreSQL 16 + Redis 7 service containers |
| `migrations-are-reversible` | `api` | `upgrade head` → `downgrade base` → `upgrade head` |
| `web` | `security` | `npm ci` → typecheck → eslint → vitest → `npm run build` |

**Change made in this milestone:** both PostgreSQL service containers were renamed
from `caprep_test` to `caprep_v2_test`, per `docs/ENVIRONMENT_ISOLATION.md`. The
service container is ephemeral either way, so this changes no behaviour — it makes
the log line in a failure name an unambiguous database, and it keeps CI subject to
the same naming convention as local runs.

### Why local green is strong but not sufficient

The local run and CI differ in ways that have each caught real defects before:

| Difference | Why it matters |
|---|---|
| OS | macOS vs `ubuntu-latest` — path separators, collation, `ssl` module build |
| Python | 3.13 locally vs **3.12** in CI |
| Locale | affects `ORDER BY` on text, which is why the compose file pins `--locale=C` |
| OCR binaries | present locally via Homebrew; installed explicitly in CI via `apt-get` |
| Start order | CI migrates before testing; a local run may rely on a pre-existing schema |

So the correct next step is exactly what the owner proposed: **push, then read the
actual Actions result.** If it fails, the failure output comes first, before any
code changes.

---

## 4. Summary

| Item | Status |
|---|---|
| Remote configured | ❌ **none** — `REMOTE_REQUIRED` |
| Remote URL | **unknown — must be supplied by the owner** |
| Branch | `main`, 10 commits, no upstream |
| Working tree | clean |
| Pre-push security gate | ✅ **CLEAN** (worktree, staged, HEAD, full history) |
| Tracked credential files | **none** |
| Historical secrets | **none found**; no rewrite needed or performed |
| CI configured | ✅ 4 jobs |
| CI executed | ❌ not possible without a remote |
| CI green | ❌ **not claimed** |
| Local gates | ✅ all green at `49adb42` |

### The single blocking action

> **Connect the repository to its real GitHub remote, then run the existing CI
> workflow and bring the actual Actions result.**

No further local work is required for CI to be able to run. The workflow is
committed, the gates it runs are green locally, and the pre-push security gate is
clean.

git push -u origin main
```

Then `.github/workflows/ci-cd.yml` runs on the push. **Until that push happens, no
CI claim of any kind can be made.**
