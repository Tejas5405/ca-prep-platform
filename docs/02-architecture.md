# Architecture

## Shape

```
Browser SPA (React/Vite)
        │  HTTPS, Bearer JWT
        ▼
FastAPI (stateless, app/api/v1)
        │
        ├──► PostgreSQL 16   system of record
        └──► Redis          rate-limit counters, RQ queues, replay guard
```

The API holds no session state. Any instance can serve any request, which is
what lets it scale horizontally and what makes the Render deploy a rolling
replacement rather than a migration window.

## The auth split, and why it is split

Two systems answer two different questions, and conflating them is the single
most common source of "why is this user an admin":

| Question | System of record |
|---|---|
| Who is this, and is the token genuine? | **Supabase Auth** — issues and signs the JWT |
| What may this account do? | **PostgreSQL** — `users.role` + `ROLE_PERMISSIONS` |

The token is *evidence*, not authority. Every authorization decision is made
server-side against a database row. A client cannot widen its own permissions by
editing a claim, because nothing reads the claim for that purpose.

`SUPABASE_URL` must match the project the token was minted by — the API verifies
`iss` against it. A drifted value means sign-in succeeds and every API call
returns 401, which looks identical to an attack.

## Middleware order

Outermost first:

```
RequestContextMiddleware    assigns X-Request-Id
        ▼
CORSMiddleware               adds Access-Control-Expose-Headers
        ▼
RateLimitMiddleware         returns the 429
        ▼
routers
```

`add_middleware` **prepends**, so the last call is the outermost layer. The
calls therefore appear in reverse order in `app/main.py`, and that is
deliberate. Two failures are being avoided:

- Rate limiting outside CORS would deliver every 429 with no CORS headers, so
  the browser reports an opaque network error and the client cannot read
  `X-RateLimit-*` at all.
- Rate limiting outside request-context would mean a 429 carries no request id,
  so the one response you most need to trace is the one you cannot trace.

`tests/test_rate_limit.py::test_middleware_sits_inside_cors` asserts this stack.
A comment explaining an inversion is exactly the sort of thing that stops being
true silently.

## Module layout

Routes live in subpackages under `app/api/v1/`. Each was a single oversized
file at some point; each is now split by concern with a `_shared.py` for
helpers used by more than one sub-module.

| Package | Concern |
|---|---|
| `campus/` | groups, forum, reference data, support, experiments, calendar, challenge, review |
| `studio/` | question bank, flags, mock papers, curriculum, plans, operations, AI triage, law notices |
| `admin/` | dashboard, users, permissions, settings, notifications, badges, payment views |
| `content/` | read library, uploads, document admin, access rules |
| `ingestion/` | uploads, jobs, draft review, publishing |
| `payments/` | plans, orders, confirmation, webhook, gateway config |
| `mocks/` | mock papers and the attempt lifecycle |

Every file under `app/api/v1/` is **under 600 lines**; the largest is
`studio/questions.py` at 555.

Each package's `__init__.py` composes its router with
`router.routes.extend(sub.router.routes)` rather than `include_router`. Two
reasons, both forced by FastAPI 0.141: `include_router` stores an
`_IncludedRouter` wrapper, so `router.routes` stops being a flat list of
`APIRoute`; and it concatenates the parent router's tags onto every included
route, which would double the tags in the published document.

`__init__.py` also re-exports the package's public surface — including names
that were *imports* in the original single file, not just the ones it defined.
`payments.get_settings` is referenced by
`tests/integration/test_postgres_routes.py`; without the re-export that line
raises `AttributeError` and the whole payments integration group fails. An
import is part of a module's public surface exactly as much as a definition is.

## Background work

An RQ worker (`app/workers/rq_worker.py`) drains the ingestion queue. It is
deployed as a **Docker** service, not Render's native Python runtime, because
the OCR path shells out to `tesseract-ocr` and `poppler-utils`. `pip install
pytesseract pdf2image` succeeds without them; the failure only appears at
runtime, on the first scanned PDF, as a pipeline that quietly produces no
drafts.
