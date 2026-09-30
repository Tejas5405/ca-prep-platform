# Data Model

59 tables. PostgreSQL 16 is the system of record; Redis holds nothing durable.

## Shared conventions (`app/models/base.py`)

- **UUID primary keys** (`UuidMixin`) — non-enumerable in URLs, safe to
  generate client-side.
- **`created_at` / `updated_at` as `TIMESTAMPTZ`**, never naive. A naive
  timestamp silently depends on the server's timezone, so a student in Dubai and
  a student in Delhi see different attempt histories.
- **Soft delete** (`SoftDeleteMixin`) on user-generated content, so an accidental
  delete does not destroy notes or a doubt thread.

## Curriculum

```
courses ──< subjects ──< chapters ──< topics
```

Four levels, not three, because mock generation and progress analytics both
operate at chapter granularity — which is the unit a study plan is written in.
`subject_components` carries the Intermediate subject splits.

## The question bank

| Table | Role |
|---|---|
| `questions` | one row per question: text, type, difficulty, marks, negative marks, `correct_answer`, `status`, `review_state` |
| `question_options` | MCQ/MSQ options, ordered by `sequence`, flagged `is_correct` |
| `question_versions` | every revision, so a marked attempt remembers which version it was marked against |
| `question_flags` | user-reported problems, with resolution state |
| `law_notices` | citation records that do **not** rewrite answers |

`status` is constrained to `DRAFT | IN_REVIEW | APPROVED | PUBLISHED | ARCHIVED`
by `ck_questions_status`.

**`ck_questions_published_requires_verifier`** refuses a `PUBLISHED` row with no
`verified_by`. A published question is one every student will be marked against;
publishing an unreviewed one produces a report that calls students wrong. The
database refuses it rather than the UI warning about it.

The verifier recorded is always the *caller's* row, resolved server-side. A
request cannot name who signed a question off.

## Content library

`content_documents` → `document_pages`, with `content_access_rules` for
per-document grants. `access_tier` is `FREE | PREMIUM | PREMIUM_PLUS`
(`ck_document_tier`), and `status` must be one of `UPLOADED, QUEUED, PROCESSING,
EXTRACTING, OCR_REQUIRED, INDEXED, COMPLETED, FAILED, ARCHIVED` — note there is
no `READY`.

## Learning and progress — two tables, on purpose

| Table | Shape | Answers |
|---|---|---|
| `user_question_progress` | one row per (user, question), mutable | "how am I doing on this question" |
| `practice_attempts` | append-only, one row per sitting | "exactly what happened, in order" |

The rollup is a cache of the log, not a replacement for it. Keeping only the
rollup would lose the ability to reconstruct a disputed attempt; keeping only
the log would mean recounting the entire history on every dashboard render.

`idx_progress_user_created (user_id, created_at)` backs the per-user history
walk.

## Study planner (`0015_study_planner`)

`study_plans` — one per (user, target attempt) — and `study_plan_items`, one
scheduled chapter per row.

| Constraint | Refuses |
|---|---|
| `uq_study_plan_user_attempt` | two plans for the same target; which is real? |
| `ck_study_plan_target_attempt` | an empty attempt name, which would silently match nothing |
| `uq_study_plan_item_chapter` | the same chapter twice in one plan |
| `ck_study_plan_item_terminal` | `done_at` **and** `skipped_at` both set — complete and skipped at once |

`original_date` is preserved across every reschedule, so "what did the plan look
like on day one" stays answerable after a hundred drags.

`target_attempt` is free text (`'May 2027'`), not an enum: hard-coding ICAI
attempt names would turn every schedule change into a migration.

**No API routes exist for the planner yet.** The schema and models are in place
and unused.

## Tagging: `applicable_attempts` (`0016_applicable_attempts`)

`TEXT[]` on `questions` and `content_documents`, defaulting to `'{}'`, with a
**GIN** index on each.

The only question ever asked is containment — `applicable_attempts @>
ARRAY['May 2027']` — which GIN answers as an index lookup. Without it the filter
meant to narrow the bank widens into a sequential scan, so the index is part of
the column, not an optimisation to add later.

There is **no** `applicable_attempt` column and no backfill: no row has ever had
one recorded, because the column to record it does not exist.

## Keyset indexes (`0017_cursor_pagination_indexes`)

`idx_audit_logs_created_id_desc`, `idx_analytics_events_created_id_desc` and
`idx_practice_attempts_created_id_desc`, each `(created_at DESC, id DESC)`.

The pre-existing `idx_audit_time (created_at)` cannot serve a keyset walk: with
no `id` tiebreaker, rows sharing a timestamp must still be sorted in memory.
These are additional; the old indexes still answer the per-user and per-actor
queries they were built for.

## Migrations

`alembic upgrade head` is run by Render as a `preDeployCommand`, once per
deploy, before the new version takes traffic — not from application startup,
which races across instances.

Every migration is reversible; `alembic downgrade base` then `upgrade head` is
part of the verification routine. `alembic check` must report
`No new upgrade operations detected`, which is what catches a column or index
that exists in the database but not in the ORM — invisible to the test suite,
and a migration away from being silently dropped.
