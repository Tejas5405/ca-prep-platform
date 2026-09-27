# F-03 — 500 responses carried no request ID, and the logs recorded `-`

**Status: FIXED** · **Severity: P3** · **Milestone: P1 defect remediation** · **Reference commit: `a3113af`**

---

## 1. Original defect

A request that ended in an HTTP 500 produced a response and a log trail that could
not be joined together:

```
$ curl -D - http://127.0.0.1:8000/api/v1/payments/plans
HTTP/1.1 500 Internal Server Error
content-type: application/problem+json
                                  ← no X-Request-Id header

{"type":"https://api.caprep.in/errors/internal","title":"Internal Server Error",
 "status":500,
 "detail":"An unexpected error occurred. The request id is in the response headers."}
```

while the logs recorded:

```json
{"level":"ERROR","logger":"app.error","request_id":"-","msg":"Unhandled error: ..."}
```

Three separate contradictions, and the middle one is the sharpest:

1. The body told the user *"the request id is in the response headers"*. There
   were no response headers carrying one. The API was lying to the person most
   likely to need it — someone reporting a broken request.
2. Every log line recorded `request_id: "-"`, the contextvar's default, on
   **every** status code including 200s.
3. A 500 produced **no access-log line at all**.

The consequence is that a support conversation could not be completed. A user
supplies a request id, or an operator finds a stack trace in the log, and neither
side can find the other. On a 500 — the case where correlation matters most — the
one mechanism designed to provide it was the one case where it did not work.

---

## 2. Root cause

Two independent defects, one visible symptom.

### 2a. The access log was written after the contextvar was reset

The middleware ended with the log call *below* the `finally` that resets the
contextvar:

```python
try:
    response = await call_next(request)
finally:
    request_id_ctx.reset(token)      # <- id is unbound here

duration_ms = round((time.perf_counter() - started) * 1000, 1)
response.headers["X-Request-Id"] = rid
logging.getLogger("app.access").info("%s %s -> %s in %sms", ...)   # <- records "-"
```

The header was correct. The log line was not, because the structured formatter
reads `request_id` from the contextvar, which had already been reset to its
`"-"` default. This affected **all** responses, not just 500s — it was simply
invisible on success, where nobody was looking.

### 2b. The catch-all handler runs outside the user middleware

This is the structural one, and it is the answer to the question posed in the
objective — *does the exception occur before the request-ID middleware can attach
the header?* Effectively yes.

Starlette composes its error handling as:

```
ServerErrorMiddleware          <- the catch-all @app.exception_handler(Exception)
  └─ user middleware           <- @app.middleware("http")  (our request-id middleware)
       └─ ExceptionMiddleware  <- registered handlers (403, 404, 422, …)
            └─ router / endpoint
```

`ServerErrorMiddleware` is the **outermost** layer. When an unhandled exception
propagates, it passes straight through the user middleware on its way out — so the
middleware's `response.headers["X-Request-Id"] = rid` line never executes. The
`@app.exception_handler(Exception)` handler then builds the 500 response *outside*
the middleware, with no access to anything the middleware had set.

That is also why no access line existed for a 500: the code that writes the access

---

## 3. Fix

Two changes, both in `apps/api/app/main.py`, and both minimal.

### 3a. Emit the log line while the id is still bound, and log the 500

The middleware body was restructured into `try/except/else/finally` so that the
contextvar is live for every log call, and so the exception path still produces an
access line before re-raising:

```python
logger = logging.getLogger("app.access")
started = time.perf_counter()
try:
    response = await call_next(request)
except Exception:
    logger.error("%s %s -> 500 in %sms", request.method, request.url.path, …)
    raise                      # re-raised, never swallowed
else:
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Request-Id"] = rid
    logger.info("%s %s -> %s in %sms", request.method, request.url.path,
                response.status_code, duration_ms)
    return response
finally:
    request_id_ctx.reset(token)
```

`request.state.request_id = rid` was already set before `call_next`, so the id is
available to the handler via `request.state` even though the contextvar is not.

### 3b. The catch-all handler stamps the header itself

Because the handler runs outside the middleware, it must attach the id from
`request.state` rather than rely on the middleware having done so — and it
re-binds the contextvar just long enough to log, so the structured formatter
records the same value that goes on the wire:

```python
rid = getattr(request.state, "request_id", None)
token = request_id_ctx.set(rid) if rid else None
try:
    error_logger.exception("Unhandled error: %s", exc, extra={"request_id": rid or "-"})
finally:
    if token is not None:
        request_id_ctx.reset(token)

response = problem(status=500, title="Internal Server Error",
                   detail="An unexpected error occurred. The request id is in the response headers.",
                   type_slug="internal")
if rid:
    response.headers["X-Request-Id"] = rid
return response
```

The exception is still **logged with a full traceback** and the response body is
still the same documented problem shape with **no stack leak** — the fix changes
only whether the id is present, not what is disclosed.

### Why the id is not a sensitive value

The request id is either a client-supplied `X-Request-Id` header or a generated
`uuid4().hex[:16]`. It carries no token, no user id, no query string, no path
segment and no credential, and it is already exposed on every successful response
via `CORS expose_headers=["X-Request-Id"]`. Echoing it on a 500 discloses nothing
new. It is a correlation handle, not a secret.

One note for completeness: because a client may choose this value, the id is
**not** trusted as an identifier for anything. It is used only to join a log line
to a response, and the tests below assert the value round-trips unchanged rather
than asserting it is well-formed.

---

---

## 5. Verification

### 5a. Live, against a running server

A genuine 500 was provoked by pointing an instance at an unreachable database
(`127.0.0.1:5999`), which makes a real repository endpoint raise:

```
$ curl -D - -H 'X-Request-Id: live500probe' http://127.0.0.1:8031/api/v1/payments/plans
HTTP/1.1 500 Internal Server Error
x-request-id: live500probe                                     ← was absent before

{"type":"https://api.caprep.in/errors/internal","title":"Internal Server Error",
 "status":500,"detail":"An unexpected error occurred. The request id is in the response headers."}
```

The logs agree, and agree with the header:

```json
{"level":"ERROR","logger":"app.access","request_id":"live500probe",
 "msg":"GET /api/v1/payments/plans -> 500 in 193.3ms"}
{"level":"ERROR","logger":"app.error","request_id":"live500probe",
 "msg":"Unhandled error: (psycopg.OperationalError) connection failed: …"}
```

The same instance confirms the other two classes:

| Request | Status | Header |
|---|---|---|
| `GET /health` | 200 | `x-request-id: live200` |
| `GET /api/v1/mocks` (no token) | 401 | `x-request-id: live401` |
| `GET /api/v1/payments/plans` (broken DB) | 500 | `x-request-id: live500probe` |

One id, three places, on every status code.

### 5b. Full regression suite

`1020 passed, 0 failed, 1 skipped` with the database; `0 failures` without it.
The pre-existing OpenAPI-drift and health-contract tests were unaffected.

---

## 6. Files changed

| File | Change |
|---|---|
| `apps/api/app/main.py` | log the access line while the contextvar is bound; log a 500 access line on the exception path; stamp `X-Request-Id` on the 500 response in the catch-all handler |
| `apps/api/tests/test_request_id_contract.py` | **new** — 6 regression tests across 200 / 4xx / 500 |
| `docs/F03_REQUEST_ID_REMEDIATION.md` | this document |

No API contract change: the error body, status codes and envelope are byte-for-byte
what they were. The fix only adds a response header that the body already promised,
and it is already declared in `CORS expose_headers`, so browser clients can read it
without any CORS change.


## 4. Regression tests

`apps/api/tests/test_request_id_contract.py` — 6 new tests, all offline, covering
the three required status classes and asserting the **same value** appears in the
header, the access log and the error log.

| Test | Status | Asserts |
|---|---|---|
| `test_success_response_carries_the_client_request_id` | **200** | header equals the client-supplied id |
| `test_generated_request_id_is_present_and_not_a_placeholder` | **200** | a 16-hex id is generated when the client sends none, and is never `-` |
| `test_4xx_response_carries_the_client_request_id` | **401** | header equals the client-supplied id |
| `test_validation_path_carries_the_client_request_id` | **4xx** | a rejected request body still carries the id |
| `test_500_response_and_error_log_share_the_request_id` | **500** | body still says the id is in the headers **and** the header is now really there; the `app.error` record carries the same id |
| `test_500_access_line_is_logged_with_the_request_id` | **500** | a 500 still produces an `app.access` line containing `500`, with the same id |

The 500 is produced realistically: a fixture overrides the `get_db` dependency
with one that raises `RuntimeError`, so the exception originates in a
request-scoped dependency exactly as an unforeseen bug would. The client is built
with `raise_server_exceptions=False` so the real HTTP response is inspected rather
than the exception re-raised into the test.

`test_500_response_and_error_log_share_the_request_id` asserts the *body
correspondence* explicitly — the message promising the id is in the headers is
checked against the header actually being present. That is what makes the
original contradiction ("the id is in the headers" + no headers) impossible to
reintroduce silently.

line was skipped entirely, not merely mis-attributed.

This also explains why the *registered* handlers (401/403/404/422) were never
affected: they run in `ExceptionMiddleware`, which is **inside** the user
middleware, so the response returns normally and the header is attached as usual.
Only the 500 path was broken.
