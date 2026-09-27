# F-01 — Development database carried a foreign Alembic revision

**Status: FIXED** · **Severity: P1** · **Milestone: P1 defect remediation** · **Reference commit: `a3113af`**

---

## 1. Original state

The development database named by `.env` (`postgresql+psycopg://caprep:***@localhost:5432/caprep`)
would not run Alembic at all:

```
$ alembic current
ERROR [alembic.util.messaging] Can't locate revision identified by '0015_syllabus_classification'
FAILED: Can't locate revision identified by '0015_syllabus_classification'
```

`0015_syllabus_classification` exists **only** in the sibling `CA Version 1` checkout
(`/Users/tejasraykar/Downloads/CA Version 1/ca-prep-platform/apps/api/alembic/versions/20260926_2000_0015_syllabus_classification.py`).
It is not in this repository's migration chain, and no revision in this chain
declares it as a parent. The database was stamped with a revision this codebase
cannot interpret, so the failure was not a missing migration but a **provenance
error**: the database had been created by a different project.

The user-visible symptom recorded during verification was worse than a failed
command — `GET /api/v1/payments/plans` returned **500**, because the ORM
expectations and the actual tables disagreed.

| Property | Value |
|---|---|
| Repository migration head | `6c3f7b0a0c13` (14 files, `0001_initial` → `6c3f7b0a0c13`) |
| `alembic current` on the dev database | `FAILED` — `Can't locate revision identified by '0015_syllabus_classification'` |
| Revision stamped in the database | `0015_syllabus_classification` (foreign) |
| Tables in the dev database | 40 |
| Columns | 298 |

### Proof the database belonged to a different project

The schema itself was the evidence, before any decision was taken:

- The dev database had **no `plans` table at all**, though this repository's
  migrations create one.
- `users` in the database carried `deleted_at`, `created_at`, `updated_at`;
  the ORM in this repository does not declare them for that model.
- The dev database was one of **nine** local `caprep*` databases, several of
  them (`caprep_audit`, `caprep_ci_clean`, `caprep_rebaseline_proof`,
  `caprep_rehearse_test`) created by earlier remediation rehearsals in the
  sibling checkout. They all carried the same foreign stamp.

---

## 2. Database classification — DISPOSABLE, with evidence

The requirement was to determine whether this data had to be preserved **before**
acting. Every row was inspected. Nothing of value was found:

| Table | Rows | Assessment |
|---|---|---|
| `courses` | 3 | seed reference data |
| `subjects` | 16 | seed reference data |
| `chapters` | 35 | seed reference data |
| `questions` | 27 | seed reference data, `source = 'Platform seed content'` |
| `question_options` | 108 | seed reference data |
| `mock_tests` | 3 | seed reference data |
| `exam_sessions` | 3 | seed reference data |
| `subject_components` | 4 | seed reference data |
| `entitlement_definitions` | 11 | platform defaults |
| `plan_entitlements` | 21 | platform defaults |
| `users` | 3 | **1 seed service account + 2 synthetic probe accounts** |
| `payment_orders`, `subscriptions`, `payment_events`, `points_ledger`, `mock_attempts`, `doubts`, `documents`, `ingestion_*` | **0** | no activity of any kind |

The three users were the decisive evidence, and every one of them is synthetic:

```
f9d26cef-…  auth_user_id='seed:system-cont'   content@seed.invalid  CONTENT_MANAGER
6c95ea5e-…  auth_user_id='pb-student-8fe36'   s@pb.invalid          STUDENT
1c2eb395-…  auth_user_id='pb-student-78a73'   s@pb.invalid          STUDENT
```

- `content@seed.invalid` — the seed service account. `seed.py` recreates it.
- `s@pb.invalid` (×2) — created by **my own verification sweep** in the previous
  milestone, the `pb-` prefix meaning playbook. `.invalid` is an RFC 2606 reserved
  TLD that can never receive mail, so no real person is behind either account.


---

## 3. Action taken

**Recreation from this repository's migrations only.** No fabricated migration, no
`alembic stamp`, no edit to migration history, no `drop database`.

```python
# connect to the maintenance database, because a database cannot be renamed
# while a session is connected to it
ALTER DATABASE caprep      RENAME TO caprep_f01_v1_baseline;   -- preserved, not dropped
CREATE DATABASE caprep;                                        -- empty
```

Then, from the repository's own chain:

```
$ alembic upgrade head
INFO  Running upgrade  -> 0001_initial, Initial schema: curriculum, questions, users, progress.
INFO  Running upgrade 0001_initial -> 0002_ingestion, …
…
INFO  Running upgrade 0013_splits_payments_notices -> 6c3f7b0a0c13, campus tools and review state

$ alembic current
6c3f7b0a0c13 (head)

$ alembic check
No new upgrade operations detected.
```

The reference content was then restored with the project's own idempotent seed:

```
$ python3 -m app.seed
INFO seeded: {'courses': 3, 'subjects': 16, 'chapters': 35, 'questions': 27,
              'mocks': 3, 'examSessions': 3}
$ python3 -m app.seed          # again — identical counts, no duplicates
$ python3 -m app.seed --check
INFO all reference content is present
```

### `caprep_test` needed the same treatment

`TEST_DATABASE_URL` points at `caprep_test`, and it carried the same foreign
stamp (`0015_syllabus_classification`). Had only the development database been
repaired, `pytest` would still have been broken, and the F-04 requirement — that
`pytest` be deterministic — would not have been met. It was remediated identically:
renamed to `caprep_test_f01_v1_baseline` (preserved), recreated empty, migrated
to head. No seed: a test database must start empty, or tests assert against
another run's leftovers.

---

## 4. Migration verification

| Check | Command | Result |
|---|---|---|
| Revision | `alembic current` | `6c3f7b0a0c13 (head)` |
| Drift | `alembic check` | `No new upgrade operations detected.` |
| Chain provenance | `alembic upgrade head` log | 14 upgrades, all from this repository; no foreign revision involved |
| Schema created | `information_schema` | **58 public tables, 643 columns** (was 40 tables / 298 columns under the V1 chain) |
| Foreign revision present? | `select version_num from alembic_version` | `6c3f7b0a0c13` — no trace of `0015_syllabus_classification` |

The table count rising from 40 to 58 is the fix made visible: the old database was
missing 18 tables this project actually uses, including `plans` — which is exactly
why `GET /api/v1/payments/plans` returned 500.

### Data preservation reasoning, verified rather than assumed

Row counts were compared between the preserved V1 database and the new one:

| Table | V1 (preserved) | V2 (remediated) | Match |
|---|---|---|---|
| `courses` | 3 | 3 | ✅ |

---

## 5. Final revision

**`6c3f7b0a0c13` (head)** — identical to the repository's canonical head, on both
`caprep` and `caprep_test`.

---

## 6. Test results

| Run | Command | Result |
|---|---|---|
| Full suite **with** the live database | `TEST_DATABASE_URL=…/caprep_test pytest` | **1020 passed, 0 failed, 1 skipped** |
| `alembic current` (dev) | `alembic current` | `6c3f7b0a0c13 (head)` |
| `alembic current` (test) | `alembic current` | `6c3f7b0a0c13 (head)` |
| `alembic check` (dev) | `alembic check` | `No new upgrade operations detected.` |
| `alembic check` (test) | `alembic check` | `No new upgrade operations detected.` |

The 87 database-backed tests that are normally skipped now execute: the run went
from 999 collected to **1020**, and the single remaining skip is an
environment-gated case, not a failure. Zero failures, zero errors.

### Application start and a representative database-backed request

The API was started against the remediated database and asked a question that
requires real database rows:

```
GET /health/db            → 200  {"status":"ok","checks":{"postgres":{"status":"up"}}}
GET /api/v1/search?q=…    → 200, results from the 27 seeded questions
```

The endpoint that returned **500** before the remediation returns **200** after it.

---

## 7. Files changed

| File | Change |
|---|---|
| *(no source file)* | — |
| `docs/F01_DB_REMEDIATION.md` | this document |

The remediation was an **environment** fix. No migration, model, schema or
application file was modified, and none needed to be: the repository was already
correct. That is the outcome worth stating plainly — the defect lived in the
environment, and it was repaired there.

## 8. State changes (full disclosure)

| Action | Reversible? |
|---|---|
| `pg_dump` backup → `/tmp/f01-backup/caprep_pre_remediation.sql` | n/a (additive) |
| `ALTER DATABASE caprep RENAME TO caprep_f01_v1_baseline` | **yes** — `ALTER DATABASE … RENAME TO caprep` |
| `CREATE DATABASE caprep` + `alembic upgrade head` + `app.seed` | yes (re-runnable, idempotent) |
| `ALTER DATABASE caprep_test RENAME TO caprep_test_f01_v1_baseline` | **yes** |
| `CREATE DATABASE caprep_test` + `alembic upgrade head` | yes |
| 2 synthetic `s@pb.invalid` probe users no longer in `caprep` | yes — still present in `caprep_f01_v1_baseline` |

**No production database was touched.** All nine databases live on a local
Homebrew PostgreSQL 16.14 server (`localhost:5432`); the `caprep` role is a local
superuser and no managed/remote host was contacted. Nothing was dropped, and
`DROP DATABASE` appears nowhere in this remediation.

| `subjects` | 16 | 16 | ✅ |
| `chapters` | 35 | 35 | ✅ |
| `questions` | 27 | 27 | ✅ |
| `question_options` | 108 | 108 | ✅ |
| `mock_tests` | 3 | 3 | ✅ |
| `exam_sessions` | 3 | 3 | ✅ |
| `users` | 3 | 1 | **differs — explained below** |

Every row of reference content was recovered identically. The one difference is
`users`: 3 → 1. The two missing rows are the synthetic `s@pb.invalid` probe
accounts created by my own verification sweep in the previous milestone. They are
regenerable at will, carry no personal data, use a reserved non-routable TLD, and
exist in the preserved `caprep_f01_v1_baseline` database if anyone wants them back.
The seed service account `content@seed.invalid` was recreated by the seed.

There were **zero** payment orders, subscriptions, points-ledger entries, mock
attempts, doubts or documents. No credential, no payment, no user-generated
content. This is a disposable development database.

### Why recreation was nevertheless reversible

`app/seed.py` is idempotent **by construction**, and its row identities are
**deterministic**: rows with a natural key are upserted on that key, and rows
without one get `uuid5(NAMESPACE, …)`, so the same content produces the same id
in every environment. The reference content is therefore not just recoverable, it
is *reproducible by construction* — which is what makes recreating the database
safe rather than merely convenient.

Two further safeguards were taken, so that "disposable" was never the only thing
protecting the data:

1. **A full logical backup was taken before anything destructive:**
   `pg_dump --clean --if-exists` → `/tmp/f01-backup/caprep_pre_remediation.sql`
   (189,770 bytes, exit 0, all 40 tables present with their rows).
2. **The old database was renamed, not dropped.** It still exists as
   `caprep_f01_v1_baseline` and can be inspected or restored at any time.
