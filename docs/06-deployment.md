# Deployment

Target: Render, declared entirely as a blueprint in `infra/render.yaml`.

## Resources

| Name | Type | Notes |
|---|---|---|
| `caprep-api` | `type: web`, native Python | `preDeployCommand: alembic upgrade head` |
| `caprep-worker` | `type: worker`, **docker** | RQ; needs Tesseract + Poppler |
| `caprep-redis` | `type: keyvalue` | `maxmemoryPolicy: noeviction` |
| `caprep-postgres` | Postgres 16 | `basic-256mb`, singapore |

Two details that are load-bearing rather than cosmetic:

- **The worker must be `runtime: docker`.** Render's native Python runtime has
  no `tesseract-ocr` and no `pdftoppm`. `pip install pytesseract pdf2image`
  succeeds without them, and the failure appears only at runtime, on the first
  scanned PDF, as a pipeline that produces zero drafts with no error on the job.
- **Redis is declared under `services:`, not `databases:`** — only Render
  Postgres belongs under `databases`. `type: keyvalue` is the current name;
  `type: redis` still parses but is a deprecated alias.

`maxmemoryPolicy: noeviction` is a correctness requirement, not tuning. Under
the default `allkeys-lru` the limiter can evict a counter, and a counter that
vanished reads as "this client has made no requests yet" — a free reset of every
limit, whenever memory fills. `ipAllowList: []` restricts Redis to Render's
private network; an open Redis is a rate-limit bypass.

## Configuration that matters

| Variable | Why |
|---|---|
| `RATE_LIMIT_TRUSTED_PROXIES=1` | Behind Render's proxy, `X-Forwarded-For` holds the real client. At `0` every anonymous user shares the proxy IP as their bucket — a self-inflicted DoS. Set too **high**, the middleware starts reading a client-controlled header and the limiter can be walked around. |
| `RAZORPAY_WEBHOOK_SECRET` | Without it the webhook refuses every request: payments never activate, silently, with no error anywhere. A revenue outage with no log line. |
| `SENTRY_DSN` | Optional. Unset, `init_sentry()` is a no-op and the app still boots — deliberately, so a staging box without Sentry is not a boot failure. |
| `SUPABASE_URL` | Must equal the frontend's and the project's inside `DATABASE_URL`. The API verifies token `iss` against it; a drift means sign-in works and every call 401s. |
| `ENVIRONMENT` | `staging` activates guards: a staging box holding an `rzp_live_` key **refuses to boot**. |

No secret is committed. Secrets are `sync: false` — Render prompts and stores
them outside git.

## REDIS_URL must be wired by hand

Render's Key Value instance exposes `host` and `port` as **separate**
properties and has no combined `connectionString`, and this application takes a
single `REDIS_URL`. It therefore cannot be composed in the blueprint.

After Render provisions `caprep-redis`, open its dashboard, copy the internal
connection URI, and set `REDIS_URL` manually on **both** `caprep-api` and
`caprep-worker`:

```
redis://:<password>@<host>:<port>/0
```

Leaving it unset or pointing it at `localhost:6379` is the failure to watch for:
every rate-limited path and every webhook answers 503.

## Post-deploy smoke test

```bash
scripts/smoke_test.sh https://caprep-api.onrender.com
```

Ten checks, exit `0` pass / `1` assertion failed / `2` unreachable. It asserts
the things that break silently: liveness, `X-Request-Id`, that the limiter is
mounted **and** that `/health` is still exempt from it, that a forged webhook
signature is refused, and that `Access-Control-Expose-Headers` advertises the
headers the browser needs to read.

Run it against a deployed target after every deploy, and against a local
uvicorn before shipping a config change.
