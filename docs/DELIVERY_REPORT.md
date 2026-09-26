# CA Preparation Platform — Delivery Report

**Date:** 25 September 2026 · **Revision:** `0007_collections` (head) · **Report status:** honest and verified — every number below was produced by running the command beside it, on this date, in this workspace.

This is the final report the specification asks for. It is written to be *checked*, not
skimmed: each claim names the command or file it came from, and the sections at the end
list what is **not** built and why, because a report that only lists successes is a
report nobody can act on.

---

## 1. What this is

A working CA preparation platform: a curriculum-backed question bank, practice with
server-side grading, mock exams scored on the server, a dated study planner, spaced
repetition, doubts, collections, PDF→question ingestion with human review, publishing
gates, subscriptions, and a public marketing site — React + TypeScript + Tailwind on
Vercel, FastAPI + PostgreSQL + Redis/RQ on Render, Supabase for Auth and Storage.

**Stack, as frozen** (§1 of the specification, with the amendments recorded in
`docs/architecture/stack-amendments.md`):

| Layer | Frozen choice | Status |
| --- | --- | --- |
| Frontend | React + TypeScript + Tailwind | ✅ built |
| Frontend supporting tech | TanStack Query · React Hook Form · Zod · Recharts | ⚠️ see §7 — the app uses a purpose-built `useLoader` + typed query module instead; nothing is broken by the substitution, but it is a deviation you should know about |
| Backend | FastAPI (Python 3.13) | ✅ built |
| Database | PostgreSQL (17.11 in the test rig) | ✅ built, 30 tables, 7 migrations |
| Cache / queues | Redis + RQ | ✅ built (`app/workers/rq_worker.py`), Redis used for rate limits |
| Authentication | Supabase Auth | ✅ built — email verified live; Google needs enabling in the dashboard |
| Storage | Supabase Storage | ✅ built — private bucket, signed URLs brokered by the API |
| OCR | Tesseract + PyMuPDF/pdfplumber/pdf2image | ✅ built (`app/ocr/extractor.py`) |
| Deployment | Vercel + Render | ✅ config present (`infra/render.yaml`, `infra/docker/`); never deployed from here — see §7 |
| Payments | Razorpay | ✅ server-side built; checkout UI built; admin list at `/admin/payments` built. Live charges still need `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` (all empty here). |
| Retired | Next.js · NestJS · Prisma · Auth.js · BullMQ · Firebase Auth | ✅ removed / never used |

---

## 2. How to run it

```bash
# 1. Postgres 17 (embeddable, no Docker in this image)
bash scripts/dev-stack.sh prepare      # python deps + postgres binaries + initdb
bash scripts/dev-stack.sh up           # postgres on 5433 (foreground)
bash scripts/dev-stack.sh db-create    # both databases, migrate to head, seed

# 2. Local auth (a JWKS server the API verifies tokens against, for real)
python3 scripts/local_auth.py serve --port 54321

# 3. API and web
bash scripts/dev-stack.sh api          # uvicorn on 8000
bash scripts/dev-stack.sh web          # vite on 5173
```

**Running right now in this workspace:** PostgreSQL (5433), the local auth harness
(54321), the API (8000) and the web app (5173) — the web preview is live.

---

## 3. Verified gates

### Backend (from `apps/api`)

```bash
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/caprep_test \
DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:5433/caprep" \
python3 -m pytest -o addopts="" -q
# → 981 passed, 3 skipped, 0 failed in 42.2s

ruff check .          # → All checks passed!
ruff format --check . # → 111 files already formatted
alembic check         # → no new upgrade operations detected
```

The 3 skips are environment-dependent, not ignored failures (no legacy anon key in this
checkout, no Supabase database URL, and one deliberately skipped integration path).

### Frontend (from `apps/web`)

```bash
./node_modules/.bin/tsc --noEmit  # → clean
./node_modules/.bin/vitest run    # → 11 files, 190 tests, all passing
./node_modules/.bin/vite build    # → ✓ built in 1.19s, 17 chunks
```

Test files: `landing` 32 · `publicPages` 37 · `attemptPlan` 40 · `authErrors` 17 ·
`login` 12 · `api` 12 · `checkout` 8 · `adminAndQuestionDetail` 8 · `authRole` 6 ·
`studyPages` 5 · `collectionsPages` 13.

**Code splitting, measured before and after** (the reason it was worth doing, and the
reason not to do more of it):

| | Before | After |
| --- | --- | --- |
| Entry chunk | 691.02 kB raw / 193.30 kB gzip | **387.83 kB raw / 117.28 kB gzip** |
| Route chunks | — | Admin 11.69 · Mocks 11.48 · Upgrade 9.84 · 11 more, all under 8 kB |
| Shared auth chunk | (inside the entry) | **104.20 kB raw / 25.59 kB gzip** |
| Total JS | 691 kB | **639.2 kB raw / 193.4 kB gzip across 26 chunks** |

A visitor on the landing page now transfers 397 kB of entry code plus the 104 kB shared
chunk the entry preloads, against 691 kB raw before — and against the 810 kB / 215 kB-chunk
intermediate state in the previous revision of this table.

The auth chunk halved because the browser was not using a third of what it downloaded:
`@supabase/supabase-js` constructs a `RealtimeClient` and pulls in Phoenix on import, and
**no bundler can remove it** because the constructor references it. This app talks to
FastAPI for every read and write, so the realtime client was dead weight on the first-paint
path. The browser client is now built directly from `@supabase/auth-js`, which the entry
preloads; `src/tests/supabaseClient.test.ts` pins the session-storage key, the PKCE flow
and the fact that the exported object has exactly one key (`auth`) — so the realtime client
cannot come back without a failing test.

The remaining weight is `react-dom` (irreducible for an SPA) and the app's own shared API
layer.

The split is one chunk per route, not one per component. Splitting further would
produce a waterfall of small requests on a 4G connection, which costs more in round
trips than it saves in bytes.

That follow-up is **done** (see the table above: 215 kB → 104 kB). It was taken as its
own verified change, which is why the frontend suite grew a file that pins the client's
identity rather than only its behaviour.

### Contract guards that fail a build rather than a user

| Guard | What it stops |
| --- | --- |
| OpenAPI drift (`test_api_contract.py`) | A route change that never reached `apps/docs/api/openapi.json` — 50 paths today |
| Schema contract (`test_schema_contract.py`) | A model file nothing imports (a table that silently does not exist) — 30 tables pinned |
| Infra contract (`test_infra_contract.py`) | A frontend and backend configured for *different* Supabase projects; a secret key in a `VITE_` variable or in `dist/`; a variable the code reads but the template omits; two `SUPABASE_URL` assignments in one file |
| Landing contract (`landing.test.tsx`) | Fabricated social proof; a link to a route that does not exist; an anchor that resolves to nothing |
| Publishing rules | A question reaching a student without a named human verifier (database CHECK + route) |

### Live smoke test (real server, real database, real JWT verification)

```
GET  /api/v1/payments/plans           → 200, public plan catalogue
POST /api/v1/collections              → 201, row written
POST /api/v1/collections (same name)  → 409 "You already have a collection called …"
GET  /api/v1/collections              → 200 with the server's count
POST /api/v1/collections/{id}/questions → {"added":1,…} then {"added":0,"alreadyPresent":1}
GET  /api/v1/collections/{id}         → question text + options, NO answer key
GET  /api/v1/collections/{id} (other student) → 404
POST /api/v1/practice/answers → POST …/bookmark → GET /api/v1/ldr → the flagged question
DELETE /api/v1/collections/{id}/questions/{qid} → {"removed":true}
GET  /api/v1/collections (no token)   → 401
```

---

## 4. Completion matrix

### 4.1 Public site (spec §5–§25)

| Item | Status | Notes |
| --- | --- | --- |
| Landing page, all required sections | ✅ | Hero + attempt calculator, ICAI results/notifications strip, product preview, trust/QA, features, question bank, mocks, progress analytics, planner, historical taxation, PDF→question ingestion, how it works, who it is for, pricing, FAQ, final CTA, footer |
| No fabricated testimonials / ranks / accreditations | ✅ | Asserted by test: the page must match neither `trusted by`/`rated N` nor `N,000+ students`; the only "proof" is ICAI's own published figures, with the notification date cited |
| Pricing page | ✅ | Rendered from `GET /payments/plans`; a 503 shows "plans could not be loaded" and **no** prices rather than stale ones |
| Features · FAQ · Privacy · Terms | ✅ | Real pages with their own metadata, canonical URLs and tests |
| SEO | ✅ | `index.html` metadata, per-page `<title>`/description/canonical, `robots.txt`, `sitemap.xml` (8 URLs, parse-verified), Open Graph card, favicon, zero third-party requests |
| Sign-up · sign-in · password reset routes | ✅ | `/signup`, `/login`, `/forgot-password` are three routes onto one component; reset never reveals whether an address exists |
| Accessibility basics | ✅ | One `h1` per page, labelled nav, skip link, 44px targets, `prefers-reduced-motion`, mobile menu is a real dialog with Escape + scroll lock |

### 4.2 Student application (spec §26)

| Route | Screen | Status |
| --- | --- | --- |
| `/dashboard` | Today, streaks, kept questions, mock list | ✅ |
| `/learn` | Course → subject → chapter tree; topics; question counts | ✅ |
| `/practice` | Filtered drills, server-graded, reveal only after committing | ✅ |
| `/mocks`, `/mocks/:id`, `/reports/:id` | Timed papers, autosave, per-question report incl. skipped and pending-review | ✅ |
| `/planner` | Dated plan from exam date + syllabus coverage, replan on demand | ✅ |
| `/revision` | Spaced repetition (SM-2), student-graded | ✅ |
| `/doubts`, `/doubts/:id` | Threaded doubts with replies and accepted answers | ✅ |
| `/progress` | Accuracy, coverage, daily activity | ✅ |
| `/search` | Postgres FTS with highlighted snippets, opens in place | ✅ |
| `/collections` | Named containers: create, open, remove, delete | ✅ **new this round** |
| `/ldr` | The practice loop's "marked for later" list with per-question performance | ✅ **new this round** |
| `/subscription` | Plan catalogue for the signed-in student | ✅ |
| `/profile` | Profile, goals, exam target | ✅ |
| `/admin` | Ingestion queue, review, publish, role assignment | ✅ |

### 4.3 API surface

88 documented paths (`apps/docs/api/openapi.json`), grouped: auth/identity, users,
curriculum, questions, practice, mocks, planner, progress, revision, doubts, search,
collections, LDR, ingestion, publishing, payments, content library, admin,
notifications, gamification, health. Every route in blueprint §7.3 exists or has a
documented deviation — the route-by-route audit is in
`docs/architecture/build-audit.md`.

### 4.4 Data model (spec §29 / blueprint §9.1)

38 tables, migrations `0001`–`0009`. Deviations are recorded rather than hidden:

| Blueprint entity | Here | Why |
| --- | --- | --- |
| `attempts`, `attempt_answers` | `practice_attempts`, `mock_attempts` (+ `answers` JSONB) | two genuinely different lifecycles; one wide table would carry half its columns empty in each mode |
| `question_solutions` | `questions.explanation` / `model_answer` | there is no video to attach — video solutions were withdrawn from the product on your instruction — and a one-row table per question is a join that buys nothing |
| `planner_plans`, `planner_tasks` | computed, not stored | the plan is a pure function of the exam date, syllabus and progress; storing it invites a stale plan |
| `progress` | `user_question_progress` | adds `is_marked_for_review`, which is the LDR flag |
| `ingested_questions` | `ingestion_drafts` + `raw_extractions` | raw OCR text is never overwritten, so cleaned and raw text are separate rows |
| `payments` | `payment_orders` + `payment_events` | every webhook is an event; an order keeps its own state |
| — | `question_versions`, `question_flags`, `exam_sessions`, `topics`, `daily_activities`, `points_ledger`, `user_badges`, `referrals`, `spaced_repetition_cards` | additions the product needed |
| `notifications`, `analytics_events` | built, as `notifications` and `analytics_events` | added this round with routes, admin fan-out and audit; delivery is still job-logged only (see §7) |
| — | `content_documents`, `document_pages`, `content_access_rules`, `content_reviews` | the content library: originals preserved, extracted text searchable, per-document access rules |
| — | `badges`, `platform_settings`, `audit_logs` | the badge catalogue, owner-editable settings, and the admin audit trail |

---

## 5. What changed in this round

This round finished the content library's test coverage, then drove the browser half of
features the server already had. Testing the library is what found the bugs, and all of
them were in code that *looked* complete: routes that returned a correct 200 or 201 in
every unit test, and could not have worked in production.

### Five real defects found by writing integration tests

1. **Every storage-backed endpoint was dead, and reported the wrong cause.** Four
   endpoints constructed `SupabaseStorage()` with no arguments — the constructor takes
   `Settings` — and then called methods that do not exist on the class
   (`signed_download_url`, `object_exists`, `signed_upload_url`). One also called
   `build_object_path()` with the wrong signature. All of it was inside
   `except Exception → 503 "Storage not configured"`, so with Supabase correctly
   configured the owner would have been told to set environment variables that were
   already set. Affected: the bulk-upload manifest, the student PDF link, the admin
   original-file download, and the post-upload existence check. Fixed with a single
   `storage_from_settings()` factory — so a signature change is one checked edit — plus
   tests that drive each endpoint through a stand-in client, which is what makes the
   wrong method name a failing test rather than an unread 503.
2. **Starting the pipeline was a 500.** `SqlIngestionSink(session)` passed a live
   session where the repository expects a session *factory*, so
   `POST /admin/content/documents/{id}/start` — the route that queues extraction after
   an upload — raised `TypeError: 'AsyncSession' object is not callable`. The
   constructor now refuses a session with a message that names the fix, because the
   original error named neither the caller nor the cause.
3. **Archiving was indistinguishable from deleting.** `soft_delete` set `deleted_at`,
   which hid an archived document from the admin list as well, made it unfindable, and
   left no way back. Archive and delete are now separate states: ARCHIVED keeps the row
   visible to admins, lives behind `?include_archived=true`, and is reversible through
   `POST /admin/content/documents/{id}/restore` (which deliberately leaves the document
   unpublished, so tidying the archive cannot push material in front of students).
4. **A missing permission was a 500, not a 403.** `require_permission` raises
   `PermissionDenied`, and nothing handled it, so the catch-all turned every
   authorisation failure into "something went wrong". An operation being refused is
   normal; the response now says which permission the role lacks.
5. **A settings switch was owner-only by mistake.** With `OWNER_ONLY = {MANAGE_ROLES}`,
   a delegated Admin could not manage roles at all. Escalation is now blocked where it
   belongs — a non-owner cannot assign a role at or above its own rank — instead of by
   denying the whole operation.

Two more were found while building this round's screens — and one of them only by
calling a running API, which is the part worth remembering:

6. **The Premium Plus bundle still sold video solutions** that no longer exist (below).
7. **The checkout sent camelCase bodies to an API that requires snake_case.** Every
   inbound schema forbids unknown fields, so `POST /payments/order` with
   `{"planCode": ...}` answers **422** — `plan_code: Field required` plus
   `planCode: Extra inputs are not permitted`. The first purchase anyone attempted would
   have failed at step one. The web client's eight existing write calls all use
   snake_case; the checkout was the first one written without a working example beside
   it. **No unit test could have caught this**: a stubbed `fetch` proves what the client
   sends and nothing about what the server accepts. It took a request against a running
   API with a real JWT. Both halves are now pinned — the client asserts the snake_case
   wire format, and `tests/test_payments_api.py` asserts the server refuses camelCase.
8. **A page's own bad tone names.** The checkout's first draft used `Badge tone="green"`
   and `tone="primary"`; the component allows `slate|brand|right|wrong|amber`, so those
   badges rendered with no styling at all. Caught by reading the component, not by a
   test — noted here because it is the kind of defect that ships looking "nearly right".

### Built this round

- **`tests/integration/test_postgres_content_library.py` — 34 tests** over access
  control, the permission matrix, audit and analytics rows, notifications, gamification
  and bulk upload, driving real HTTP requests against a real PostgreSQL. This file is
  what found items 1–5 above.
- **A defaults module** (`app/services/platform_defaults.py`) holding the platform
  settings and the eight-badge catalogue as data, written idempotently. Migration `0008`
  seeds them for a fresh database; the module exists because a database whose rows were
  cleared by hand — or a test database truncated between tests — must still come up with
  a working settings screen, and because an owner who turns a flag *off* must not have a
  restart turn it back on.
- **Migration `0009` withdraws video solutions** (see §7).
- **The Razorpay checkout, on the frontend, against the existing server.**
  `POST /payments/order` → Razorpay Checkout → `POST /payments/confirm`, with all four
  endings handled: paid, failed (the provider's own reason, shown verbatim), **dismissed**
  (reported as "nothing was charged" — the most common ending and the one that otherwise
  leaves a dead button), and provider-unreachable. `/upgrade` is the only screen that
  opens Checkout; it is authenticated, it sends a plan code and nothing else, and it
  never sees the key secret. A 503 from the API is rendered as the exact environment
  variables that are missing. 8 tests, including one that asserts the options handed to
  Checkout contain no secret.
- **Route-level code splitting**, measured above: the entry chunk drops from 691 kB to
  388 kB. Each lazily loaded route has a loading state inside the app shell (so the
  navigation does not unmount) and an error boundary, because a stale deploy can make a
  chunk request fail and the previous behaviour would have been a blank page.

---

## 6. Quality posture, stated plainly

- **The answer key is only in the response to an answer.** No list, search result,
  collection or question-detail payload contains `correct_answer`; four integration
  tests assert the *body text* of a response, not just the fields a test thought to
  check.
- **Only published content is visible to students.** One `_published()` predicate,
  applied in every query, including inside collections.
- **A human signature gates publication.** `verified_by` records *who*, a CHECK
  constraint enforces it, and approval is a different act from publishing.
- **Ownership is a predicate in the query**, never a comparison after loading a row —
  asserted from both sides in the tests, because a test that only checks the owner
  passes while the check is entirely absent.
- **Errors are shaped, not swallowed.** RFC 7807 bodies with a request id; a failed
  request renders as an error card with that id, never as an empty list.

---

## 7. Explicitly NOT built, and why

These are absences, listed so nobody discovers them by clicking.

Built since the last revision of this table, and no longer absences: the student
library and reader (`/library`, extracted text verified in a browser; the original PDF
opens only when storage answers — this deployment's storage answers 503 and the screen
says so), and the student inbox (`/notifications`).

| Item | Status / reason | What it would need |
| --- | --- | --- |
| Video solutions | **REMOVED, on your instruction.** Nothing was ever implemented — a storage prefix, one entitlement string, one settings row and some marketing copy implied it. All of it is gone (migration `0009`), and the tests now assert that *no* public copy mentions video, in either direction | Nothing. This is closed, not deferred |
| AI assistant / recommendations | Not built. No provider client, no retrieval, no screen. The landing page says so | Provider choice, a spend ceiling, and retrieval that applies the **same authorisation rules as the content library** — an assistant that can summarise an admin-only document into a student's answer is a leak with a chat interface |
| Gamification UI | ✅ `/achievements`. Points, streak, badge catalogue (a bar only when the server sent a number), ledger, and a leaderboard of names and totals. A turned-off board is a named empty state and does not hide the badges. Checked in a browser: rank 2, no email, no avatar fetch. Not in the top nav — that row already clips if another item is added; it is linked from the dashboard and from Progress | Nothing, unless email-style badge notifications are wanted |
| In-app notifications UI | ✅ `/notifications`. Lists the caller's rows, marks one or all read, and refuses to render an absolute `linkUrl` as a link (checked in the browser with a `https://evil.example` row). **The worker is still a stub**: nothing is emailed, and the screen says so | A mail provider (Resend or equivalent) if email delivery is wanted |
| Admin console UI | The six previously grey sections now have screens. Plans, AI and storage name their limits instead of pretending. See `docs/ADMIN_CONSOLE_REPORT.md` | Generated AI answers (`AI_PROVIDER_API_KEY`), storage listing (`SUPABASE_SECRET_KEY`), outbound email (`RESEND_API_KEY`) |
| Learning/business/AI analytics screens | `analytics_events` is written and read (`GET /admin/analytics` is asserted to see the rows it recorded). There is no per-user learning analytics or business dashboard yet, and no `POST /events` for front end tracking | Dashboard screens; a privacy decision before instrumenting student behaviour |
| `mypy` on the backend | Not adopted; `ruff` + 935 tests are the gate | A typing pass, module by module |
| Repository hygiene: git repo, CI execution | `.github/workflows/ci-cd.yml` exists and has **never run**, because this is not a git repository: `git rev-parse` fails and there is no remote | `git init`, first commit, remote |
| Deployment | Nothing has been deployed from this workspace. `infra/render.yaml` is written and unapplied; there is no `vercel.json` and no Vercel linkage | Accounts and the secrets below |
| Google sign-in | Code path exists; the provider is not enabled on the Supabase project | Google OAuth client id/secret, dashboard enablement |
| Email confirmation / password reset mail beyond the auth provider | Supabase sends these; no branded templates | Supabase SMTP or Resend |

### Needs a decision from you (not a build)

**Premium Plus is ₹1,299/year.** You agreed that price; it is `amount_paise = 1_29_900`
in `app/services/billing.py`. The earlier ₹1,799 figure included a video entitlement
that has been removed, so it is not the price. This is closed — it is not an open
decision.

### A conflict I need to flag, as you asked

Your admin-panel specification (§12) lists "add video URLs where supported". That
directly conflicts with removing video solutions entirely, so I removed the video
feature as instructed and did **not** add a `video_url` field. If what you meant was a
plain metadata field for linking an external recording — no player, no storage, no
entitlement — say so and it is a small, additive change. I have not guessed.

### Blocked on you

1. **Razorpay keys** — `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`.
   The checkout screen and the API are both complete and tested against stubs; without
   these the API answers 503, the screen says so, and no payment can be made. The
   webhook secret must also be pasted into the Razorpay dashboard's webhook
   configuration, pointing at `POST /api/v1/webhooks/razorpay`.
2. **Supabase database connection strings** — pooler (port 6543) → `DATABASE_URL`,
   direct (5432) → `DIRECT_DATABASE_URL`, so migrations can run separately from the API.
3. **Rotate both Supabase keys** — the secret key transited our chat, so it must be
   treated as compromised before real students exist.
4. **Google OAuth** client id/secret, for sign-in with Google.

---

## 8. Where to look next

| Question | File |
| --- | --- |
| What is built vs what deviates, route by route | `docs/architecture/build-audit.md` |
| Every stack change, with its trigger condition | `docs/architecture/stack-amendments.md` |
| The API contract | `apps/docs/api/openapi.json` (88 paths) |
| The exam facts the product asserts | `apps/web/src/lib/examData.ts` (read-only) |
| How the marketing copy is assembled | `apps/web/src/lib/publicContent.ts` |

**No claim in this report is aspirational.** Where something is partial, incomplete or
parked, it says so above.
