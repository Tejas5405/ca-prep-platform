# Environment Isolation

> **Canonical documentation is the numbered set in this directory.** This file is
> retained because a test asserts its existence and its content, and because the
> rule it records caused a real data-loss incident. For everything else, start at
> [01 — Overview](01-overview.md).

This is the isolation contract for this repository. It exists because a
convention written only in a document is a convention that gets forgotten the
first time somebody is in a hurry — so the rule is **also enforced in code** at
`apps/api/tests/integration/_db.py::assert_disposable_database`, and pinned by
`apps/api/tests/test_database_isolation.py`.

## The incident

During the P1 defect-remediation milestone, a **concurrent agent working in the
sibling `CA Version 1` checkout** recreated the shared database `caprep_test`
from the *V1* migration chain while this repository's suite was running against
it.

Two repositories, one database name, no ownership check. The suite truncated and
recreated a database another working process owned.

## The rule

The integration suite **owns only databases named `caprep_v2_<purpose>`**
(`OWNED_PREFIX = "caprep_v2_"`). Anything else is refused before a single row is
touched.

The refusal is not advisory. `assert_disposable_database` raises
`UnsafeDatabaseError`, and it runs **before** truncation — checking afterwards
would be checking a database that is already empty, which is the state that
caused the incident.

`caprep_test` is owned by the V1 checkout and must never be used here. If a test
run appears to need it, the URL is wrong, not the rule.

## How to point the suite at a database

```bash
TEST_DATABASE_URL='postgresql://user@localhost:5432/caprep_v2_test' \
  pytest tests/integration -q
```

`TEST_DATABASE_URL` is preferred over `DATABASE_URL` specifically so an
integration run cannot accidentally point at a database holding real data.
`DATABASE_URL` remains the CI fallback, where the service container is
disposable and its name still matches the prefix.

## Staging is a different concern

This rule is about the **test** database. Staging-resource isolation —
`Settings.staging_isolation_problems()`, which reports a staging box pointing
at a local or shared database, Redis or Supabase project — is a separate
mechanism described in [06 — Deployment](06-deployment.md).
