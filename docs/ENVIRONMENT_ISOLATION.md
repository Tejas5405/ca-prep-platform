# Environment Isolation

**Milestone:** repository ownership + staging readiness · **Baseline commit:** `49adb42`

This document is the isolation contract for this repository. It exists because a
convention written only in a document is a convention that gets forgotten the
first time somebody is in a hurry — so the rule is **also enforced in code** at
`apps/api/tests/integration/_db.py::assert_disposable_database`, and pinned by
`apps/api/tests/test_database_isolation.py`.

---

## 1. The incident that caused this document

During the P1 defect-remediation milestone, a **concurrent agent working in the
sibling `CA Version 1` checkout** recreated the shared database `caprep_test`
from the *V1* migration chain while this repository's suite was running against
it.

The visible result was **163 failing tests**, all of them reporting:

```
sqlalchemy.exc.ProgrammingError: relation "users" does not exist
```

None of those 163 failures indicated a defect in this codebase. The database had
been swapped out from under the run by a different process using a different
schema.

Two things about this are worth stating precisely, because they are the reason the
rule below is a hard guard rather than a convention:

1. **It was silent.** The suite did not report "the database changed". It reported
   163 product-looking failures. Had those been believed, the next action would
   have been "fix the code", which would have been both wrong and destructive.
2. **The reverse accident is worse.** `open_database` issues
   `TRUNCATE … CASCADE` over every table. Two suites sharing one database would
   empty each other's fixtures mid-run. That produces failures that look like
   product bugs, and it destroys whatever the other run had set up.

The general rule: **a shared mutable database is not a shared resource, it is a
shared liability.** Two suites that both truncate it cannot both be right.

---

## 2. Database naming convention

Every database this repository creates is named with this repository's own
prefix, followed by its purpose:

```
caprep_v2_<purpose>
```

| Database | Purpose | Who may write to it |
|---|---|---|
| `caprep` | local development, seeded reference content | developer, by hand |
| `caprep_v2_test` | automated test suite (truncated per test) | the test suite only |
| `caprep_v2_rehearsal` | manual rehearsal / migration drills | one operator at a time |
| `caprep_v2_staging_probe` | staging smoke probes, disposable | the smoke job only |

**Machine-wide allocation, so two checkouts never collide:**

| Checkout | Owns |
|---|---|
| `CA Version 1` | `caprep_v1*` (e.g. `caprep_v1_test`) |
| `CA Version 2` (this repository) | `caprep_v2*` |
| CI | an ephemeral PostgreSQL service container, named `caprep_v2_test` |
| Staging | a dedicated staging instance, `caprep_staging` (see §6) |

The `v2` in the prefix is the **checkout identity**, not the product version. It
exists so that no name is ever ambiguous about which tree owns it.

### Generic names are prohibited

This repository must **never** point its test suite at:

```
caprep_test        caprep        postgres        template0        template1
```

`caprep_test` in particular is the dangerous one: it is the *obvious* name, it is
what every CA checkout reaches for first, and it is therefore the name most likely
to already be in use by something else. It is refused explicitly, by name, so the
error message can explain why rather than merely reporting a prefix mismatch.

---

## 3. Test isolation rule

**The automated suite may only truncate a `caprep_v2_*` database.**

This is enforced in code, not by convention:

```python
# apps/api/tests/integration/_db.py
def assert_disposable_database(url: str) -> str:
    …raise UnsafeDatabaseError unless the name starts with "caprep_v2_"…
```

and it is invoked from `open_database` **before the first `TRUNCATE`**, against
`SELECT current_database()` — the server's own answer, not a string parsed from
the URL. A URL can resolve to a different database through a `search_path`, a
proxy or a rewritten DSN; the only authority on what is about to be emptied is
the server.

The failure is a **hard error, not a skip.** A skip would report green while
silently running no database tests at all — the exact failure mode this project
was already bitten by once, when a missing `asyncpg` driver made the whole
database suite skip and report success.

### Escape hatch

```bash
CAPREP_ALLOW_SHARED_TEST_DB=1
```

Setting this bypasses the name check. It exists for someone running a genuinely
single-purpose database they have confirmed is exclusively theirs. It must be
typed deliberately: it is never set by any script, CI job or dotfile in this
repository, so it cannot happen by accident or by shell-profile inheritance.

### Verified in this repository

| File | What was changed |
|---|---|
| `.github/workflows/ci-cd.yml` | both PostgreSQL services renamed `caprep_test` → `caprep_v2_test` |
| `scripts/dev-stack.sh` | creates `caprep_v2` + `caprep_v2_test`; `test` target points at the latter |
| `apps/api/tests/integration/_db.py` | the `assert_disposable_database` guard |
| `apps/api/tests/test_database_isolation.py` | tests pinning the rule |
| `apps/api/tests/integration/conftest.py`, `apps/api/tests/test_integration_db.py` | documented examples use `caprep_v2_test` |
| `README.md`, `docs/TEST_BASELINE.md` | local instructions use the owned names |

---

## 5. CI database rule

CI uses an **ephemeral PostgreSQL service container**, destroyed with the runner.
It is therefore already isolated by construction — there is nothing persistent to
collide with.

The service is nonetheless **named `caprep_v2_test`**, for two reasons:

- the name travels with the job, so a failing log names an unambiguous database;
- the workflow is checked by the same test suite as everything else, so the
  convention is exercised on every run rather than only locally.

CI must **never** point at a database outside its own service container. There is
no exception for "just running the migration check" — `alembic upgrade head`
against a shared database is a write to someone else's data.

---

## 6. Staging database rule

| Rule | Detail |
|---|---|
| Dedicated instance | staging Postgres is a **separate instance** from any local server and from production |
| Name | `caprep_staging` |
| Credentials | staging-specific; **never** production credentials, in either direction |
| Migrations | `alembic upgrade head` only, applied by the deploy pipeline (`preDeployCommand`), never by an application startup path |
| Data | synthetic only — see `docs/STAGING_DATA_POLICY.md` |
| Never | `alembic downgrade` as an experiment; see `docs/STAGING_ARCHITECTURE.md` |
| Probes | disposable probe databases are `caprep_v2_staging_probe`, dropped after use |

Staging must never be a copy of production. A staging database restored from a
production backup inherits production personal data, which defeats the purpose of
having a staging environment at all.

---

## 7. Prohibition on shared mutable databases

**No two independent actors may write to the same database unless one owns it and
the other is explicitly a guest with no concurrent activity.**

In practice, on this machine:

| Forbidden | Why |
|---|---|
| This suite and the V1 suite sharing `caprep_test` | demonstrated: 163 false failures |
| Two agents truncating one database concurrently | interleaved fixtures; failures that look like product bugs |
| Staging and production on one database | a staging migration would destroy production data |
| A developer session and CI on one database | CI's `TRUNCATE` would empty the developer's data mid-session |
| Treating "it's only local data" as permission | the V1 database carried seeded content that a test run would have destroyed |

The last row is the one worth internalising: *disposable* was true of the V1
database and it was still destroyed. Disposability describes the data, not the
right of some other process to delete it.

---

## 8. Redis isolation

Redis carries the same risk in a milder form, and the P1 milestone found it there:
`app.core.dependencies._redis_client` is a process global, and a client bound to
one event loop is unusable from the next.

| Rule | Detail |
|---|---|
| Database index | local agents use a **non-default Redis database index** (e.g. `redis://localhost:6379/1`) so keys do not interleave |
| Key prefixes | cache and queue keys already carry distinct prefixes |
| Tests | must not depend on ambient Redis state; see `docs/F04_REDIS_TEST_ISOLATION.md` |
| CI | service container, ephemeral |

Redis holds no source-of-truth data — entitlements and points live in PostgreSQL —
so cross-talk degrades performance rather than correctness. That is a mitigation,
not a licence: a rate-limit counter bleeding between two agents still makes
behaviour non-reproducible.

---

## 9. Environment separation

`LOCAL`, `STAGING` and `PRODUCTION` are fully independent. The full matrix,
including which configuration values are public and which are secret, is in
`docs/STAGING_ARCHITECTURE.md`.

The invariant this document adds: **no environment may be reached by a database
name belonging to another.**


---

## 4. Local agent rule

Any agent, script or human working in this checkout:

1. Uses **only** `caprep_v2_*` databases. Never `caprep_test`.
2. Runs the suite with the database named explicitly:

   ```bash
   cd apps/api
   TEST_DATABASE_URL='postgresql://caprep:caprep@127.0.0.1:5432/caprep_v2_test' \
     python3 -m pytest -q
   ```

3. Creates its own database if the one it wants does not exist, rather than
   adopting an existing one whose provenance it cannot confirm:

   ```bash
   createdb caprep_v2_<purpose>
   DATABASE_URL='postgresql://…/caprep_v2_<purpose>' alembic upgrade head
   ```

4. **Does not modify another checkout.** Not its files, not its databases, not its
   processes. The V1 incident happened because two processes shared one database;
   the other half of the rule is that each owns its own.

5. Where concurrent work is genuinely needed, prefer a **separate git worktree**
   with its own `caprep_v2_*` database, so two agents cannot collide on either the
   working tree or the data.
