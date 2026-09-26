# Build audit — decisions vs. what exists

Blueprint: `CA_Preparation_Platform_Final_Master_Blueprint.pdf` (v3.0, authoritative)
plus the recorded stack amendments SA-01…SA-08.

This audit answers one question: **for everything we decided, is it built — and if
not, why not?** An unbuilt item is only acceptable with a reason attached. The
reasons fall into five kinds, and they are not interchangeable:

| Reason | Meaning |
| --- | --- |
| **Needs a credential** | The code is done and correct; it cannot be *verified* without a key or account only you hold. |
| **Not in the build order** | Never scheduled. D1–D7 does not mention it. This is a planning gap, not a technical one. |
| **Phase-gated** | Deliberately deferred by your own decision, with a stated trigger condition. |
| **Environment** | Cannot be exercised in this sandbox (no Docker, no Redis, no Tesseract, no browser). |
| **Deferred by you** | Explicitly parked in conversation. |

---

## 1. Payments (§13.1) — BUILT THIS SESSION

Was: **schema only.** `subscriptions` and `payment_events` shipped in
`0001_initial_schema.py`, but there was no service, no gateway client, no route and
no order table. Two of the three tables existed to support a flow that did not
exist.

Now:

| Piece | Status |
| --- | --- |
| `app/services/billing.py` — prices, entitlement rules, signature verification, renewal maths | built, 72 tests (`test_billing.py`) |
| `app/integrations/razorpay.py` — order creation, payment lookup, HTTP Basic auth | built, exercised through the route tests |
| `alembic/versions/..._0003_payments.py` — `payment_orders` | built, rendered offline as SQL |
| `app/repositories/billing.py` — `SqlBillingStore`, `ON CONFLICT` idempotency | built |
| Payment HTTP surface (routes + store + gateway, end to end) | 61 tests (`test_payments_api.py`) |
| `POST /api/v1/payments/order` | built |
| `POST /api/v1/payments/confirm` (browser callback) | built |
| `GET /api/v1/payments/plans`, `GET /api/v1/payments/subscription` | built |
| `POST /api/v1/webhooks/razorpay` | built |
| Mutation testing of the money path | **14/14 mutations caught** |

**Still open:**

* **Razorpay keys.** `RAZORPAY_KEY_ID` / `_KEY_SECRET` / `_WEBHOOK_SECRET` are
  placeholders in `.env` and `render.yaml`. *Why:* only you can create them, and a
  test-mode key is a two-minute job at dashboard.razorpay.com. Until then
  `payments_enabled()` is `False` and the endpoints answer **503 by design** — a
  deployment that cannot take money should say so, not 500.
* **End-to-end against real Razorpay.** *Why:* needs those keys plus a public URL
  for the webhook. Everything up to the network boundary is tested.
* **A live PostgreSQL.** *Why:* no Docker or PostgreSQL in this sandbox; the
  migration is verified by rendering it to SQL offline and by the ORM/migration
  drift tests, not by running it.

## 2. Deliberately deferred (your decisions)

| Item | Reason |
| --- | --- |
| Video solutions | Phase 3, managed provider (SA: no self-hosted transcoding) |
| WebSockets / live classes | Phase 4, gated on DAU > 1,000 / concurrent > 100 / revenue > $5k/mo |
| AI assistant | Phase 2+. Costed at a 9× negative margin at ₹999/yr; no SKU was created until the ceiling in SA-05 is enforced |
| Offline, social, mentorship, referrals | P2 — after the P0 slice is finished |
| Push/email (Resend) | Keys are placeholders; the confirmation email is a queued event, not a send |

## 3. Not built, and not phase-gated — the honest gaps

| Item | Why it is still missing |
| --- | --- |
| ~~Doubt resolution (P0)~~ | **BUILT.** `0005_doubts`, `SqlDoubtRepository`, and `POST/GET/PATCH /doubts` plus replies and accept. Ownership, staff-only answers and resolve-permission are enforced from the database row, not the token claim or the request body. |
| ~~Search endpoints (D4)~~ | **BUILT.** `GET /search` over the `0001` GIN index. The UI is not built (see below). |
| ~~Spaced repetition over HTTP~~ | **BUILT.** `/revision/due`, `/revision/review`, `/revision/stats`. The SCHEDULE is server-side: a client sends a grade, never a due date. |
| ~~Progress endpoints (D5)~~ | **BUILT.** `GET /progress/overview` returns totals, per-subject accuracy, focus/strong chapters, profile and recent activity in one response, because the dashboard needs them in one render. |
| **Collections and gamification over HTTP** | Still library code. Both are written and tested, and neither has a UI that consumes it yet — mounting a router nothing calls adds surface without a user. This is now the honest reason; it was "not mounted" before. |
| **Frontend for the new surface** | The API is complete and tested; `Dashboard.tsx` reads `/mocks` only. Practice, revision, doubts, search and the planner page have no screens. This is where "the webapp works 100%" is decided, and it is the largest remaining item. |
| **Firebase Admin SDK role endpoint** | Deferred by you (`do what feels right`). Roles currently come from the token claim, which is fine while roles are managed by hand but cannot be self-service. |
| **Turborepo root** | The build order (deliverable 3) called for a Turborepo; what exists is two independent apps with their own toolchains and no root task runner. Nothing depends on it yet — which is exactly why it was never added. |
| **Git repository** | The workspace was never initialised as one. `.gitignore` and the CI workflow are written for a repo that does not exist yet, so **CI has never run**. |
| **mypy** | Declared advisory in the stack document; never installed, so there is no type gate beyond `tsc` on the frontend. |
| **Exam mode (pen-and-paper vs CBT)** | Sources conflict and no authoritative ICAI page settles it. The landing page makes **no claim** and a test asserts that absence — the honest option, not an oversight. |

## 4. Closed by this audit

* `docs/api/openapi.json` — the OpenAPI contract was a named deliverable that had
  never been written. Now generated from the app (22 paths) and **guarded by a test
  that fails when it drifts**.
* The schema tripwire — `TestSchemaSize` asserted 25 tables; the payment work made
  it 26, so the suite failed until the count and the payment invariants were added.
  The tripwire worked as designed.
* `payment_events` had no writer: the idempotency table existed for a flow that
  could not run. It now has one, and rejected events are archived too, so
  "the money left my account" is answerable from the database.

* **Twelve defects that only a real database could reveal.** Mounting the new
  routers and running them over HTTP against PostgreSQL 17 (ASGI transport, real
  sessions, real constraints) found, and fixed:

  | Defect | Consequence if shipped |
  | --- | --- |
  | `checkout.exam_date` / `started_at` rejected ISO strings (`strict=True`) | `/planner/generate` and `/mock-attempts/{id}/submit` were **impossible to call from any HTTP client**, including our own frontend |
  | `progress._sync_totals` wrote the level NAME into a SmallInteger column | every correct answer raised `ValueError` on the answer path |
  | `focus_and_strong` built `ChapterPerformance` with fields it does not declare and omitted `accuracy` | `/progress/overview` raised `TypeError` for any student with chapter stats |
  | `chapter_stats` never fetched `Chapter.weightage` | focus areas were ranked by a default of 0 — arbitrary order that looked deliberate |
  | `_award` called instead of the public `award` | the ledger credited points the daily activity row never saw (streak chart read 0) |
  | `_LEVEL_ORDER` / snake_case wire names / `and_()` join clauses | ordering, query-parameter names and the chapter-count join were each wrong against real SQL |
  | Objective questions skipped were graded `None` ("pending review") | a blank MCQ sat in the dashboard's review count forever, and dropped out of the accuracy denominator |
  | cards scheduled at tomorrow 04:00 instead of now | "review your mistakes" showed an **empty queue** to the student who had just made them |
  | `problem()` sent `application/json` | the body was documented as RFC 7807 while announced as ordinary JSON; now `application/problem+json` |

* **The `asyncpg` silent-skip trap.** The database suite needs `asyncpg`, which was
  declared in no requirements file, and the fixture converted *any* error into a
  skip. On a clean machine — and in CI — 87 tests skipped and the suite reported
  **green having executed no SQL at all**. The driver is now declared and a missing
  package FAILS instead of skipping; no database, or an unreachable one, still skips.
* **The `postgres` marker was never registered.** With `--strict-markers` that is a
  collection *error*, not a warning; it only stayed hidden because the files using it
  were collected when a database happened to be reachable.
* **OpenAPI contract** regenerated: 39 paths (was 22), still drift-guarded.

## 5. One security item that is yours, not the code's

The Supabase **secret** key was pasted into a chat. It is used only server-side and
never ships to the browser, but it should be rotated before real students exist:
`dashboard.supabase.com` → project `zyrmlnpvylhcpyaoizyz` → Settings → API keys.

## 6. Numbers

| Gate | Result |
| --- | --- |
| Backend tests | **779 passed, 3 skipped** with a database (was 644/2); the 3 skips are "no built web bundle" and "no tesseract binary" |
| Ruff | `check` and `format` clean |
| Payment mutations | 14/14 caught |
| Frontend tests | 104 passed (was 93) |
| OpenAPI paths | 39 (was 22) |

---

## Round: identity provisioning tests, and the two defects they found

`tests/integration/test_identity_provisioning.py` was owed from the round that
repaired `GET /me`. Writing it against a real database found two defects that no
existing test could have seen, because every one of them stops short of the code
in question.

### D1 — deactivation was unenforceable

`SqlUserRepository.get_by_auth_id` filtered `deleted_at` and **documented** that it
filtered `is_active`. It did not. The consequence was not a cosmetic one:
`get_current_user` looks the caller up, finds nothing (because the row is hidden
only by accident), and **provisions a new account**. A suspended student's next
request therefore created a second row for the same `auth_user_id` and let them
straight back in.

The fix is a filter plus a deliberate asymmetry:

- `get_by_auth_id` filters both flags by default and takes `include_inactive` /
  `include_deleted` for the callers that must SEE a barred row.
- `get_current_user` uses those flags to distinguish "no account" from "an account
  that is barred", and answers the second with **403** rather than provisioning.
- `provision`'s race-recovery read stays unfiltered *on purpose*. It must find the
  row that occupies the unique index; excluding a suspended row there would send
  the loser of a race back into the insert, forever.
- `get_current_user` re-checks the row `provision` returned, because on the race
  path that row may be one the caller did not create.

### D2 — `BOOTSTRAP_ADMIN_EMAILS` in the environment crashed settings construction

`bootstrap_admin_emails` and `cors_origins` are `list[str]` fields with
`mode="before"` validators that split a comma-separated string. pydantic-settings
JSON-decodes a complex field **before** a validator runs, so
`BOOTSTRAP_ADMIN_EMAILS=admin@caprep.in` raised `SettingsError: error parsing value
for field "bootstrap_admin_emails"` — at import, on the API's own settings object.
Not a rejected request: an API that does not boot, and only in the environment that
actually configured an administrator.

The "comma-separated" promise in the docstring was therefore false for both fields;
the deployment had to be spelled as JSON or not at all. Both fields are now
`Annotated[list[str], NoDecode]` with the raw string reaching a validator that
accepts **either** spelling (`_split_list`), because the local stack sets JSON and a
human editing a dashboard field writes commas.

Pinned by tests: `test_an_empty_allow_list_promotes_nobody` (the shipped default
grants nothing, so no deployment has a backdoor by omission) and
`test_a_configured_bootstrap_email_becomes_an_admin_and_nothing_else_does`, whose
near-miss cases (`admin@caprep.in.evil.example`, `administrator@caprep.in`) are the
shapes a `startswith`/`endswith` check would let through.

### What the rest of the file pins

| Behaviour | Why it is worth a test |
| --- | --- |
| The row is visible on a **second connection** | A read through the writing session can be answered from its identity map, so it would pass with no commit at all |
| Repeated sign-ins keep one row | Provisioning twice fails on the unique index, or creates a second account |
| Same email, two subjects, two accounts | Password + Google on one address is legitimate; merging them merges two people |
| A token claiming `ADMIN` does not promote its row | The claim is only settable by an API holding a secret this deployment may not have |
| No email claim → STUDENT | Supabase omits it for some providers; absence must not widen access |
| Three concurrent provisions converge | Proves the SAVEPOINT survives real contention, not just a replay of the loser's path |
| Barring one subject does not bar another | A guard written on a shared email or a global flag passes a single-account test and locks out the platform |

**Result:** 827 passed, 5 skipped (16.6s) against local PostgreSQL 17.11 — the 5
skips are environmental (tesseract absent, no built web bundle, no Supabase DB URL).
`ruff check` clean. Frontend unchanged this round: `tsc -b` clean, `eslint` clean,
123 tests passing, `vite build` succeeds.

---

## Round: §7.3 route coverage — the audit that found two dead ends

Blueprint §7.3 is an *example* endpoint inventory, and until now it had never been
checked line by line against `app/api/v1/`. Two of its routes were missing, and both
failures had the same signature as the empty list pages: **the client could reach a
state it had no way out of.**

| §7.3 route | What exists | Verdict |
| --- | --- | --- |
| `POST /auth/verify` | JWKS verification inside `get_current_principal` | **Superseded** (SA-05 → SA-09). A verify endpoint would be a second source of truth for a token the API already validates on every request. |
| `GET /courses` | `GET /api/v1/curriculum/courses` (plus `/subjects`, `/chapters`, `/chapters/{id}/topics`) | Present, namespaced |
| `GET /questions` | `GET /api/v1/practice/questions` (filtered draw) and `GET /api/v1/search` | Present |
| `GET /questions/:id` | **`GET /api/v1/questions/{question_id}`** | **Was missing — built this round.** See below. |
| `POST /attempts` | `POST /api/v1/mocks/{id}/attempts`, `POST /api/v1/practice/answers` | Present in this product's vocabulary (a practice attempt is one answer, not a sitting) |
| `POST /attempts/:id/answers` | Answers are submitted with the attempt: `POST /mock-attempts/{id}/submit` carries the list | **Deliberate deviation.** A timed paper is submitted once; per-answer writes would need a client that can lose the last write on a dropped connection. |
| `POST /attempts/:id/submit` | `POST /api/v1/mock-attempts/{attempt_id}/submit` | Present |
| `GET /progress/overview` | `GET /api/v1/progress/overview` | Present |
| `POST /collections` | **Nothing.** `app/services/collections.py` validates filters and seeds the LDR system collection; there is no `collections` table, no route and no screen | **Open gap — see below** |
| `POST /planner/generate` | `POST /api/v1/planner/generate` (+ `/replan`) | Present |
| `POST /ingestion/jobs` | Folded into `POST /api/v1/ingestion/uploads`, which now also creates the job row | **Deliberate deviation**, documented at the call site: one round trip, and a failed upload leaves a visible retryable `QUEUED` job instead of a paper that silently never appeared |
| `GET /ingestion/jobs/:id` | `GET /api/v1/ingestion/jobs/{job_id}` (+ the list, + `/start`) | Present |
| `POST /admin/questions/:id/publish` | `POST /api/v1/admin/questions/{question_id}/publish` | **Was missing — built in the previous round.** It was the missing last step of the editorial chain: drafts could be approved and then never shipped. |
| `POST /payments/order` | `POST /api/v1/payments/order` (+ `/confirm`, `/plans`, `/subscription`) | Present, 503 without keys by design |
| `POST /webhooks/razorpay` | `POST /api/v1/webhooks/razorpay` | Present, signature-verified, idempotent |
| `GET /health` | `GET /health` (+ `/db`, `/redis`, `/storage`) | Present |
| §9.1 `question_solutions` table | Solutions live on the question row (`explanation`, `model_answer`) | **Deliberate deviation.** A separate table exists to hold `video_url`; the row-level fields already serve text solutions, and one solution per question is the model the review flow produces. Revisit when video solutions are built. |

**Score: 16 of 16 named routes addressed — 11 as named or namespaced, 4 deliberate
deviations, 1 genuine gap (collections).**

### `GET /questions/{id}` — built this round

The gap was invisible from the server side and obvious from the browser: `GET /search`
returns an id, a snippet and the metadata, and **deliberately no options**, so there
was nothing a client could do with a result except display it. Search found questions
and then dead-ended. The route now returns the question with its options, its
placement and its sitting label.

Two properties are asserted against a real database, because both are the kind that
survive review and fail in production:

* **The answer is not in the payload before the student answers.** Asserted against
  the raw response *text* (`correctAnswer`, `explanation`, `modelAnswer`, `isCorrect`
  must not appear), not field by field — a leak is a stray key, and the key that
  leaks is rarely the one under test. The repository never *reads* those columns
  unless the caller has an attempt, so there is no filter to forget.
* **Unpublished content is uniformly a 404.** A `DRAFT` row, a soft-deleted row and
  an id that never existed all produce the same body, so the endpoint cannot be used
  to enumerate the bank. A 403 would confirm existence.

Also asserted: a skipped answer still unlocks the solution (the row exists), one
student's bookmark never appears on another's screen, and `GET` creates no attempt
or progress row — a read that writes is how a dashboard ends up counting questions
nobody attempted.

### The content team's screen — built this round

The ingestion chain was complete and unreachable: signed upload → job → drafts →
review → publish existed end to end, and could only be driven with `curl`. A
pipeline with no operator interface is not a feature, it is a set of endpoints.

`apps/web/src/pages/Admin.tsx` at `/admin` (and `/admin/*`, blueprint §6.1) now
drives it: request a signed URL, `PUT` the bytes straight to storage, start the job,
watch the queue with a failure's reason inline, then work the review queue. The
order is deliberate and slow — approving a draft creates a question in `DRAFT`, and
publishing is a **separate, Content-Manager-only act**. Clearing an OCR backlog is
triage; signing content off for students is not, and they must not be one button.

`GET /search` results now open in place, so the question detail route has a consumer
from the day it ships.

### Still open

| Item | Status |
| --- | --- |
| ~~**Collections**~~ (`POST /collections`, §9.1 `collections` + `collection_questions`) | **CLOSED this round.** Model, migration `0007_collections`, six routes, `SqlCollectionStore`, 17 integration tests. The last named §7.3 route now exists. |
| Gamification over HTTP | Library code only, still no UI (unchanged). |
| Turborepo root, git repo, CI never run, mypy | Unchanged from the previous audit. |

### Numbers after this round

| Gate | Result |
| --- | --- |
| Backend tests | **898 passed, 3 skipped (38.0s)** with PostgreSQL 17.11 (was 879/5) |
| New backend tests | 17 — `tests/integration/test_postgres_collections.py` |
| Ruff | `check` clean, `format` clean (5 previously unformatted files fixed) |
| OpenAPI paths | **50** (was 44), regenerated and drift-guarded |
| Frontend tests | **182 passed, 10 files** (was 131 / 8) — 13 new for the collections and LDR screens, 8 for the admin surface and question detail |
| Frontend gates | `tsc -b` clean · `eslint .` clean · `vite build` OK |

### The screens

`/collections` lists the student's containers with the server's counts, creates by
name, opens a collection to its questions, and removes memberships. `/ldr` renders
the practice loop's flag. Both are reachable from the dashboard ("Questions you
kept"), because a screen whose only entrance is a typed URL is a screen nobody uses.

Two things the screens deliberately do NOT have:

* **No delete or rename on a system collection.** The LDR list is referenced by the
  practice loop; the button is absent rather than disabled, because a disabled
  delete invites a bug report.
* **No write control on `/ldr`.** The flag is owned by the practice loop, and a
  toggle here would be a second writer: the row the practice route reads decides
  whether a question enters the daily queue, so the two would eventually disagree.
  The empty state says where flagging happens instead of offering a button that
  does not exist.

`accuracy: null` renders as "not scored yet" rather than "0%": a skipped question
and a failed one are different, and one of them the student has not answered.

## Round: `POST /collections` — the last §7.3 gap, and two bugs it uncovered

`app/services/collections.py` validated smart-collection filters and seeded the LDR
collection, and no route called it: the same shape as the empty list pages and the
publishing endpoint that wrote nothing. It is now a feature.

**What a collection is, decided explicitly.** Not a bookmark. The bookmark is one
boolean on `user_question_progress` written by the practice loop, meaning "I met this
question and want it again"; a collection is a named container with membership rows
that can carry a note. They are different questions ("what did I flag?" vs "what does
this group contain?") and the earlier idea of a nullable `collection_id` on the
progress row was rejected: there is one progress row per student/question, so the
second membership has nowhere to go. Two tables, one container, one membership.

**Endpoints** (all filtered by the caller's `users.id` *in the query*, not compared
afterwards): `POST/GET /collections`, `GET/PATCH/DELETE /collections/{id}`,
`POST/DELETE /collections/{id}/questions[/{question_id}]`,
`GET /questions/{id}/collections`, and `GET /ldr` for the practice loop's own flag.

**Two real bugs, both caught by something other than the request that caused them.**

1. `JSONB` without `none_as_null=True` stores a Python `None` as the JSON value
   `null`, which is not SQL NULL. `filters IS NULL` was therefore false for every
   manual collection and `ck_collection_filters_match_kind` rejected the insert — so
   every "create a collection" returned a 409 "name already used", which is the
   message the *other* constraint on that table produces. A database CHECK found a
   modelling bug that no amount of route reading would have.
2. The test harness injected the same `User` ORM instance into every request, while
   production resolves that row once per request. A route that rolls back (any 404 or
   409) expires every instance in the session, so the NEXT request touching `user.id`
   issued a lazy load, which in an async context is `MissingGreenlet` — a 500 that
   exists only in the tests, appearing in whichever endpoint was written most
   recently. `Actor` now re-reads the identity row per request, and the collections
   routes capture `user_id` before any branch that can roll back, because that
   hazard is real in production too.

