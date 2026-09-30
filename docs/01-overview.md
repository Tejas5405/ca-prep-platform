# CA Prep Platform — Overview

Structured preparation for the ICAI Chartered Accountancy examinations: a
question bank, a mock-attempt engine, a document library, and a planner that
turns "I am weak at indirect tax" into "chapter 4, Tuesday".

## What it is

A study platform for CA candidates in India, built as a stateless FastAPI
service over PostgreSQL and Redis, with a browser SPA in front.

## Course levels

`courses.level` is constrained by `ck_course_level` to exactly four values:

| Level | Meaning |
|---|---|
| `FOUNDATION` | CA Foundation |
| `INTERMEDIATE` | CA Intermediate |
| `FINAL` | CA Final |
| `SET` | Self-Paced Online Module, shipped as SET A–D |

`SET` was added by migration `0014_widen_course_level_set`. It exists because
self-paced modules are real courses that appear in the catalogue and need a
level for the existing filters to work; without a value for them the CHECK
constraint refused the row and the insert failed at the database as a 500.

The same four values are mirrored in `CourseLevel` (`app/models/enums.py`) and
in the SQL constraint. They must be changed together — a model that permits a
value the database rejects fails on write, not on review.

## The learning loops

1. **Syllabus navigation.** A four-level tree — `courses` → `subjects` →
   `chapters` → `topics`. Mock generation and progress analytics both operate at
   chapter granularity, because that is the unit a study plan is written in.
2. **Question practice.** A `questions` bank with typed options, instant
   scoring, and per-question progress rolled up into `user_question_progress`
   while the append-only truth stays in `practice_attempts`.
3. **Mock attempts.** `mock_tests` and `mock_attempts`, scored through
   `mock_scoring`, with negative marking.
4. **Content library.** Uploaded PDFs, OCR'd and chunked into
   `content_documents` and `document_pages`, with a tier gate and an explicit
   access-rule table.

## Where to go next

- `02-architecture.md` — how the pieces fit, and the middleware order.
- `03-data-model.md` — the 59 tables and why they are shaped as they are.
- `04-api-reference.md` — envelopes, pagination, route namespaces.
- `05-development.md` — running it locally and the quality gates.
- `06-deployment.md` — Render blueprint and post-deploy smoke test.
- `07-security.md` — rate limiting, webhook fail-closed, RBAC.
- `08-content-ops.md` — copyright posture, QC constraints, the OCR pipeline.
- `09-roadmap.md` — what is built, and what is deliberately not yet built.

Everything in these documents describes code that exists in this repository and
is exercised by the test suite. Where something is not built, it says so.
