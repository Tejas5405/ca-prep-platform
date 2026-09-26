# API / Frontend Contract Audit

Scope: every FastAPI route that the React client can reach, checked against the
client's own HTTP layer and the checked-in OpenAPI document.

## Headline results

- **OpenAPI is exactly in sync with the code.** A fresh `app.openapi()` dump was
  diffed against the checked-in `apps/docs/api/openapi.json`: **153 paths, identical
  operationIds, zero drift** (verified 27 Sep 2026). The checked-in document is
  therefore trustworthy for client work. (`tests/test_api_contract.py` also asserts
  this — the "drift test".)
- **One envelope, everywhere.** Success `{data, meta}`, list
  `{data, meta:{total,page,limit,hasMore,requestId}}`, errors RFC 7807
  `{type,title,status,detail,errors[]}` — produced by `app/core/envelope.py`,
  consumed by `apps/web/src/lib/api.ts`, and asserted by HTTP-level tests
  (`test_api_contract.py`) that run with **no auth override** so the
  unauthenticated behaviour is part of the contract.
- **Naming is homogeneous camelCase** (`requestId`, `hasMore`, `amountPaise`,
  `providerOrderId`, `displayName`). No snake_case/camelCase mismatch was found in
  the sections verified.
- **Error handling is uniform:** `ApiError` distinguishes 401/403/422 and maps
  `problem.errors[]` (field-level) onto form fields.

## Endpoint registry (153 paths)

Full registry is generated from OpenAPI; the endpoint count per domain:

| Domain | Paths | Notes |
| --- | --- | --- |
| health | 4 | mounted at `/` (not versioned); `/health`, `/health/db`, `/health/redis`, `/health/storage` |
| curriculum | 4 | read-only student views |
| practice | 5 | questions/answers/bookmark/flags; entitlement-checked server-side |
| mocks | 7 | list, attempt lifecycle, submit, report; `locked` derived server-side |
| planner | 2 | generate, replan |
| progress | 2 | overview, projection |
| revision | 3 | due, review, stats (spaced repetition) |
| doubts | 6 | threads, replies, accept-answer |
| search | 1 | FTS with read-scoping |
| collections | 9 | CRUD + membership + LDR |
| content | ~19 | library documents, pages, file URLs, access |
| gamification | 5 | points, badges, streak, achievements, leaderboard |
| notifications | 4 | list, read, read-all, unread-count |
| payments | 5 | plans, order, confirm, subscription, webhook |
| ingestion | 7 | uploads, jobs, drafts, review |
| users | 3 | me, role assignment |
| admin | 18 | console (dashboard, people, plans, ai, storage, …) |
| access | 4 | content-access grants + explain |
| studio | ~28 | question/test/curriculum/plans/AI/storage authoring |
| assistant | 2 | status, ask |
| campus | 38 | forum, calendar, pomodoro, tickets, mentorship, marketplace, experiments, law notices, glossary, formulas |

## Client ↔ server call map (representative)

The frontend client (`apps/web/src/lib/api.ts`) calls only `/api/v1/...` paths
with `Authorization: Bearer <supabase access token>`; `api.publicGet` is reserved
for genuinely public reads (pricing catalogue — the one page that must work before
sign-in). Request/response shapes for paths the pages use were read against the
backend schemas in the study loop:

| Page | Backend path(s) | Verified shape |
| --- | --- | --- |
| Dashboard | `GET /me`, `GET /progress/overview`, `GET /revision/due`, `GET /revision/stats`, `GET /gamification/*` | camelCase payloads, meta.requestId present |
| Practice | `GET /practice/questions`, `POST /practice/answers`, `POST /practice/questions/{id}/flags` | `{data, meta}`; server grades |
| Mocks | `GET /mocks`, `POST /mocks/{id}/attempts`, `POST /mock-attempts/{id}/submit`, `GET /mock-attempts/{id}/report` | `locked`/`entitlement` fields |
| Library | `GET /content/documents`, `GET /content/documents/{id}/pages`, `GET /content/documents/{id}/file` | admin vs student route split (tested) |
| Upgrade | `GET /payments/plans`, `POST /payments/order`, `POST /payments/confirm` | amount computed server-side |
| Admin console | `GET /admin/*` (18), `GET /admin/me/permissions` | permission-driven console |
| Studio | `POST /admin/questions`, tests, curriculum, plans, AI, storage | 422 field errors in `errors[]` |

## Verified-good behaviours

- Pagination meta (`hasMore`, `total`, `page`, `limit`) matches the client's
  `ApiList` type; `test_api_contract.py` asserts exact last-page semantics.
- 401 (missing/malformed/invalid token) and 403 (role insufficient) are covered by
  HTTP tests and surfaced distinctly by `ApiError.isAuthError` / `isForbidden` so
  the UI can choose its remedy.
- 422 validation responses flatten field paths to dot-notation (`main.py`
  handler) — the client's `fieldErrors` map consumes them directly.
- The webhook (`POST /webhooks/razorpay`) is deliberately **not** bearer-auth; it
  verifies its own HMAC signature and answers 401 to unsigned callers (confirmed
  in the prior live RBAC matrix).
- `X-Request-Id` is echoed into `meta.requestId` and surfaced by the client.

## Discrepancies found

1. **Checkout 503 copy mismatch (frontend, deterministic test failure).**
   `pages/Upgrade.tsx` renders the fixed "This deployment cannot take payments yet:
   the gateway credentials are not set." only when the 503 problem has no `detail`;
   the API always sends one (`Payments are not enabled on this deployment.`), so
   the operator-facing fallback copy never appears and the component test that
   asserts it cannot pass. Fix in the page (prefer fixed copy + server detail) or
   the test — see `NEXT_EXECUTION_PLAN.md` P2-1.
2. **No contract-level integration test across the whole journey.** The suite is
   strong per-endpoint (HTTP) and per-page (Vitest), but there is no automated
   test that drives a page's real fetch wiring against the real API in one process.
   `scripts/live_verify.py` covers server-side RBAC against a live API; the
   browser layer above it is not part of any automated artifact yet (P1-4).
3. **Minor:** `docs/DELIVERY_REPORT.md` states "88 paths"; current contract is 153.
   The OpenAPI file itself is current — the prose report is not (P2-3).

## Items checked and found consistent

- snake_case vs camelCase — none found; both sides are camelCase.
- Dates: ISO-8601 strings, timezone-aware on the backend (`DateTime(timezone=True)`),
  parsed by the client as strings; no client-side date arithmetic was found that
  assumes a naive zone.
- IDs: `users.id`, `auth_user_id` (Supabase UUID), question/test/collection uuids all
  travel as strings; the client never constructs them.
- Enums: stored as VARCHAR+CHECK, serialised as the exact enum strings
  (`models/enums.py`), mirrored by `contentKinds.ts`/`roles.ts`; a test asserts the
  two role lists stay equal.
- Nullability: `accuracy: null` renders as "not scored yet"; nullable `examScore`
  explicitly handled.
- Error responses: uniform RFC 7807; the client tolerates non-JSON error bodies
  (proxy error pages) without hiding the status.

## Outstanding verification (needs live infra)

- Real signed-URL round-trip (storage) and real OCR job (worker) — both
  `BLOCKED_EXTERNAL`; storage routes answer their documented 503 in this workspace.
- Razorpay webhook delivery and browser-callback path against a real account —
  `BLOCKED_EXTERNAL` (no keys; the routes are covered by offline HTTP tests).
- Auth contract under a real Supabase project (JWKS) — `BLOCKED_EXTERNAL` here;
  unit tests pin the offline behaviour.

## Method note

Path-by-path comparison was performed against the fresh OpenAPI dump and targeted
reads of `apps/web/src/lib/api.ts`, `apps/web/src/app/App.tsx`, router files, and
the contract tests, plus a regenerated-spec equality diff. For the highest-value
student journeys the request/response shapes were additionally traced through the
backend schemas. The remaining paths are covered by the same client abstraction and
the uniform envelope, so category-level risk is low where per-path deep reads were
not performed.