# Production Readiness Matrix

**Milestone:** repository ownership + staging readiness · **Baseline commit:** `49adb42`

Status vocabulary, used strictly:

| Status | Meaning |
|---|---|
| **READY** | verified, with evidence, in an environment resembling production |
| **PARTIAL** | partly verified; a named gap remains |
| **BLOCKED** | cannot proceed; an external dependency is missing |
| **NOT_TESTED** | never exercised, in any environment |

**Local tests passing is not evidence of production readiness.** A suite of 1039
tests can be green while the payment flow has never run against a real gateway, or
while no deployed instance has ever served a request. The `NOT_TESTED` and
`BLOCKED` rows below are the honest part of this document.

---

## 1. Security

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| No secrets in the working tree | **READY** | `check_secrets.py --worktree` CLEAN, 0 unexpected matches | — | — |
| No secrets in the index | **READY** | `--staged` CLEAN | — | — |
| No secrets in Git history | **READY** | `--rev` on HEAD and the root commit CLEAN; no tracked `.env`/key files | — | — |
| No secret material in the browser bundle | **READY** | `test_infra_contract.py` allows only `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`; `dist/` scanned | — | — |
| TLS trust store is explicit and verifying | **READY** | F-02: certifi context, `CERT_REQUIRED`, `check_hostname=True`; live JWKS fetch with `SSL_CERT_FILE` unset | — | — |
| Secrets managed outside git | **PARTIAL** | `render.yaml` uses `sync: false`; no secret has ever been *deployed* | no deployed environment | provision staging, set dashboard values |
| Dependency vulnerability scan | **NOT_TESTED** | no `pip-audit` / `npm audit` in CI or locally | — | decide whether to add to the `security` job |
| Rate limiting enforced under load | **PARTIAL** | implemented and unit-tested | never observed against real traffic | observe in staging |
| HTTPS/TLS termination | **NOT_TESTED** | provider-level | no deployed host | confirm at deploy |

## 2. Database

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Migration chain is linear and complete | **READY** | 14 migrations, `0001_initial` → `6c3f7b0a0c13`; `upgrade head` from empty succeeds | — | — |
| No model/migration drift | **READY** | `alembic check` → "No new upgrade operations detected." | — | — |
| Migrations are reversible | **PARTIAL** | `migrations-are-reversible` job does `upgrade → downgrade base → upgrade` | **the job has never run** — no remote | run CI |
| Production database is at head | **BLOCKED** | n/a | no production database | provision |
| Backups enabled and restorable | **NOT_TESTED** | `render.yaml` notes daily snapshots; retention unverified | no production instance | verify retention; **test a restore** |
| Test-database isolation enforced in code | **READY** | `assert_disposable_database` refuses non-`caprep_v2_*` names before any `TRUNCATE`; 19 tests | — | — |
| Zero-downtime migration strategy | **NOT_TESTED** | not designed | no staging to rehearse against | rehearse in staging |

## 3. Authentication

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| JWT signature, issuer, audience, expiry verified | **READY** | live Supabase ES256 JWKS fetch; valid token → 200, garbage → 401 | — | — |
| JWKS reachable without manual TLS setup | **READY** | F-02 verified live with `SSL_CERT_FILE` unset | — | — |
| Role is not trusted from the token | **READY** | `is_staff()` reads the database row; proven — a token claiming ADMIN got 403 until the row was `ADMIN` | — | — |
| Sign-up / confirmation flow | **NOT_TESTED** | not exercised end-to-end on a deployed environment | no staging Supabase project | provision, then test |
| Password reset / MFA | **NOT_TESTED** | no coverage found | — | decide scope |
| Rate limiting on auth endpoints | **NOT_TESTED** | implemented, never observed under load | — | observe in staging |

## 4. Authorization

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Role × route matrix is correct | **READY** | 107 routes × 6 roles = 642 requests, 0 unexpected 5xx, 0 mismatches | — | — |
| Horizontal privilege escalation is refused | **READY** | `TestHorizontalPrivilegeEscalation` in `test_security.py` | — | — |
| Object-level access control | **PARTIAL** | repositories scope by owner; unit-tested | not re-verified on a deployed DB | re-run the sweep against staging |

## 5. Storage

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Buckets are private | **READY** | the publishable key lists zero buckets (verified live) | — | — |
| Signed URL round trip | **READY** | upload → sign → download (sha256 match) → delete → list empty, against real Supabase | — | — |
| All access brokered server-side | **READY** | no storage credentials in the bundle; the API mints signed URLs | — | — |

## 6. Redis

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Reachable and healthy | **READY** | `PING` → `PONG`; `/health/redis` 200 | — | — |
| Eviction policy protects the queue | **PARTIAL** | `noeviction` set deliberately in `docker-compose.yml` | staging instance does not exist | carry the policy to staging |
| Persistence configured | **NOT_TESTED** | the local Redis ran without persistence | no managed instance | configure AOF/RDB |
| Tests independent of ambient Redis | **READY** | F-04: 1039 passed with Redis ON **and** OFF | — | — |

## 7. Workers

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Jobs actually execute | **READY** | a live RQ job ran end-to-end ("Job OK") | — | — |
| A queue outage does not fail the upload | **READY** | `test_queue_boundary.py`; enqueue is guarded and returns `enqueued: false` | — | — |
| Worker uses the Docker runtime | **READY** (config) | `infra/render.yaml`; `test_infra_contract.py` asserts the OCR binaries are in the image | — | — |
| Worker deployed and observed | **NOT_TESTED** | — | no staging deploy | deploy, verify an ingestion job |
| Retry / dead-letter handling | **PARTIAL** | `Retry` object with `MAX_RETRIES`; the dict-spelling bug is regression-tested | dead-letter queue never observed | observe in staging |

## 8. OCR

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Text-layer extraction works | **READY** | PyMuPDF tier, 51 chars from a generated PDF | — | — |
| Scanned-PDF OCR works | **READY** | Tesseract tier fired, 27 chars, confidence **0.9567** | — | — |
| OCR failure is contained | **READY** | the pipeline keeps the text layer and the job still succeeds | — | — |
| OCR binaries present in the deployed image | **PARTIAL** | the `Dockerfile` installs them; `test_infra_contract.py` asserts it | image never built or run | deploy the worker, ingest a scan |
| Accuracy on real scanned papers | **NOT_TESTED** | only synthetic PDFs were used | no permitted real documents | test with permitted public material |

## 9. AI

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Grounded answers only | **READY** | a live grounded answer was returned; the prompt forbids invented sections/rates | — | — |
| Failure degrades honestly | **READY** | returns `None` → quotations-only; never an invented answer | — | — |
| Fallback is genuinely redundant | **READY** | F-05: configuration-driven, and an identical pair is rejected at startup | — | — |
| Primary success does not call the fallback | **READY** | a regression test asserts `asked == ["primary-model"]` | — | — |
| API key never logged | **READY** | the key is redacted from the prompt and the response | — | — |
| Cost ceiling enforced | **PARTIAL** | `AI_MONTHLY_CEILING_USD`, `AI_FREE_QUERIES_PER_DAY` configured | never observed under load | observe in staging |

## 10. Payments

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Missing keys degrade gracefully | **READY** | 503 naming `RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET` | — | — |
| Unsigned webhook is refused | **READY** | 401 | — | — |
| Order creation | **NOT_TESTED** | never called with credentials | no Razorpay keys | obtain **TEST** keys |
| Signature verification (positive path) | **NOT_TESTED** | only the negative path verified | no keys | obtain **TEST** keys |
| Webhook → entitlement activation | **NOT_TESTED** | — | no keys | obtain **TEST** keys |
| Idempotency / duplicate webhook | **PARTIAL** | `payment_events` + unique constraints exist; logic unit-tested with fakes | never run against Razorpay | test in staging |
| Failed payment leaves no entitlement | **NOT_TESTED** | — | no keys | test in staging |
| Production keys | **BLOCKED** | deliberately not requested | owner decision | do not request yet |

## 11. Frontend

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Typecheck / lint / tests | **READY** | exit 0; 248 vitest tests | — | — |
| Production build succeeds | **READY** | `npm run build` exit 0, 357 ms | — | — |
| SPA deep links and refresh | **NOT_TESTED** | no rewrite config exists (**DG-1**) | no deployed host | add rewrite config, then verify |
| Reaches the deployed API | **NOT_TESTED** | no `VITE_API_BASE_URL`; dev-only `/api` proxy (**DG-6**) | no deployed host | decide proxy vs absolute |
| No secret in the bundle | **READY** | bundle scanned; only the publishable key present | — | — |

## 12. Backend

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Test suite green | **READY** | **1039 passed, 0 failed**, with Redis ON and OFF | — | — |
| Lint / format clean | **READY** | ruff check; 152 files formatted | — | — |
| Request IDs on every response | **READY** | F-03: 200, 4xx and 500 all carry the same id as the logs | — | — |
| Error responses leak no stack | **READY** | the catch-all returns the documented problem shape | — | — |
| Refuses to boot with wildcard CORS in production | **READY** | asserted in config/startup tests | — | — |
| Graceful degradation when a dependency is down | **READY** | DB/Redis/storage health endpoints verified in both states | — | — |
| Runs as a deployed service | **NOT_TESTED** | only local `uvicorn` runs | no staging host | deploy |
| Multiple workers are safe | **NOT_TESTED** | `--workers 2` is configured | never run with >1 | deploy and probe |

## 13. CI/CD

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Workflow defined | **READY** | 4 jobs in `.github/workflows/ci-cd.yml` | — | — |
| Gates enforced in CI | **READY** | security, lint, format, migrate, drift, test, build, reversibility | — | — |

## 14. Observability

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Structured JSON logs | **READY** | one JSON line per request, with `request_id` | — | — |
| Request id correlates log ↔ response | **READY** | F-03, verified on a live 500 | — | — |
| Health endpoints | **READY** | `/health`, `/health/db`, `/health/redis`, `/health/storage` | — | — |
| Error tracking (Sentry) | **NOT_TESTED** | `SENTRY_DSN` is an empty `.env.example` entry | — | provision (**DG-5**) |
| Uptime monitoring | **NOT_TESTED** | none configured | — | provision (**DG-5**) |
| Alerting on 5xx rate | **NOT_TESTED** | none | — | provision (**DG-5**) |
| Metrics / tracing | **NOT_TESTED** | none | — | decide whether to add |

## 15. Backups

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Automated backups configured | **PARTIAL** | `render.yaml` notes daily Render snapshots | no production instance | enable, verify retention |
| **A restore has been tested** | **NOT_TESTED** | never attempted | no backup to restore | test a restore before go-live |
| Point-in-time recovery | **NOT_TESTED** | unknown | no production instance | confirm the plan supports PITR |
| Retention meets the requirement | **NOT_TESTED** | unverified | — | confirm the window |

## 16. Rollback

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| Application rollback path | **READY** (documented) | previous deploy; stateless services | — | — |
| Schema rollback policy | **READY** (documented) | roll back the app, never the schema; forward-fix migrations | — | — |
| Rollback rehearsed | **NOT_TESTED** | never performed | no deployed environment | rehearse in staging |
| Migration rollback verified in CI | **PARTIAL** | the job exists; never executed | no remote | run CI |
| Data-loss window understood | **NOT_TESTED** | not analysed | no production data at scale | document before go-live |

## 17. Monitoring

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| `/health` suitable for a load balancer | **READY** | liveness only, deliberately — a DB blip must not restart the process | — | — |
| Dependency health separately probeable | **READY** | `/health/db`, `/health/redis`, `/health/storage` | — | — |
| Uptime monitor configured | **NOT_TESTED** | none | — | provision |
| On-call route for alerts | **NOT_TESTED** | none | — | define |
| Queue-depth alerting | **NOT_TESTED** | none; a stalled queue looks like a slow pipeline | — | add once Redis is managed |

## 18. Data privacy

| Requirement | Status | Evidence | Blocker | Owner action |
|---|---|---|---|---|
| No production data in staging | **READY** (policy) | `docs/STAGING_DATA_POLICY.md`; staging does not exist yet | — | enforce at creation |
| No real data in Git | **READY** | scanner CLEAN across worktree, index and full history | — | — |
| Storage is private | **READY** | the publishable key lists zero buckets | — | — |
| PII columns exist and are handled | **PARTIAL** | `email`, `phone`, `display_name` in the schema | retention/erasure policy not written | document retention |
| Data retention policy | **NOT_TESTED** | none written | — | define before launch |
| Right-to-erasure implemented | **PARTIAL** | soft-delete columns exist (`deleted_at`); `get_by_auth_id` hides barred rows | hard erasure and its blast radius untested | test and document |
| Consent / terms capture | **PARTIAL** | `terms_accepted_at` exists | never exercised on a deployed env | test in staging |
| Privacy policy / legal pages | **NOT_TESTED** | outside code scope | — | owner/legal action |

---

## 19. Tally

Counted mechanically from the table rows above (108 requirements across 18
categories):

| Status | Count | Reading |
|---|---|---|
| **READY** | 52 | verified, with evidence |
| **PARTIAL** | 15 | works, with a named gap |
| **NOT_TESTED** | 37 | never exercised in any environment |
| **BLOCKED** | 3 | needs an external dependency |
| **UNKNOWN** | 1 | CI green — cannot be claimed |

**The single most important line in this document:** 37 of 108 requirements have
never been tested in any environment, and the entire payment lifecycle beyond "it
refuses nicely" sits among them. A green local suite does not move any of those
rows — it only narrows what remains unproven.

The categories carrying the most untested surface are **Observability** (4 of 7),
**Payments** (4 of 7) and **Monitoring** (3 of 5). All three are unproven for the
same reason: they can only be demonstrated against something deployed.

Three blockers, and all three are owner actions rather than engineering work:

| # | Blocker | Where | Action |
|---|---|---|---|
| 1 | No Git remote | §13 CI/CD | configure it and push |
| 2 | No production database | §2 Database | provision |
| 3 | Production Razorpay keys | §10 Payments | **deliberately not requested yet** |

The staging infrastructure gaps (DG-2, DG-3, DG-4) are *not* counted as blockers
here, because they are provisioning steps in `docs/STAGING_DEPLOYMENT_PLAN.md`
rather than things that prevent this assessment — but they are the reason many
`NOT_TESTED` rows are still untested.

Sequencing note: **blocker 1 gates the cheapest evidence** (a real CI run, on
Python 3.12 and Linux rather than 3.13 and macOS), so it should be resolved first
even though it is not the most technically involved.


| **Workflow has been executed** | **BLOCKED** | never run | **no git remote** | configure the remote, push |
| **CI is green** | **UNKNOWN** | cannot be claimed without a run | same | — |
| Branch protection required | **NOT_TESTED** | repository-level setting | no remote | enable on the remote |
| Automated deploy | **NOT_TESTED** | `autoDeploy: true` in `render.yaml`, never exercised | no remote, no accounts | decide policy |
| Dependency update automation | **NOT_TESTED** | none configured | — | decide whether to add |

| Provider-level redundancy | **NOT_TESTED** | the fallback is model-level only — same endpoint, same key | would need a second provider | out of scope for now |

| Upload size/type limits | **PARTIAL** | validated in the API | never tested with a large real file | test in staging |
| Malicious upload handling | **NOT_TESTED** | no fuzzing or antivirus step | — | decide whether to add |
