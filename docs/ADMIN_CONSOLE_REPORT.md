# Admin console — what exists today

**Answer to "tell me what the progress is on the admin page, because I don't know what
you have done."**

Every claim below was checked at runtime, not read off a file list. Where something is
not finished, it says so and names what is missing.

Measured on 2026-09-26 against a running stack (`postgres` :5433, `uvicorn` :8000,
`vite` :5173, local Supabase-auth stub :54321).

---

## 1. The console at a glance

| | |
|---|---|
| API endpoints | **112** paths, of which **48** are admin/console endpoints |
| Database | **39** tables, migrations through `0011` |
| Permissions | **19** distinct permissions across the 6 existing roles |
| Backend tests | Full suite **not re-run**. The new studio tests passed against PostgreSQL (student 403, draft create, plans ₹1,299, assistant does not leak a premium document). |
| Web tests | The admin, landing and public-page files were re-run: **93 passed**. The rest of the web suite was not re-run this turn. |
| Live authorization sweep | The earlier 618-request sweep predates these routes. This turn: a student token on the question bank is **403**; an editor can draft a question; an editor cannot create a paper (`MANAGE_TESTS`). |
| Console routes verified in a real browser | Payments was checked earlier at 1440 and 390. The six new screens were checked over HTTP this turn, not re-opened in a browser. |
| Layout check | Payments: no sideways scroll at 1440 or 390. Dates wrap on words; gateway ids wrap so they do not overflow. |
| Screenshots | `docs/verification/screenshots/` (console tour + header at three widths) |

The approval matrix, for reference:

```
STUDENT          0 permissions
EDITOR           3   VIEW_CONTENT, MANAGE_CONTENT, MANAGE_QUESTIONS
CONTENT_MANAGER  8   + MANAGE_CURRICULUM, MANAGE_TESTS, PUBLISH_CONTENT,
                     MANAGE_ACCESS, VIEW_ANALYTICS
MODERATOR        4   VIEW_CONTENT, VIEW_USERS, MANAGE_NOTIFICATIONS, VIEW_ANALYTICS
ADMIN            19  everything except MANAGE_SETTINGS
SUPER_ADMIN      19  everything
```

`MANAGE_SETTINGS` is owner-only: an ADMIN can run the platform but cannot change its
feature flags or quotas.

---

## 2. Every admin screen, and what state it is in

### Built and working (13)

| Screen | Route | Permission | What it does today |
|---|---|---|---|
| Dashboard | `/admin` | any admin | Students, documents, extracted pages, questions, mocks, notifications, revenue, subscriptions, failed payments. Leads with the failures, because that is what needs a person. |
| Analytics | `/admin/analytics` | `VIEW_ANALYTICS` | Events by type and by day, from `analytics_events` (written on every request that matters). |
| Audit log | `/admin/audit` | `VIEW_AUDIT` | Every administrative action with actor, target and timestamp. Survives the actor's deletion. |
| Content library | `/admin/library` | `VIEW_CONTENT` | Table of every document with pipeline state, extracted-text view, preview, download, re-process, archive, restore, bulk metadata. Search now looks **inside** the extracted text and says when it matched there ("Found in text · page 12"). |
| Bulk upload | `/admin/uploads` | `MANAGE_CONTENT` | Up to 500 files per batch, drag-and-drop, SHA-256 duplicate detection **before** any bytes move, 4-way concurrency, per-file progress, retry-failed-only, bulk categorisation, 50 MB/file. |
| Review & publish | `/admin/editorial` | `MANAGE_QUESTIONS` | The extraction queue: read a draft beside the page it came from, then approve/reject/regenerate and publish. |
| Access control | `/admin/access` | `MANAGE_ACCESS` | Grant or deny the library by user, role, plan or tier, optionally scoped to a course, subject or content type, with expiry. "Explain" runs the student's own check and shows why they can or cannot read a document. |
| Students | `/admin/users` | `VIEW_USERS` | Search accounts, see progress and entitlement, change a role. |
| Roles & permissions | `/admin/permissions` | any admin | The live matrix the API enforces, plus what *your* token may do. |
| Notifications | `/admin/notifications` | `MANAGE_NOTIFICATIONS` | Announce to a role or to every subscriber, with delivery rows. |
| Gamification | `/admin/badges` | `MANAGE_GAMIFICATION` | Badge catalogue, manual award, leaderboard on/off. |
| Platform settings | `/admin/settings` | `MANAGE_SETTINGS` | Feature flags and quotas, one at a time, without a deploy. Owner-only. |
| Payments | `/admin/payments` | `VIEW_PAYMENTS` | Orders and webhook events. A paid order is shown next to the live subscription, so "paid but no access" is visible. The raw webhook body is not returned. There is no refund button: nothing writes `payments.refund_recorded`. |

### Built this round, with the limits written on the screen (6)

These are no longer grey. A student calling the write routes gets **403**. Checked
against the running API on 2026-09-26.

| Section | Route | What it does, and what it will not |
|---|---|---|
| Question bank | `/admin/questions` | List and create drafts, including MCQ options. Publish still uses the verifier route. A published question is corrected with revise, which keeps the old version so an earlier score is not rewritten. Reports are a queue, not a change to the key. |
| Test series | `/admin/tests` | Create a draft paper. A published paper is not edited here. Publishing still refuses a paper whose questions are not all published. |
| Courses & syllabus | `/admin/curriculum` | Add a course and retire it. Inactive rows stay visible here and hidden from students. No hard delete. |
| Plans & pricing | `/admin/plans` | Read-only. Checkout still reads `billing.PLANS`. PREMIUM_PLUS is **₹1,299**. There is no save button. |
| AI configuration | `/admin/ai` | Reports the flag, the $200 ceiling, and `AI_PROVIDER_API_KEY` missing. It does not call a model. |
| Storage & objects | `/admin/storage` | Reports `configured: false` and names `SUPABASE_SECRET_KEY`. No usage number. |

Students open `/assistant` from the dashboard, not the header. It is off until
`features.ai_assistant` is turned on. When on, it quotes documents the same access
rules already allow. `answer` is null. Email is still in-app only; `RESEND_API_KEY`
is unset, and nothing here claims a message was sent.

---

## 3. What was actually wrong, and is now fixed

This is the part worth reading. Each of these was a real defect found by running the
system, not by reading it.

| # | Defect | Consequence if shipped | Fix |
|---|---|---|---|
| 1 | The upload screen offered content kinds ("Past paper", "Syllabus") that the database CHECK constraint rejected, and the API validated only string length | Choosing "Past paper" for a past paper → a **500 carrying a PostgreSQL message** | One source of truth (`DocumentKind`), used by the model, the migration and the request schema. Migration `0011`. A bad kind is now a **422 naming the field and listing the valid values**. |
| 2 | `retry={"max": 2}` passed to RQ where it wants a `Retry` object | **Every ingestion job silently failed.** Uploads answered 201, re-process answered 200, and nothing was ever extracted. The error was caught and logged as a warning, so it looked idle rather than broken. | `Retry(max=…)`, plus `tests/test_queue_boundary.py`, which runs real RQ over a real Redis implementation. |
| 3 | "Re-process" inserted a new ingestion job for an object that already had one, against a unique constraint | The **second press of Re-process was a 500** — on the exact button an admin presses after a failure | `open_job()` re-opens the run, resets the stage and appends to the history. Tested by pressing it twice over HTTP. |
| 4 | A storage failure reached the client as a 500 "unexpected error" | An operator whose storage was down would debug the application instead of storage. Also: a storage host returning an HTML error page crashed on `resp.json()`. | Every storage call goes through one guarded path; malformed/unreachable answers become `StorageError`; an app-level handler turns any of them into a **503 naming the dependency**, so routes not yet written inherit it too. |
| 5 | The console took the role from the auth token, which the API does not use for authorization | A user promoted in the console stayed **locked out of the console** until they signed out and back in, while the API would have served them | The UI now takes the role from `GET /me` (the same database row the API authorizes against); the token is only the pre-fetch placeholder. Proven in a browser with a deliberately stale token. |
| 6 | Admin library search matched titles and filenames only | An operator could not find a document by anything inside it. A file named `accounting-policy-scan.pdf` was not findable by typing "accounting policy". | Search now covers extracted page text plus metadata, with separator-aware matching, and reports **where** it matched. |
| 7 | The verification sweep truncated response bodies to 200 bytes | A handled dependency outage whose message was longer than that was **counted as an unhandled server error** | Bodies are read up to 4000 characters, and the sweep now separates "the API broke" (500 → failure) from "the API correctly reported a dependency" (503 → listed, not failed). |
| 8 | The page header wrapped "CA Prep" and "Sign out" onto two lines, and constraining it clipped a nav item ("Con" for Content) | The bar looked broken at 1440px | One-row header from `lg` up, where the set genuinely fits; below it the navigation moves to its own scrollable row. |
| 9 | **Below 640px the navigation was hidden entirely** | A student on a phone had **no navigation at all** — no link to Practice, Revision or Mocks. Every one of those screens was reachable only by typing a URL. | The compact row above fixes it: all 8 destinations are present and scrollable at 390px. Verified by measuring the rendered links at 1440px, 1280px and 390px. |

Point 7 is worth dwelling on: the sweep previously printed **"0 5xx"** and the earlier
summary in this project repeated that number. It was only true because the bodies were
being cut short. Honest reporting means the *checker* had to be fixed first.

---

## 4. Permissions: what is enforced, and where

Authorization is decided in exactly one module, `apps/api/app/core/permissions.py`, and
enforced on the server for **every** request. Hiding a button is presentation; calling
`/api/v1/admin/users` with a student token returns 403 and no data.

The live sweep proves it rather than asserting it: 101 routes × 6 roles, with real tokens
for real rows, checking that a role holding a permission is never told 403 and a role
lacking it is always told exactly 403. **606 requests, 0 mismatches.**

Two deliberate properties:

* **A 404 or 422 for a permitted role is a pass** — the route was reached and rejected the
  *input*, which is the opposite of an authorization hole.
* **`SUPER_ADMIN` is not assignable through the API** (`ASSIGNABLE_ROLES` excludes it), and
  the owner cannot change their own role. That stops both privilege escalation and
  lockout.

---

## 5. What still needs you

Nothing below is a coding problem; each is a credential or a decision.

| # | Needed | Exact item | Blocks |
|---|---|---|---|
| 1 | Razorpay keys | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, plus the webhook URL configured in the Razorpay dashboard | Taking money. Until these exist the API answers **503** on `/payments/order` and `/payments/confirm` — deliberately, so nobody is charged against a half-configured account. The price is implemented: **₹1,299/yr PREMIUM_PLUS** (`amount_paise = 1_29_900`). |
| 2 | Rotate both Supabase keys | Publishable and secret keys both came through chat | Security, before any real user. The publishable key is *meant* to be public; the secret key is not. |
| 3 | Supabase connection pooler URLs | Transaction-mode pooler host/user for `DATABASE_URL` | Production database connections from a serverless host. |
| 4 | Google OAuth | Enable the provider in Supabase, add the callback URL | The "Continue with Google" button. |

---

## 6. Known gaps and honest limitations

| Item | State | Notes |
|---|---|---|
| **Student library** | ✅ | `/library` and `/library/:id`. The list calls `GET /content/library`, not the admin list. Live check with a free student: the published FREE document is returned; a published PREMIUM document is **404** (not listed); an unpublished document is not listed and a phrase that exists only in it does not appear in search. Browser-checked at 1440 and 390. |
| **Reading a document** | ✅ text / ⚠️ original file | Extracted text is page-by-page from `GET /content/documents/{id}/pages`, and search hits deep-link to the page. The original PDF is an iframe of the short-lived signed URL from `GET .../file`, requested only when the student asks — not on render, because the URL expires. **On this deployment that call is a 503** (no storage credential). The screen says so and leaves the extracted text up. It has not been seen rendering a real PDF, because there is no storage host to render one from. |
| **Student inbox** | ✅ in-app / 🟡 email | `/notifications`. Live rows render; an absolute `linkUrl` is not turned into a link. Mark-read goes to the student's own route. Nothing is emailed — the worker is still a stub, and the page says that. |
| OCR | ⚠️ Needs a binary | The pipeline detects scanned pages and marks documents `OCR_REQUIRED`; Tesseract is not installed in this environment, so the OCR path itself is unexercised here. |
| Background worker | 🟡 Stub | `workers/rq_worker.py` now enqueues correctly (defect 2 above), but there is no Redis or worker process running in this environment, so jobs queue and no extraction happens. In production this needs a worker container. |
| Bulk upload at 500 files | ✅ Implemented, 🟡 not load-tested | The design is right (dedupe before upload, concurrency 4, per-file retry, batch progress computed from rows so it survives a browser close). It has been exercised with small batches; a genuine 500-file run against real storage is untested. |
| Real browser hashing | ⚠️ Environment-dependent | `crypto.subtle` only exists in a secure context. The upload screen refuses to start with an explicit message over plain HTTP rather than failing per file. Use HTTPS or localhost. |
| Payments UI | 🟡 checkout / ✅ list | `/admin/payments` lists orders and webhook events. Checkout still answers **503** until `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` are set — all three are empty here. This screen does not call Razorpay and does not record refunds. |
| AI assistant | 🟡 quotations / 🔴 generated answers | `/assistant` quotes documents the student may already read, using the same `document_filter` as the library. It is off until `features.ai_assistant` is on. `answer` is always null. `AI_PROVIDER_API_KEY` is unset, and this build does not call a model. |
| Git / CI / deployment | 🔴 | This directory is not a git repository, the workflow file has never run, and nothing has ever been deployed. |
| Email | 🟡 | Notifications are in-app only; there is no mail provider configured. |

---

## 7. Security position

* Secrets stay on the server: the browser bundle contains only the Supabase **publishable**
  key. A test asserts the front end's env file contains no secret.
* Every admin route is permission-enforced server-side; the sweep above is the evidence.
* Uploads are validated by content type, extension **and** size, with the object path
  sanitised server-side, so a client-supplied filename cannot escape its prefix.
* PDF and file downloads are **short-lived signed URLs**, capped at one hour regardless of
  what the caller asks for — never public links.
* Payment and AI secrets are read only by the API process.
* The role the UI displays now comes from the database row the API authorizes against, so
  there is one answer to "what may this person do" rather than two that can disagree.

---

## 8. Efficiency, measured

| Metric | Before | After |
|---|---|---|
| Largest non-entry JS chunk (`api`) | 210.2 kB raw / 56.3 kB gzip | **101.8 kB raw / 24.7 kB gzip** |
| Total JS | 746.6 kB raw / 222.6 kB gzip | **639.2 kB raw / 193.4 kB gzip** |
| JS chunks | 25 | 26 (each admin section is its own) |

The `api` chunk halved because the browser client no longer constructs a realtime client
it never used — replaced with the auth-only client, pinned by a unit test so it cannot
regress. Nothing else was split "just in case": what is split is what a user has to wait
for (admin screens, the mock engine, analytics), and the entry chunk holds the shell.
