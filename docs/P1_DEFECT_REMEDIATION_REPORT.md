# P1 Defect Remediation Report

**Milestone:** P1 defect remediation · **Reference commit:** `a3113af`
**Scope:** F-01 … F-05 from `docs/LIVE_INFRASTRUCTURE_REPORT.md`

---

## Summary

| ID | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| **F-01** | P1 | Development database carried a foreign Alembic revision | **FIXED** | `alembic current` = `6c3f7b0a0c13` (head); `alembic check` = no drift; 1020 passed |
| **F-02** | P1/P2 | JWKS TLS verification failed on stock python.org macOS | **FIXED** | Live JWKS fetch OK with `SSL_CERT_FILE` unset; 2 regression tests |
| **F-03** | P3 | `X-Request-Id` missing on 500s; logs recorded `-` | **FIXED** | Live 500 carries the id; header, access log and error log agree; 6 tests |
| **F-04** | P3 | Two tests changed verdict with ambient Redis | **FIXED** | Redis ON **and** OFF → 1020 passed, 0 failed; 6 tests |
| **F-05** | P3 | AI fallback model identical to primary, so unreachable | **FIXED** | Fallback fires on failure, not on success; 7 tests |

**All five are FIXED.** Two pre-existing conditions of the previous milestone are
recorded below as `BLOCKED_EXTERNAL` (no git remote → no CI; no deployed host),
but they are not defects in this remediation and neither impeded any fix.

Nothing was deferred, nothing was weakened, and no test was disabled or deleted.

---

## F-01 — Development database carried a foreign Alembic revision → **FIXED**

**Original defect.** The database named by `.env` was stamped
`0015_syllabus_classification`, a revision that exists only in the sibling
`CA Version 1` checkout. `alembic current` aborted with *"Can't locate revision"*,
and `GET /api/v1/payments/plans` returned **500**.

**Root cause.** The database had been created by a *different project*. The schema
proved it independently: 40 tables and **no `plans` table at all**, versus 58 tables
in this repository's chain. Not a missing migration — a provenance error.

**Files changed.** *No source file.* This was an environment repair, and the
repository was already correct. See `docs/F01_DB_REMEDIATION.md`.

**Fix.** Classified the database as disposable **with evidence** (every row was
seed data or a synthetic `.invalid` probe account; zero payments, attempts, doubts
or documents). Then, rather than dropping anything:

1. `pg_dump` backup → `/tmp/f01-backup/caprep_pre_remediation.sql` (189,770 bytes)
2. `ALTER DATABASE caprep RENAME TO caprep_f01_v1_baseline` — **preserved, not dropped**
3. `CREATE DATABASE caprep` (empty) → `alembic upgrade head` → `python -m app.seed`
4. Same treatment for `caprep_test`, which carried the same foreign stamp

No fabricated migration, no `alembic stamp`, no edit to migration history, no
`DROP DATABASE`. The old database is still present and restorable.

**Data preservation — verified, not assumed.** Row counts compared against the
preserved database: `courses` 3, `subjects` 16, `chapters` 35, `questions` 27,
`question_options` 108, `mock_tests` 3, `exam_sessions` 3 — **all identical**. Only
`users` differed (3 → 1): the two missing rows were synthetic `s@pb.invalid` probe
accounts created by my own verification sweep, still present in
`caprep_f01_v1_baseline`.

**Regression test.** Not a code defect, so no unit test applies. The standing
protection is the repository's own schema-drift test plus `alembic check`, both now
passing. The durable evidence is the documented, re-runnable recovery procedure.

**Verification.** `alembic current` = `6c3f7b0a0c13 (head)` on both databases;
`alembic check` = "No new upgrade operations detected." on both; schema grew
40 → 58 tables / 298 → 643 columns. Full suite with the database: **1020 passed,
0 failed, 1 skipped**. Application started against the remediated database:
`/health/db` → 200 and `/api/v1/payments/plans` → **200** (was 500).

---

## F-02 — JWKS TLS verification → **FIXED**

**Original defect.** On the stock python.org macOS interpreter, every JWKS fetch
failed with `CERTIFICATE_VERIFY_FAILED`, and the API reported it as
`401 "Invalid authentication token"` — a transport failure masquerading as a
credential rejection.

**Root cause.** `PyJWKClient` uses `urllib` with the interpreter's *default* SSL
context, which on a python.org macOS install has no CA bundle until the user runs
`Install Certificates.command`. The application had no control over its own trust
store, so a developer-machine configuration gap silently became an authentication
verdict. The deployment image was unaffected, so CI would have stayed green and the
defect would have shipped.

**Files changed.** `app/core/security.py`, `requirements.txt`,
`tests/test_security.py`.

**Fix.** Build an explicit `ssl.create_default_context(cafile=certifi.where())` and
pass it to `PyJWKClient`; declare `certifi==2026.5.20` explicitly rather than
relying on it transitively via `httpx`. `create_default_context` is the **strict**
default — `CERT_REQUIRED`, `check_hostname=True` — and the code changes only the CA
bundle path. `verify=False`, `CERT_NONE`, `check_hostname=False` and exception
suppression were all rejected and are documented as rejected.

**Regression tests.** `TestJwksTlsContext` (2 offline tests) assert security
properties, not merely that a context exists: `verify_mode == CERT_REQUIRED`,
`check_hostname is True`, `get_ca_certs()` non-empty, and that the context is built

---

## F-03 — `X-Request-Id` missing on 500s → **FIXED**

**Original defect.** A 500 response had **no** `X-Request-Id` header while its body
claimed *"The request id is in the response headers"*, and every log line recorded
`request_id: "-"`. A 500 produced no access-log line at all.

**Root cause.** Two independent defects. (a) The middleware reset the contextvar in
`finally` *before* writing the access-log line, so every response logged `-`.
(b) Structurally: Starlette runs `@app.exception_handler(Exception)` in
`ServerErrorMiddleware`, which is **outside** user middleware — so an unhandled
exception passed straight through the request-id middleware, which never got to
attach the header. This answers the objective's question directly: yes, the failure
occurs outside the middleware's reach.

**Files changed.** `app/main.py`, `tests/test_request_id_contract.py` (new).

**Fix.** Restructured the middleware into `try/except/else/finally` so the access
line is written while the contextvar is bound and the 500 path logs before
re-raising; the catch-all handler now stamps the header from
`request.state.request_id` and re-binds the contextvar only for the log call.

**Regression tests.** 6 tests covering **200**, **4xx** (401 and 422 paths) and
**500**, asserting the *same* value appears in the header, the access log and the
error log. The 500 test also asserts the body's claim about the headers is true,
making the original contradiction impossible to reintroduce silently.

**Verification (live, against a real 500 provoked by an unreachable database):**

```
HTTP/1.1 500 Internal Server Error
x-request-id: live500probe
{"level":"ERROR","logger":"app.access","request_id":"live500probe","msg":"GET /api/v1/payments/plans -> 500 in 193.3ms"}
{"level":"ERROR","logger":"app.error","request_id":"live500probe","msg":"Unhandled error: (psycopg.OperationalError) …"}
```

200 → `live200`, 401 → `live401`, 500 → `live500probe`. One id, three places, every
status class. No stack leak; the error body is unchanged.

---

## F-04 — Redis-dependent tests → **FIXED**

**Original defect.** The suite read `998 passed / 0 failed` with Redis stopped and
`996 passed / 2 failed` with Redis running.

**Root cause.** (1) `test_health_redis_returns_503_when_unreachable` patched
`app.core.dependencies.get_redis_client`, but `app/api/v1/health.py` binds that name
at **import** time — the patch was a no-op, so the test silently exercised the
*real* client and its verdict was purely a fact about the machine. (2) Because of
that, a real redis-py client was cached in a module global, bound to a `TestClient`
event loop that then closed; the next `TestClient` shutdown raised
`RuntimeError: Event loop is closed` inside `close_clients()`, surfacing at an
arbitrary victim (`test_schema_contract.py::TestSchemaSize::…422`).

Neither test was a legitimate Redis integration test. Test 1 was a unit test of the
degradation path that failed to isolate its dependency; test 2 was a victim of the
leak.

**Files changed.** `tests/conftest.py` (new), `tests/test_redis_isolation.py` (new),
`tests/test_api_contract.py`. **No application source file.**

**Fix.** Patch `app.api.v1.health.get_redis_client` — the name the route actually
resolves — and add an autouse fixture that resets the cached
`_engine`/`_redis_client`/`_session_factory` around every test, so no test can hand a

---

## F-05 — AI fallback model → **FIXED**

**Original defect.** `_FALLBACK_MODEL = "gemini-3.8-flash"` equalled the configured
`AI_PROVIDER_MODEL`, so `if text is None and model != _FALLBACK_MODEL` was never
true on a default deployment. The retry was unreachable code — and would have
retried the identical model against the identical outage if it had been reached.

**Root cause.** A hardcoded constant could not differ from the configured default,
and nothing validated the pair.

**Files changed.** `app/core/config.py`, `app/services/gemini.py`,
`app/api/v1/assistant.py`, `apps/docs/api/openapi.json` (regenerated, +11 lines),
`tests/test_gemini_fallback.py` (new).

**Fix.** The fallback is now configuration (`AI_PROVIDER_FALLBACK_MODEL`), threaded
from the assistant route into the service, and a `model_validator` **rejects an
identical pair at startup** rather than letting an operator believe in redundancy
that does not exist. `None` means "no fallback" — one attempt — not "fall back to
the default". The `fallback_model != model` guard is kept at the call site too, as
defence in depth.

**Regression tests.** 7 offline tests, no network and no billing. They prove both
required halves: **primary failure → fallback attempted → fallback result returned**
(`asked == ["primary-model", "fallback-model"]`), and **primary success → fallback
NOT called** (`asked == ["primary-model"]`). The first would have failed against the
original code.

**Verification.** 7 passed; OpenAPI drift test satisfied; 1020 passed / 0 failed;
ruff clean.

**Stated limitation.** The fallback is **model-level, not provider-level** — both
calls use the same endpoint and the same API key, so it covers a model outage but
not a provider-wide failure. There is no backoff or bounded retry. Provider-level
redundancy is a feature addition, outside this milestone. No expensive model was
introduced; the fallback is unset by default.

---

## Full regression

### Backend

| Gate | Result |
|---|---|
| `ruff check .` | **All checks passed** |
| `ruff format --check .` | **152 files already formatted** |
| `pytest` (with PostgreSQL) | **1020 passed, 0 failed, 1 skipped** |
| `pytest` (no database) | **0 failures** (246 DB tests skipped) |
| `alembic check` | **No new upgrade operations detected.** |
| `alembic current` | `6c3f7b0a0c13 (head)` |

### Frontend

| Gate | Result |
|---|---|
| `npm run typecheck` | exit **0** |
| `npm run lint` | exit **0** |
| `npm test` | exit **0** (248 tests) |
| `npm run build` | exit **0** (built in 357 ms) |

### Security


---

## Disclosed state changes

| Change | Reversible | Notes |
|---|---|---|
| `pg_dump` backup of the pre-remediation database | n/a (additive) | `/tmp/f01-backup/caprep_pre_remediation.sql` |
| `caprep` renamed to `caprep_f01_v1_baseline` + fresh `caprep` created | **yes** | nothing dropped |
| `caprep_test` renamed to `caprep_test_f01_v1_baseline` + fresh `caprep_test` | **yes** | nothing dropped |
| New database `caprep_v2_test` created | yes | used for verification runs (see below) |
| `apps/docs/api/openapi.json` regenerated | **yes** (`git checkout`) | +11 lines, one new field |
| 2 smoke-test users created then **deleted** from the local dev DB | yes | deleted; `users` back to 1 (the seed account) |
| A local user row temporarily promoted to `ADMIN` for the RBAC check | **yes** | row deleted immediately afterwards |
| Redis installed via Homebrew, running on `127.0.0.1:6379` | yes | pre-existing from the previous milestone |
| Temporary `uvicorn` instances on ports 8020/8033/8044 and a `local_auth` JWKS server on 54443 | yes | **all stopped** |

**No production database, no production payment, and no deployed environment was
touched.** No secret was written to a file, a log, or a commit.

### A note on the shared local PostgreSQL

While verifying F-04, a **concurrent process in the sibling `CA Version 1`
checkout** was found running its own test suite against the *same* local PostgreSQL
server. It had recreated `caprep_test` from the V1 migration chain, which made 163
database tests fail with `relation "users" does not exist`. This is a
shared-infrastructure collision between two checkouts on one machine — not a code
defect, not a test-isolation defect, and not caused by anything in this milestone.

Verification runs were therefore performed against `caprep_v2_test`, a database name
unique to this checkout that the other process does not touch. This is recorded in
both `docs/F01_DB_REMEDIATION.md` §8 and `docs/F04_REDIS_TEST_ISOLATION.md` §6 so
the next person is not misled by a `caprep_test` that holds 40 tables again.

---

## Change control

Five focused commits, one per finding, with no unrelated changes mixed in:

| Commit | Contents |
|---|---|
| `fix: remediate development database baseline` | `docs/F01_DB_REMEDIATION.md` |
| `fix: harden JWKS certificate handling` | `security.py`, `requirements.txt`, `TestJwksTlsContext` |
| `fix: preserve request IDs on error responses` | `main.py`, `test_request_id_contract.py` |
| `test: isolate Redis-dependent tests` | `tests/conftest.py`, `test_redis_isolation.py`, `test_api_contract.py` |
| `fix: make AI fallback configuration valid` | `config.py`, `gemini.py`, `assistant.py`, `openapi.json`, `test_gemini_fallback.py` |
| `docs: record P1 defect remediation outcomes` | this report |

**No new framework, no architecture change, no API contract change, no database
business-logic change, and no feature work.** Total production-code change across
all five fixes: **5 files**, all minimal, all in place of behaviour that was already
supposed to work. New tests: **21** across 3 new files, plus 2 added to an existing
file.

---

## Stop condition

F-01 … F-05 are all **FIXED**, each with a regression test where a code defect was
involved and with live evidence where it was not. Nothing was deferred, and nothing
was blocked by an external condition.

**No deployment, production payment, production migration, feature development or
UI work was begun.** The working tree is clean. The next milestone can be determined
from this report.

| Gate | Result |
|---|---|
| `python scripts/check_secrets.py --worktree` | **CLEAN** — 17 allow-listed fake fixtures, **0 unexpected matches** |

### Re-verification of the six required areas

| # | Area | Result |
|---|---|---|
| 1 | **Authentication** | Valid JWT → JWKS (live Supabase, no `SSL_CERT_FILE`) → **200** on `/api/v1/mocks`, `/api/v1/curriculum/subjects`, `/api/v1/search`; garbage token → 401; no token → 401 |
| 2 | **Database health** | `/health/db` → **200** `postgres: up`; `/api/v1/payments/plans` → **200** (was 500) |
| 3 | **Storage** | `/health/storage` → **200** `question-pdfs` |
| 4 | **Redis** | `PING` → `PONG`; `/health/redis` → **200**; suite green with Redis ON **and** OFF |
| 5 | **OCR** | PyMuPDF tier: 51 chars from a text-layer PDF; **Tesseract tier fired** on a scanned page, 27 chars, **confidence 0.9567** |
| 6 | **RBAC** | `/api/v1/admin/analytics` → **200** for a DB-`ADMIN`, **403** for `STUDENT`; 68 admin routes present in the OpenAPI document |

Expensive external tests were not re-run unnecessarily. The only live external call
was the JWKS fetch (free, and directly affected by F-02); **no AI generation call
was made** for F-05, whose tests are hermetic by design.

One observation worth recording, not a defect: after Redis was stopped and
restarted, a long-running API instance's `/health/redis` returned 503 once, then
**200 on the next request** without intervention — a stale pooled connection that
self-healed. Recorded because it is the kind of transient that looks alarming in a
log and deserves to be understood rather than dismissed.

---

## Pre-existing external conditions (unchanged by this milestone)

- **Remote CI: BLOCKED_EXTERNAL.** This checkout has **no git remote**, so there is
  no CI to execute and no deployment to smoke-test. All gates above were run locally
  and are reproducible with the repository's own commands.
- **Razorpay: BLOCKED_EXTERNAL.** Keys remain empty. The graceful-503 behaviour and
  the unsigned-webhook 401 were verified in the previous milestone and are untouched
  here. **No real payment transaction was attempted.**

loop-bound client to the next. Isolation applied at the lifecycle boundary, not by
deleting assertions. `test_queue_boundary.py` was left alone: its `fakeredis` usage
is deliberate, since the original bug was *inside* RQ's job construction, which a
mocked queue would have accepted happily.

**Regression tests.** 6 new tests asserting determinism with in-process fakes,
including the same test in the same process asserting **opposite** outcomes for up
vs down, and a test that the route resolves the patched name.

**Verification — the requirement, literally:**

| Run | Tests | Failures | Skipped |
|---|---|---|---|
| Redis **ON** + PostgreSQL | 1020 | **0** | 1 |
| Redis **OFF** + PostgreSQL | 1020 | **0** | 1 |
| Redis **ON**, no DB | 1020 | **0** | 246 |
| Redis **OFF**, no DB | 1020 | **0** | 246 |

Redis was genuinely stopped (`redis-cli shutdown nosave`, confirmed
`ConnectionRefusedError`) rather than assumed down. Before the fix the Redis-ON row
read `failures=2`.

from exactly `certifi.where()`.

**Verification.** Live fetch from the real Supabase project with `SSL_CERT_FILE`
explicitly unset → `keys = 1, alg = ES256`. certifi 2026.05.20, 119 CA roots.
