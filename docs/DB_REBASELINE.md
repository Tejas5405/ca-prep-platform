# Development Database Re-baseline — P0-2

**Scope:** determine the state of the pre-existing development databases and define a *safe*
remediation. No database was dropped, stamped or rewritten. **No migration file was created,
edited or deleted.**

**Date:** 2026-09-27

---

## 1. Findings

### 1.1 Repository head

| Property | Value | Evidence |
|----------|-------|----------|
| On-disk revision files | 14 (`0001_initial` … `0013_splits_payments_notices`, plus `6c3f7b0a0c13`) | `grep -h '^revision' alembic/versions/*.py` |
| Alembic head | `6c3f7b0a0c13` (`20260926_2002_...campus_tools_and_review_state.py`) | revision id in that file is the only one not referenced as a `down_revision` |
| Heads/branches | 1 head, linear chain | full upgrade `→ base →` upgrade succeeded (§3) |

### 1.2 Database stamps

| Database | `alembic_version` | Tables in `public` | Classification |
|----------|-------------------|--------------------|----------------|
| `caprep` (primary local dev) | `0014_drop_duplicate_queue_index` | 40 | **D — unsafe to alter without manual intervention** |
| `caprep_test` (local test DB) | `0014_drop_duplicate_queue_index` | 40 | **D — same lineage as `caprep`** |
| `caprep_audit` (created during the audit) | `6c3f7b0a0c13` | 58 | **C — on the current canonical chain** |

### 1.3 Missing-revision evidence (the defect)

```
$ alembic current            # DATABASE_URL → caprep
ERROR [alembic.util.messaging] Can't locate revision identified by
      '0014_drop_duplicate_queue_index'
FAILED: Can't locate revision identified by '0014_drop_duplicate_queue_index'
```

`0014_drop_duplicate_queue_index` **does not exist anywhere in the repository** — neither as a
revision file nor as a `down_revision` reference. Consequences:

* `alembic current`, `alembic upgrade head` and `alembic check` all abort against these databases.
  They are not merely *behind* the chain; Alembic cannot reason about them at all.
* Because the revision is unresolvable, **no safe forward migration path exists** for these
  databases. Stamping or force-upgrading them could skip or double-apply DDL against a schema that
  does not match the chain.

### 1.4 Schema divergence (why "behind" is the wrong model)

Comparing `caprep` with a database on the canonical chain (§3):

* **28 tables exist at head but not in `caprep`** — e.g. `content_documents`, `document_pages`,
  `collections`, `collection_questions`, `content_grants`, `content_access_rules`, `audit_logs`,
  `notifications`, `platform_settings`, `badges`, `forum_threads`, `study_groups`, `law_notices`,
  `formula_entries`, `glossary_entries`, `marketplace_listings`, `mentorship_requests`,
  `experiment_assignments`, `analytics_events`, `proctor_events`, `support_tickets`,
  `calendar_events`, `pomodoro_sessions`, `exam_mode_sittings`, `payment_gateway_config`, …
* **10 tables exist only in `caprep`** and are created by *no* migration in the chain:
  `documents`, `plans`, `plan_entitlements`, `entitlement_definitions`, `subtopics`,
  `content_sections`, `authoritative_sources`, `verification_records`,
  `subject_component_audit_logs`, `point_adjustments`.

  Method note: a quoted-name grep returned 0 hits for all ten, so the method was positive-controlled
  against names known to be in the chain (`questions` → 3 files, `content_documents` → 1,
  `users` → 2, `mock_attempts` → 2). The apparent hits for `documents` in migrations `0008`/`0011`
  are substrings of `content_documents`, not the standalone table.

**Reading:** the two development databases were built from an **earlier, since-squashed migration
lineage** (the one that ended at `0014_drop_duplicate_queue_index` and used the older table
vocabulary), while the repository now contains a rewritten linear chain (`0001_initial` …
`6c3f7b0a0c13`). They are therefore **not recoverable by Alembic** from the current chain alone.

### 1.5 Does any development data need preserving?

| Database | Rows | Assessment |
|----------|------|------------|
| `caprep` | 1 user, 3 courses, 27 questions, 0 documents, 0 ingestion jobs, 0 subscriptions | Seed-shaped development data only; no ingestion, payment or entitlement records |
| `caprep_test` | 0 users, 0 questions (empty) | Disposable |

No production data exists in either database. However, per the P0 brief, **nothing was dropped or
rewritten** — the owner decides whether to keep them.

---

## 2. Classification

| Database | Class | Rationale |
|----------|-------|-----------|
| `caprep` | **D (unsafe to alter) + A (disposable data)** | Foreign, unresolvable revision stamp; divergent schema (10 legacy-only tables, 28 missing). Alembic cannot advance it. Data is seed-shaped. |
| `caprep_test` | **D (unsafe to alter) + A (disposable data)** | Same lineage/stamp; empty. |
| `caprep_audit` | **C (recoverable from the current chain)** | Freshly created and migrated with the repository's own `alembic upgrade head`; `alembic check` clean. |
| `caprep_rebaseline_proof` | **C (reference)** | Created in this task purely as the chain proof (§5). Safe to drop at any time. |
| `caprep_rebaseline_test` | **C (reference)** | Created in this task to run the application suite against a clean, `*_test`-named database (§5). Safe to drop at any time. |

---

## 3. Safe remediation

### 3.1 Procedure for a disposable database (canonical chain)

```bash
# 1. create an empty database owned by the app role
psql -h 127.0.0.1 -p 5432 -U caprep -d postgres \
  -c 'DROP DATABASE IF EXISTS caprep_rebaseline_test' \
  -c 'CREATE DATABASE caprep_rebaseline_test OWNER caprep'

# 2. build the schema from migrations only
cd apps/api
export DATABASE_URL='postgresql+psycopg://caprep@127.0.0.1:5432/caprep_rebaseline_test'
python -m alembic upgrade head

# 3. verify schema matches the models
python -m alembic check

# 4. run the application tests against it
TEST_DATABASE_URL='postgresql://caprep@127.0.0.1:5432/caprep_rebaseline_test' \
  python -m pytest -o addopts="" -q -rs
```

Notes learned while running this:

* `DEBUG` must not be exported in the shell when running Alembic or pytest — a shell-level
  `DEBUG=release` overrides `.env` (`DEBUG=true`) and makes `Settings` raise
  `ValidationError: debug / Input should be a valid boolean`. Environment issue, not a code defect
  (see `docs/P0_EXECUTION_REPORT.md`).
* `alembic check` emits one benign warning —
  `UserWarning: Computed default on document_pages.search_vector cannot be modified` — followed by
  `No new upgrade operations detected.`

### 3.2 Procedure for `caprep` / `caprep_test` (data that must not be destroyed)

The brief forbids destroying or rewriting development databases, and no safe in-place path exists.
If either database must be preserved, use **export → rebuild → import**, never `alembic stamp`:

```bash
# 1. preserve an archive copy (do not touch the original)
mkdir -p ~/caprep-db-archive
pg_dump -h 127.0.0.1 -p 5432 -U caprep -Fc caprep > ~/caprep-db-archive/caprep-$(date +%F).dump

# 2. build a NEW database on the current chain (procedure 3.1) under a new name
# 3. import only the tables that still exist on the canonical chain, e.g.
pg_restore --data-only --table=users --table=courses --table=questions \
  --disable-triggers -d caprep_rebaseline_test ~/caprep-db-archive/caprep-<date>.dump
#    ...repeat per table, mapping legacy names (documents → content_documents, plans → …) by hand
# 4. verify counts, then point DATABASE_URL at the new database
# 5. keep the original database until the new one has been verified
```

**Explicitly rejected approaches** (and why):

* `alembic stamp head` on `caprep` / `caprep_test` — would label a divergent schema as current,
  leaving 28 missing tables and 10 orphan tables, and would make every later `alembic upgrade`
  apply DDL against the wrong schema.
* `alembic upgrade head` after manually inserting a `0014…` stub revision — recreating a migration
  file to satisfy a stale stamp would fabricate history and risk re-running DDL.
* Dropping either database — not authorised by this task.

---

## 4. Data preservation considerations

* No `ingestion_jobs`, `documents`, `content_documents`, `subscriptions`, `payment_gateway_config`
  or `audit_logs` rows exist locally, so nothing in the pipeline, billing or audit domains is at risk.
* The only non-seed artefact worth checking is the single `users` row in `caprep`; the archive step
  in §3.2 preserves it without copying any credential material.
* **Migrations were not modified.** The canonical chain in the repository is unchanged; the
  databases are the divergent party.

---

## 5. Verification results

| Check | Command | Result |
|-------|---------|--------|
| Head is unique and linear | `alembic heads` | `6c3f7b0a0c13` (single head) |
| Clean build from empty | `alembic upgrade head` on empty `caprep_rebaseline_proof` | PASS → `6c3f7b0a0c13` |
| Reversibility | `alembic downgrade base` | PASS — downgraded cleanly through `0002_ingestion → 0001_initial → (base)` |
| Re-apply | `alembic upgrade head` again | PASS → `6c3f7b0a0c13` |
| No model/migration drift | `alembic check` | `No new upgrade operations detected.` |
| Application tests on the re-baselined schema | `TEST_DATABASE_URL=…caprep_rebaseline_proof pytest -q -rs` | **997 passed, 2 skipped, 0 failed** |
| Application tests incl. destructive round-trip test | `TEST_DATABASE_URL=…caprep_rebaseline_test pytest -q -rs` | **998 passed, 1 skipped, 0 failed** |
| Development databases untouched | `psql … select version_num from alembic_version` | `caprep` / `caprep_test` still stamped `0014_drop_duplicate_queue_index`, 40 tables each; **no writes performed** |

### 5.1 The remaining skip

`998 passed, 1 skipped` — the single skip is the suite's own documented conditional guard, not a
swallowed error:

* `tests/test_infra_contract.py:288` — *"no Supabase database URL in this checkout"* (the local
  `DATABASE_URL` is a localhost PostgreSQL, so the cross-project database assertion cannot run).

Two further guards that were **skipped during the audit baseline** now execute and pass, because
`apps/web/dist` exists after the frontend gate run:

* `tests/test_infra_contract.py:614` and `:671` — the built-bundle scans for server-only material.

That accounts exactly for the baseline's `995 passed / 4 skipped` versus today's
`997 passed / 2 skipped` on the proof database (999 collected items in both runs).

