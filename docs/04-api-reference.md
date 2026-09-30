# API Reference

Base path `/api/v1`. The published contract is `apps/docs/api/openapi.json`,
regenerated from the running app and asserted byte-for-byte by
`tests/test_api_contract.py`.

## Envelopes

Success:

```json
{ "data": { ... }, "meta": { "requestId": "b7df7db140bd48d3" } }
```

Paginated:

```json
{ "data": [ ... ], "meta": { "requestId": "...", "total": 120, "page": 1,
                             "limit": 50, "hasMore": true } }
```

Errors carry a problem document with a stable `type` and a human-readable
`detail`. Every response carries `X-Request-Id`; quote it in bug reports.

`data` is a list on every list endpoint, including cursor-paginated ones. A
client never has to detect which kind of response it received.

## Cursor (keyset) pagination

`GET /api/v1/admin/audit` supports it. Pass `limit` (default 50) and, from the
second page onward, `cursor`:

```
GET /api/v1/admin/audit?limit=2
GET /api/v1/admin/audit?limit=2&cursor=<meta.nextCursor>
```

| Field | Meaning |
|---|---|
| `limit` | page size, default 50, **capped at 100** on the keyset path |
| `cursor` | opaque position from `meta.nextCursor`; malformed input is a **400**, never a 500 |
| `meta.nextCursor` | present only when `hasMore` is true |
| `meta.hasMore` | whether another page exists |
| `meta.total` | **absent on the keyset path** — counting is the cost being avoided |

A cursor is an encoded sort position, not an offset. It is stable under
concurrent inserts: rows added while a client is paging do not shift the window,
so no row is repeated or skipped. Offset pagination has the opposite property on
an append-only log, which is why an audit log should not be read with it.

The cursor is opaque, not secret. It is tamper-*evident*, and a forged value can
only select a position — the ordinary permission checks run independently and
still apply.

## Namespaces

| Prefix | Gating | Contents |
|---|---|---|
| `/api/v1/admin/*` | `require_permission(...)` per route | users, roles, settings, broadcasts, badges, content admin, payment views, audit |
| `/api/v1/campus/*` | mixed; student-facing | study groups, forum, formula/glossary reference, support tickets, experiments, marketplace, calendar, pomodoro, exam mode, daily challenge, referrals, review queue |
| `/api/v1/studio/*` | owner/editor | question authoring, flags, mock papers, curriculum tree, plan pricing, AI configuration, storage status, AI triage, law notices |
| `/api/v1/content/*` | viewer resolved from role + entitlement tier | read library, document pages and files, search |
| `/api/v1/payments/*` | billing user or `require_permission` | plans, subscription, order creation, confirmation, Razorpay webhook, gateway config |
| `/api/v1/webhooks/*` | HMAC signature, no bearer token | provider callbacks |

`/api/v1/admin/*` is gated by `require_permission` on **every** route. A new
admin route without the dependency is a data-exposure bug, so this is asserted
by the RBAC matrix rather than left to review.

## Webhooks

`POST /api/v1/webhooks/razorpay` verifies the HMAC of the **raw request body**.
Parsing and re-serialising the JSON changes key order and whitespace, and the
signature no longer matches — so verification takes bytes, not objects.

Comparison uses `hmac.compare_digest`. A `==` comparison short-circuits on the
first differing byte and leaks the correct prefix through timing.

A malformed or wrongly-signed request is **400/401** and writes nothing. If
Redis is unavailable this path is **fail-closed** and answers **503**: an
uncounted webhook can be replayed, and a replayed capture activates a
subscription nobody paid for.
