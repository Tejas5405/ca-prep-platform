# F-04 — Two backend tests changed verdict depending on whether Redis was running

**Status: FIXED** · **Severity: P3** · **Milestone: P1 defect remediation** · **Reference commit: `a3113af`**

---

## 1. Original defect

The full suite produced a different result depending on whether the developer
happened to have a local Redis:

| Machine state | Result |
|---|---|
| Redis **stopped** | 998 passed, 0 failed, 1 skipped |
| Redis **running** on `127.0.0.1:6379` | 996 passed, **2 failed**, 1 skipped |

Neither failure indicated a product defect. Both indicated that a test was reading
the machine's environment instead of the behaviour it claimed to test — which
makes the suite's green/red verdict a fact about the workstation rather than about
the code.

---

## 2. The two tests, and what was actually wrong with each

### Test 1 — `test_api_contract.py::TestHealthContract::test_health_redis_returns_503_when_unreachable`

**What it intends to verify:** that `/health/redis` answers **503** with
`{"redis": {"status": "down"}}` when Redis is unreachable. That is a real and
important contract — a health check reporting 200 while naming a dependency
"down" makes every uptime monitor useless (the same class of defect
`test_health_db_returns_503_when_the_database_is_unreachable` guards for Postgres).

**Why it passed or failed on machine state.** It "simulated" an unreachable Redis
like this:

```python
monkeypatch.setattr("app.core.dependencies.get_redis_client", exploding_client, raising=True)
```

But `app/api/v1/health.py` does:

```python
from app.core.dependencies import get_redis_client   # bound at IMPORT time
…
async def health_redis(response: Response):
    client = get_redis_client()                        # resolves the LOCAL name
```

Patching the attribute on `app.core.dependencies` after import has **no effect**:
the route body looks up the name that was copied into `app.api.v1.health`'s own
namespace at import time. The patch was a no-op, and the test was silently
exercising the **real** Redis client.

So its verdict was entirely determined by the machine:

- **No Redis running** → the real client raised `ConnectionRefusedError` → the
  route degraded to 503 → `assert 503` **passed**, for the wrong reason.
- **Redis running** → the real client answered `PONG` → the route returned 200 →
  `assert 503` **failed**.

The test had been asserting the correct thing while verifying none of it.

### Test 2 — `test_schema_contract.py::TestSchemaSize::test_the_upload_schema_rejects_an_unknown_kind_as_a_422`

**This one is the interesting one, and it is not itself Redis-aware.**

It passes in isolation, always. It failed only when it ran *after* test 1 in the
same process. The causal chain:

1. Test 1's no-op patch meant a **real** `aioredis` client was created inside
   test 1's `with TestClient(app)` block, and cached in the module global
   `app.core.dependencies._redis_client`.
2. A redis-py async client owns a connection pool bound to the event loop that
   first used it. That `TestClient` context exited, closing its event loop.
3. The **next** `TestClient` to shut down called `close_clients()`, which awaited
   `aclose()` on the still-cached client — bound to a dead loop — raising
   `RuntimeError: Event loop is closed`.
4. That error surfaced inside whichever test happened to be running at the time.
   It was reported against test 2, which had nothing to do with Redis.

So the second failure was **cross-test contamination through a process global**,
surfaced at an arbitrary victim. `pytest-randomly` is not in this project's

---

## 3. Fix

Two complementary changes. Neither disables a test, and no assertion was weakened
or deleted.

### 3a. Patch the name the route actually resolves

`apps/api/tests/test_api_contract.py`:

```python
monkeypatch.setattr("app.api.v1.health.get_redis_client", exploding_client, raising=True)
```

A one-token change, with an in-code explanation of why the other name does not
work — the exact detail that caused the defect. `raising=True` keeps it honest: if
the route is ever refactored to resolve the name lazily from
`app.core.dependencies`, this patch target will stop existing and the test will
fail loudly rather than silently becoming a no-op again.

### 3b. Reset the cached clients around every test

`apps/api/tests/conftest.py` — **new**, with one autouse fixture:

```python
@pytest.fixture(autouse=True)
def _reset_cached_clients():
    from app.core import dependencies

    def _clear() -> None:
        dependencies._engine = None
        dependencies._redis_client = None
        dependencies._session_factory = None

    _clear()
    yield
    _clear()
```

Only the *references* are dropped — no connection is force-closed — because
closing requires the owning event loop, which is exactly what is unavailable at
that point. The sockets belong to a loop that has already ended and the OS
reclaims them; `close_clients()` remains the real shutdown path for a live
process.

This is applied **unconditionally and autouse**, so a test cannot know or care
whether some other test already built a client on a different event loop. It fixes
the general class of bug, not just the instance found on this machine — which
matters, because the contamination was reported at an arbitrary victim and would
otherwise resurface on a future test.

### 3c. Why the queue tests were left alone

`tests/test_queue_boundary.py` uses `fakeredis` rather than a mocked `Queue`, and
that is deliberate and correct: the original bug (`retry={"max": N}` instead of a
`Retry` object) blew up *inside* RQ's job construction, so a mocked queue would
have accepted the dict happily and proved nothing. Those tests use their own
`FakeStrictRedis` instance and never touch the cached global, so they are already
isolated and were correctly left as they are. The genuine live-Redis integration
coverage from the verification milestone (`PING` → `PONG`, an RQ job executing
end-to-end) is a **runtime** check against a real service, not a unit test, and

---

## 5. Verification — the requirement, literally

> `pytest` with Redis OFF and `pytest` with Redis ON should produce equivalent
> intended results.

Full suite, same database, only Redis differing:

| Run | Tests | Failures | Errors | Skipped |
|---|---|---|---|---|
| **Redis ON** + PostgreSQL | 1020 | **0** | **0** | 1 |
| **Redis OFF** + PostgreSQL | 1020 | **0** | **0** | 1 |
| **Redis ON**, no database | 1020 | **0** | **0** | 246 (DB tests skipped) |
| **Redis OFF**, no database | 1020 | **0** | **0** | 246 (DB tests skipped) |

Before the fix the Redis-ON row read `failures=2`. It now reads `0`, and the
Redis-OFF row is unchanged — so the *intended* result is identical in both states,
which is the requirement. The previously-failing pair was also run directly, in
order, with Redis up, and passes.

Redis was genuinely stopped for the OFF run (`redis-cli shutdown nosave`,
confirmed `ConnectionRefusedError`) rather than merely assumed to be down, and
restarted afterwards for the live Redis check.

---

## 6. Files changed

| File | Change |
|---|---|
| `apps/api/tests/conftest.py` | **new** — autouse fixture resetting the cached engine/redis client around every test |
| `apps/api/tests/test_redis_isolation.py` | **new** — 6 determinism regression tests |
| `apps/api/tests/test_api_contract.py` | patch `app.api.v1.health.get_redis_client` instead of the no-op target, with the reason documented in place |
| `docs/F04_REDIS_TEST_ISOLATION.md` | this document |

**No application source file was changed.** The defect was entirely in the tests'
isolation, and the fix is entirely in the tests. The route's behaviour — 503 with
`status: down` when the client fails — is unchanged and still asserted.

### One environment note (F-01 related, not a test defect)

While verifying this, a **concurrent process in the sibling `CA Version 1`
checkout** was found to be sharing the same local PostgreSQL server; it had
recreated the `caprep_test` database from the V1 migration chain, which made 163
database tests fail with `relation "users" does not exist`. That is a shared-local-
infrastructure collision — not a code defect and not a test-isolation defect. The
verification runs above therefore used a database name unique to this checkout
(`caprep_v2_test`). See `docs/F01_DB_REMEDIATION.md` §8.

remains in the live verification report.

---

## 4. New regression tests

`apps/api/tests/test_redis_isolation.py` — **new, 6 tests.** They assert
*determinism* rather than a status code, because "no Redis" is not a property this
repository can guarantee. Both states are simulated with in-process fakes, so they
pass on a machine with Redis running, on one without, and in CI.

| Test | Asserts |
|---|---|
| `test_health_redis_reports_up_when_the_client_answers` | a client that pings → **200** / `up` |
| `test_health_redis_reports_down_when_the_client_raises` | a client that raises → **503** / `down` |
| `test_the_health_route_resolves_the_name_it_is_patched_on` | the route's own module attribute is what gets called — the literal reason failure 1 happened |
| `test_no_test_leaks_a_cached_client_into_the_next_one` | the conftest contract: a client cached by one test is not handed to the next |
| `test_the_result_is_the_same_either_way` (×2) | records whether Redis was reachable, **asserting nothing about it** — deliberately, so the suite cannot become state-dependent again |

The first two are the important pair: the *same* test, in the *same* process,
asserting **opposite** outcomes. That is only possible if the result is driven by
the injected client and never by ambient Redis.

The last test deliberately makes no assertion on the value it observes. A test
that failed based on "is Redis running?" would reintroduce exactly the defect being
fixed.

dependencies, so the order was stable — which is why the failure looked
deterministic and was still wrong.

**The classification the objective asks for:** neither test is a legitimate Redis
integration test, and neither should be disabled. Test 1 is a **unit test of the
route's degradation path** that failed to isolate the dependency it was patching.
Test 2 is an **unwitting victim** of test 1's leak.
