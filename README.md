# CA Prep Platform

Exam-preparation platform for CA (Chartered Accountancy) students: a verified
question bank with previous-year papers, practice with spaced repetition, mock
exams with analytics, and a study planner.

The authoritative specification is
`uploads/CA_Preparation_Platform_Final_Master_Blueprint.pdf` (v3.0). Where this
README and the blueprint disagree, the blueprint wins — and the disagreement
should be fixed here.

---

**Delivery report:** [`docs/DELIVERY_REPORT.md`](docs/DELIVERY_REPORT.md) — what is built, what is verified, and what is not built yet.

## Stack

Per blueprint v3.0 §2 and §24. This version **supersedes** the earlier
Next.js / NestJS / Prisma / BullMQ / AWS-auth stack; do not mix the superseded
choices into this codebase.

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + Tailwind CSS → Vercel |
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic → Render |
| Database | PostgreSQL — Supabase Postgres in every environment (system of record) |
| Cache / queues | Redis + RQ |
| Auth | **Supabase Auth** — access tokens verified against the project JWKS |
| File storage | Supabase Storage (private buckets, backend-brokered signed URLs) |
| PDF / OCR | PyMuPDF → pdfplumber → pdf2image + Tesseract |
| Payments / email / analytics | Razorpay · Resend · PostHog · Sentry (optional) |
| Search | PostgreSQL full-text search first — **no search server yet** |

**Deliberate anti-goals.** No microservices, no Kubernetes, no separate OCR
service, no vector database, no Redis as a source of truth. Each is deferred
behind a stated trigger rather than rejected outright; see the blueprint's scale
section before adding one.

Retired code is kept for reference only in `_archive_v2_nestjs_stack/`.

**Deviations from the blueprint are recorded, not silent.** Authentication is
Supabase Auth, not Firebase: one vendor for identity, database and files means one
signing key, one dashboard, and no cross-vendor JWT exchange. See
[`docs/architecture/stack-amendments.md`](docs/architecture/stack-amendments.md)
(SA-09; SA-05 superseded, SA-08 amended) for the reasons, what changed for storage
access, and the cost — Supabase is now a single point of failure for sign-in.

---

## Repository layout

```
apps/
  api/                 FastAPI service + RQ worker
    app/
      api/v1/          HTTP routers (health, mocks, planner, ...)
      core/            config, security, envelopes, dependencies
      models/          SQLAlchemy 2.0 models
      schemas/         Pydantic request/response models
      services/        domain logic - pure, unit-tested
      repositories/    data access
      workers/         RQ job definitions and worker entrypoint
      ocr/             PDF extraction and segmentation
      integrations/    Supabase Storage, payments, email
    alembic/versions/  migrations
    tests/             pytest suite
  web/                 React + TypeScript + Tailwind (Vite) - landing page, auth, planner
infra/
  docker/              local Postgres + Redis
  render.yaml          Render blueprint (API + worker + database)
docs/
  product/  architecture/  api/  runbooks/
```

---

## Local development

### Prerequisites

Docker, Python 3.12+, Node 20+ (frontend only).

The OCR path additionally needs two **system binaries**, not just the Python
packages. `pip install pytesseract` succeeds without them, and the failure
surfaces only at runtime on the first scanned PDF:

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng poppler-utils
```

### Backend

```bash
cp .env.example .env                      # then fill in the Supabase values
docker compose -f infra/docker/docker-compose.yml up -d

cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

alembic upgrade head
pytest
uvicorn app.main:app --reload --port 8000
```

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>, `/health/db`, `/health/redis`, `/health/storage`

### Worker

Ingestion is asynchronous, so PDFs are not processed by the API process:

```bash
cd apps/api && python -m app.workers.rq_worker
```

### Ingestion flow

| Step | Endpoint | Notes |
| --- | --- | --- |
| 1 | `POST /api/v1/ingestion/uploads` | Editor+. Validates the file and mints a 5-minute signed upload URL. The API never receives the bytes. |
| 2 | *(client)* `PUT` to the signed URL | Straight to the private `question-pdfs` bucket. |
| 3 | `POST /api/v1/ingestion/jobs/{id}/start` | Confirms the object exists, then queues extraction. 409 if the upload never landed. |
| 4 | `GET /api/v1/ingestion/jobs/{id}` | Poll the stage; `isTerminal` tells the client when to stop. |
| 5 | `GET /api/v1/ingestion/drafts` | The QA worklist. PENDING drafts only. |
| 6 | `POST /api/v1/ingestion/drafts/{id}/review` | `APPROVE` (Content Manager+) creates a **DRAFT** question; `REJECT`/`DUPLICATE` (Editor+) close the draft. |

The pipeline stops at `AWAITING_QA` and never publishes. A published question
requires a `verified_by` user id, and unattended automation has no legitimate one
to claim — so "extraction never publishes" is structural, not a policy.

### Checks

```bash
cd apps/api
ruff check app tests        # lint
ruff format --check app tests
mypy app                    # advisory while the strict baseline is adopted
pytest --cov=app
```

### Supabase Storage configuration

Storage is Supabase, and so is auth — which means Storage RLS *could* work, and is
still deliberately not the mechanism. Row-level policies would split
authorization across Postgres policies and Python: two places to get it wrong,
two places to audit, and a policy that silently matches nothing reads as
protection that is not there. The API stays the only boundary, and every upload
and download is brokered as a short-lived signed URL (SA-09).

`SUPABASE_SECRET_KEY` holds the current opaque secret key (`sb_secret_...`) and
lives only in the backend `.env` (gitignored) or Render's environment. The legacy
`service_role` JWT also works in that variable; the client detects the format.

**The two key systems authenticate on different headers**, which is the trap here:

| Headers sent | Result (verified against the live project) |
| --- | --- |
| `apikey: sb_secret_...` | **200** |
| `Authorization: Bearer sb_secret_...` | **403 `Invalid Compact JWS`** |

The new keys are not JWTs, so a client that sends only a bearer token works with
a legacy key and fails with every current one. `SupabaseStorage._build_headers`
picks the header set from the key format.

Buckets are `question-pdfs`, `question-media` and `user-uploads` — all private,
50 MB limit. Bucket **names** cannot contain `/` (`400 InvalidBucketName`); the
blueprint's `originals/` and `processed/` folders are path prefixes within
`question-pdfs`.

The **publishable key is now shipped to the browser, and that is the whole point.**
Sign-in happens in the browser, so `@supabase/supabase-js` cannot initialise
without it. The boundary is narrower than it looks and is asserted by tests: the
browser talks to the **Auth API only** — never PostgREST, never Storage — and the
publishable key, checked against the live project, lists **zero** storage buckets.
A signed upload URL remains a self-contained bearer capability, so file access
still needs no key at all. The secret key (`sb_secret_...`) is Postgres
`service_role` with `BYPASSRLS` and stays server-side; a test reads the built
bundle and fails if it appears there.

### Supabase Auth configuration

| Setting | Where it lives | Used by |
| --- | --- | --- |
| `VITE_SUPABASE_URL` | `apps/web/.env.local`, Vercel | browser SDK |
| `VITE_SUPABASE_ANON_KEY` | `apps/web/.env.local`, Vercel | browser SDK (publishable key) |
| `SUPABASE_URL` | root `.env`, Render | JWKS + issuer for token verification |
| `SUPABASE_SECRET_KEY` | root `.env`, Render (**never** the browser) | Storage, admin calls |
| `DATABASE_URL` / `DIRECT_DATABASE_URL` | root `.env`, Render | API (pooler 6543) / Alembic (direct 5432) |

`SUPABASE_URL` in the backend and `VITE_SUPABASE_URL` in the browser **must name
the same project**: the API verifies the issuer *and* the audience of every access
token, so a mismatch means sign-in succeeds in the browser and every subsequent
API call 401s. `tests/test_infra_contract.py` asserts the two agree across
`.env.local`, `.env.example`, `infra/render.yaml` and CI.

The browser values are public by design — the URL identifies a project and the
publishable key authorises nothing beyond the Auth API. Access control is the
API's own role and entitlement checks, never the client.

**Three dashboard settings must be made by hand; none of them is visible from the
code.**

1. **Google provider** — Authentication → Providers → Google. Sign-in methods are
   email/password *and* Google, and the project currently has **email only**:
   Supabase answers `400 provider is not enabled` until a Google Cloud OAuth
   client id and secret are pasted in. The app detects this specific failure and
   prints the exact steps, rather than reporting "sign-in failed".
2. **Redirect URLs** — Authentication → URL Configuration. Add
   `<origin>/auth/callback` for **every** origin you use: `http://localhost:5173`,
   each preview host, and production. An origin missing from this list is
   redirected to the Site URL instead, which lands the student somewhere they did
   not ask to be — a failure that looks like a broken button.
3. **Google Cloud redirect URI** — the OAuth client's authorised redirect URI is
   `https://<project-ref>.supabase.co/auth/v1/callback`, i.e. Supabase's callback,
   not the app's. Pasting the app's own `/auth/callback` here produces
   `redirect_uri_mismatch` at the final step of the Google flow.

**Production deployment** needs `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`
set in Vercel (`.env.local` is gitignored and never deploys). Vite inlines them at
build time, so a missing value is baked into the bundle as `undefined` and the app
throws on load rather than at the first sign-in.

**No analytics SDK is initialised.** Product analytics in this stack is PostHog,
and the Supabase client is created with the auth options only — one tracking
stack, one consent surface. See `src/lib/supabase.ts`.

### Running the whole stack locally

`scripts/dev-stack.sh` is the recovery script for this environment, and it is the
written-down form of a setup that used to be rebuilt by hand every session:

```bash
bash scripts/dev-stack.sh prepare     # python deps + embedded PostgreSQL 17 + initdb
bash scripts/dev-stack.sh up          # postgres in the FOREGROUND on 5433
bash scripts/dev-stack.sh db-create   # caprep + caprep_test, migrate to head, seed
bash scripts/dev-stack.sh test        # full backend suite against caprep_test
bash scripts/dev-stack.sh api         # uvicorn on 8000
bash scripts/dev-stack.sh web         # vite on 5173
```

There is no Docker daemon and no system PostgreSQL in this sandbox, so the script
installs Zonky's relocatable PostgreSQL 17 binaries and runs them as an
unprivileged user. That is what makes the migration and database-backed tests real
rather than hypothetical: `alembic upgrade head`, `alembic check` and 30-odd
integration tests all execute against a server.

Two server semantics worth knowing: `up` execs postgres in the **foreground** (a
process supervisor needs one foreground command per service), and a second
`up` aborts on `postmaster.pid` — a listening 5433 already means it is running.

---

## Conventions that are load-bearing

These are not style preferences. Each exists because its absence caused a
specific defect, and each is asserted by a test.

**1. Inbound schemas use `StrictRequest` (`extra="forbid"`, `strict=True`).**
Pydantic v2's default lax mode coerces: `"yes" → True`, `"5" → 5`. The
validators this stack replaced rejected those. A plain port would silently
accept *more* than the code it replaced.

**UUID fields must use `UuidRef`, not bare `uuid.UUID`.** JSON has no UUID type,
so a client can only send one as a string — and strict mode rejects that with
`is_instance_of`. A bare UUID field therefore passes every test that builds the
model with a real UUID object and fails against every real HTTP client.
`UuidRef` relaxes exactly that one field; `int` still rejects `"5"` and `bool`
still rejects `"yes"`.
→ `app/schemas/base.py`, `tests/test_api_contract.py`

**2. Health checks return HTTP 503 when a dependency is down.**
A health endpoint reporting `{"status":"ok"}` while the database is unreachable
is worse than none, because the monitor stays green. A missing *optional*
integration reports `not_configured` and 200 — an outage and a not-yet-enabled
feature are different things.
→ `app/api/v1/health.py`, `tests/test_api_contract.py`

**3. Identity comes only from the verified JWT `sub`.**
Never from a query string, body or custom header. Algorithm is pinned (no
`none`, no HS256) and the audience is verified.
→ `app/core/security.py`, `tests/test_security.py`

**4. Instants are `TIMESTAMPTZ`; arithmetic is done in UTC.**
The earlier spaced-repetition code built a UTC instant through local-time
arithmetic and a zero-indexed month, so reviews landed on the wrong day at month
and year boundaries.
→ `app/services/spaced_repetition.py`, `tests/test_spaced_repetition.py`

**5. The planner tells the truth when the syllabus does not fit.**
It never schedules more study minutes than the calendar holds. When it cannot
cover everything it returns an explicit `coverageWarning` and the list of dropped
chapters.
→ `app/services/study_planner.py`, `tests/test_study_planner.py`

**6. Raw extracted text is never overwritten.**
Cleaned text is stored separately, so a bad question can be traced back to
extraction versus editing.
→ `app/ocr/extractor.py`

**7. No student passwords in PostgreSQL.**
Supabase Auth owns credentials. There is no password column, and a test asserts
none can be added without failing.
→ `tests/test_schema_contract.py`

**8. Approving a draft never publishes a question.**
Promotion writes a row in `DRAFT` with no verifier. Publishing is a separate act,
gated by `ck_questions_published_requires_verifier`. Clearing an OCR backlog is
triage, not content sign-off — the two must not be conflated, or one click turns
a mis-read marks value into a student's scorecard.
→ `app/services/draft_review.py`, `tests/test_draft_review.py`

**9. The application role is read from a server-set claim only.**
This is a regression guard that has now survived two identity providers. An
earlier implementation read the role from Supabase's `user_metadata`, which the
authenticated user can write — a student could have promoted themselves to ADMIN
with a validly-signed token. Supabase's `app_metadata` is writable only through
the Admin API (which needs the secret key), so the role is read from
`app_metadata.role` then `https://caprep.in/role`, and a mutation test proves the
guard catches a reintroduction of the readable path.
→ `app/core/security.py`, `tests/test_security.py`

**10. An identity is `auth_user_id`, never an email.**
The verified `sub` claim is what every row is keyed on. Emails are recycled
between people and one address can legitimately appear under two subjects (a
password account and a Google account), so keying an account on the address would
either merge two students or break the second sign-in. The address is stored for
display and notification routing only.
→ `app/core/identity.py`, `tests/integration/test_identity_provisioning.py`

**11. A suspended account stays suspended.**
`is_active` and `deleted_at` remove an account from circulation, and both are
checked where identity is resolved — not by whichever caller remembers. A
deactivated student's next request is answered **403**, not with a freshly
provisioned account: a control the subject can undo by retrying is not a control.
→ `app/repositories/users.py`, `tests/integration/test_identity_provisioning.py`

---

## Landing page and the static preview

`/` is public. The signed-in dashboard moved to `/dashboard` for two reasons: a
visitor typing the bare domain should see the product rather than a login wall,
and a referral link to `/` should not drop a prospective student into an auth
form. `ProtectedRoute` still guards `/dashboard` and `/planner` and redirects to
`/login`.

The page has one deliberate structural feature worth knowing about before
editing: **the attempt-feasibility calculator is the hero element, not a
screenshot.** `src/lib/attemptPlan.ts` is the same feasibility rule
`app/services/study_planner.py` applies on the server, reduced to the part that
needs no account. Its design rule is to refuse to flatter - the widget reports a
shortfall and names the papers that will not fit rather than always returning
"on track", because the flattering version produces a plan the student
discovers is impossible with a month to go.

Two honesty constraints are enforced by tests rather than by review:

- Study-hour figures are labelled as **our estimates**, never as ICAI data.
- The exam mode is never stated. Pen-and-paper vs computer-based is still
  unresolved (see Known gaps), and `landing.test.tsx` fails if the page asserts
  either.

```bash
cd apps/web
npm run preview:static   # -> landing-preview.html, self-contained, verified
```

The preview is a review artefact, not a second deployment target: it is
gitignored and regenerated on demand, because a committed copy of a build output
goes stale silently.

---

## API conventions (blueprint v3 §7.2)

Base path `/api/v1`.

```jsonc
// success
{ "data": { ... }, "meta": { "requestId": "..." } }

// list
{ "data": [ ... ],
  "meta": { "requestId": "...", "total": 150, "page": 1, "limit": 20, "hasMore": true } }

// error (RFC 7807-style)
{ "type": "https://api.caprep.in/errors/validation",
  "title": "Validation Failed", "status": 422, "detail": "...",
  "errors": [{ "field": "marks", "message": "must be positive" }] }
```

Health endpoints are mounted at the root, not under `/api/v1`, so infrastructure
probes do not depend on the API version.

---

## Deployment

Vercel (frontend) + Render (API, worker, PostgreSQL). Render is configured from
`infra/render.yaml`.

Four things to know before the first deploy:

1. **The RQ worker must be a Render `worker`, not a `web` service.** A worker
   never opens an HTTP port, so a Web Service health check probes a port that
   does not exist and the deploy fails with a message that never mentions RQ.
2. **The worker needs a Docker build, not Render's native Python runtime.** The
   OCR pipeline requires the `tesseract-ocr` and `poppler-utils` system
   binaries; `pip install` alone is not enough. See `apps/api/Dockerfile`.
3. **Point the redirect allow-list at the deployed origin.** Supabase →
   Authentication → URL Configuration must list `<production-origin>/auth/callback`
   as well as localhost, or Google sign-in returns the student to the Site URL
   instead. This is the deployment step that is invisible until someone tries to
   sign in on the deployed domain.
4. **No Storage RLS policies, by design.** The API is the single authorization
   boundary and brokers short-lived signed URLs; a bucket policy would be a second
   boundary to keep correct. Buckets are private and the publishable key lists
   none of them. See SA-09.

Migrations run via `preDeployCommand`, not at application startup — with more
than one instance, startup migrations race. `DIRECT_DATABASE_URL` (port 5432)
must be set for that step: a transaction-mode pooler cannot hold the session
`alembic upgrade` needs.

---

## Status

**Built and tested** — `apps/api`:

- **827 passing tests, 5 skipped** against a real PostgreSQL 17.11 (the skips are
  environmental: Tesseract absent, no built web bundle, no Supabase DB URL in the
  checkout). Without a database the same suite runs 698 tests, 110 skipped — the
  database-backed ones are skipped, never silently dropped
- Domain services: ingestion pipeline, spaced repetition (SM-2), study planner,
  gamification, mock scoring, smart collections
- Schema: 28 tables across migrations 0001–0006, validated against the migration
  with zero drift (`alembic check`), including the required index set, the taxation
  compliance constraints, the partial unique indexes that make a second active
  subscription impossible, and `ck_questions_published_requires_verifier` /
  `ck_questions_objective_requires_answer`
- **Auth: Supabase JWKS verification** with algorithm pinning (ES256/RS256, no
  `none`, no HS256), issuer and audience checks, `exp`/`iat`/`sub` required, and
  the role read only from a server-set claim. The publishable key and a
  wrong-issuer token both answer 401, verified against the live project
- **Identity provisioning, live-verified:** the first authenticated request creates
  the platform row in a SAVEPOINT, so two simultaneous requests converge on one
  account instead of one 500. A suspended or erased account is refused with 403
  rather than re-provisioned
- **Mock lifecycle, live-verified end to end:** start writes or resumes an attempt
  row (premium gate included), the paper is returned without the answer key,
  submission is server-scored with rank and percentile, a past-deadline submission
  is auto-submitted, a resubmission is 409, and the report is 404 for an unknown
  attempt and 409 while one is in progress
- Storage verified against the live project — signed upload from an unauthenticated
  client, brokered read, byte-identical download, delete; private buckets confirmed
  unreachable without a signature
- PDF ingestion: tiered extraction, quality gate, OCR fallback, segmentation, QA
  worklist — runs unattended and stops at `AWAITING_QA`, never publishing
- Payments (§13.1): server-priced orders, signed browser confirmation, and a
  signature-verified, idempotent Razorpay webhook. The price never comes from the
  client, a payment is always read back from the gateway before anything is
  activated, and `payment_events.event_id` makes a replayed webhook a no-op rather
  than a second year of access. 14/14 mutations of the money path are caught.
  **Inert until keys are set**: with no `RAZORPAY_*` values the endpoints answer
  503 by design rather than half-working
- Health endpoints, response envelopes, RFC 7807 errors
- `docs/api/openapi.json`, generated from the app and held in sync by a test, so a
  client can be written against a file instead of a running server
- RQ worker and job definitions; CI runs lint, format, typecheck, migration
  round-trip and coverage

**Built and tested** — `apps/web`:

- React 19 + TypeScript + Tailwind v4, built with Vite 8
- **Supabase Auth**: email/password and Google sign-in, with the failure modes
  translated into the exact dashboard step that fixes them (provider not enabled,
  redirect URL not allow-listed, `redirect_uri_mismatch`)
- Typed API client covering the v3 envelopes and RFC 7807 errors, with field errors
  mapped for inline form rendering
- Every study screen implemented and routed: dashboard, syllabus, practice
  (linkable by `?course=&subject=&chapter=`), revision, doubts, progress, mock
  papers (list, timed sitting, report) and search, plus profile and plans. Six of
  them were rewritten this round to satisfy the React compiler's purity and
  effect rules rather than to suppress them
- Public landing page: a working attempt-feasibility calculator, ICAI pass
  percentages with their sessions and candidate counts, and a labelled roadmap of
  what is not built
- Google sign-in carries the official "G" mark as an inline SVG in the four brand
  colours, held in place by a test that transcribes Google's published values
  independently of the component
- **123 passing tests across 7 files**: the API client, the feasibility model, the
  landing page, sign-in, and the study screens — which assert the properties a
  screenshot cannot show (no answer key before an answer, an option graded from the
  server's flag, a search snippet rendered as text so markup cannot execute, and a
  report that keeps un-attempted and pending-review questions distinct)
- `tsc -b`, ESLint and the production build are all clean
- `npm run preview:static` emits `landing-preview.html` — the real landing and
  sign-in screens with the stylesheet and bundle inlined into one file, verified by
  rendering both in jsdom and driving the page switcher. It opens from disk with no
  server, no network and no Supabase project, so a non-engineer can review it

### Remaining, and why

`docs/architecture/build-audit.md` carries the full audit — every decided item,
whether it exists, and the reason for anything that does not. The short version:

| Remaining | Why |
| --- | --- |
| **Google provider not enabled in Supabase** | A dashboard action with a Google Cloud OAuth client; the code path is written and its failure is reported precisely |
| **Supabase database connection string** | Not derivable from the API keys — the DB password is a separate secret. Needed before the API can use Supabase Postgres instead of local PostgreSQL |
| Razorpay keys | Only you can create them; until then payments answer 503 |
| End-to-end payment run | Needs those keys and a public webhook URL |
| Redis / Tesseract | Not installable in this sandbox; the code paths are tested by contract and the binaries are in the worker image |
| Role assignment endpoint | Every user is provisioned STUDENT. `BOOTSTRAP_ADMIN_EMAILS` is the only route to staff today, and it is deliberately empty by default |
| Turborepo root, git repository | Never added; CI has therefore never run in this workspace |
| mypy | Advisory in the stack document, never installed |
| Exam mode (pen-and-paper vs CBT) | Sources conflict, so the page claims nothing |

**Next:**

1. Enable the Google provider in Supabase and paste a Google OAuth client — the
   one sign-in method that is specified but not yet live.
2. Send the Supabase database connection string (pooler URL on 6543 into
   `DATABASE_URL`, direct on 5432 into `DIRECT_DATABASE_URL`); the migrations are
   already at head against an identical schema locally.
3. Rotate both Supabase keys — they have transited a chat window, and both were
   re-pasted during setup.
4. Role assignment endpoint: the one thing that blocks editorial work, since draft
   review needs an Editor to triage and a Content Manager to approve.

### Known gaps

- **An actual sign-in has never been observed.** The configuration is real, the
  project answers, and the API rejects a token from any other issuer — but no
  browser has completed the flow, because there is no browser in the build
  environment and the in-app preview is a sandboxed iframe with no network access.
  Confirm it once in a real browser tab at `http://localhost:5173`. Until then
  "auth works" is an inference from configuration plus negative tests, not an
  observation.
- **Google sign-in is specified but not live**: the Supabase project's enabled
  providers are `["email"]`. See the dashboard steps above.
- **Both Supabase keys transited a chat window** and should be rotated in the
  dashboard (Project Settings → API Keys) before this serves real students. A
  credential that has been transmitted through a chat is best treated as exposed.
  Rotating is non-disruptive: no JWT secret changes, so no sessions are
  invalidated.
- **The database still has to be pointed at Supabase.** Everything runs against
  embedded PostgreSQL 17.11 locally, with the same migration head, so the move is a
  connection-string change rather than a schema project — but it is a change
  nothing has yet executed against the managed service.
- **`alembic revision --autogenerate` drift-checking** runs locally now and is
  clean; what CI has never done is run it, because this workspace is not a git
  repository and the workflow has therefore never executed.
- **`SELECT ... FOR UPDATE` in `SqlDraftReviewStore.promote` is untested under real
  concurrency.** It is the mechanism that stops two editors creating two questions
  from one draft, and single-threaded tests cannot demonstrate it. The one place
  real contention IS covered is identity provisioning, where three concurrent
  requests are asserted to converge on one row.
- **The landing page calculator is client-side**, so its estimates are not
  persisted and not personalised beyond its inputs. Making it authoritative means
  calling the planner service once the student has an account; the landing page is
  the honest preview, not the record.
- **Exam-mode research (pen-and-paper vs computer-based) is unresolved.** Sources
  conflict and ICAI's own pages were not conclusive; the page therefore asserts
  neither, and a test fails if it starts to.
- **No end-to-end test spans the two halves.** Backend and frontend are tested
  separately; nothing yet proves that a token issued by Supabase Auth is accepted
  by the API over a real network. Every piece of that chain has been verified
  independently — the API accepts tokens from the live project's JWKS and rejects
  a wrong issuer, and the browser sends the access token on every request — but the
  join is unproven.
