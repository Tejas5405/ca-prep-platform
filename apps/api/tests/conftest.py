"""Suite-wide isolation for the process-global clients.

WHY THIS FILE EXISTS (F-04)
===========================

``app.core.dependencies`` caches two module globals - ``_engine`` and
``_redis_client``. A redis-py async client owns a connection pool bound to the
event loop that first used it, so a client created inside one
``with TestClient(app)`` block is UNUSABLE from the next one. Two failures were
observed live, and only when the developer happened to run a local Redis:

  1. ``test_health_redis_returns_503_when_unreachable`` monkeypatched
     ``app.core.dependencies.get_redis_client`` - a name ``app/api/v1/health.py``
     had already bound at import time, so the patch did nothing. The real client
     answered 200 and ``assert 503`` failed. (Fixed in the test itself, by
     patching the name the route actually resolves.)

  2. Because that test really did create a live client, the cached client stayed
     bound to the now-closed event loop, and the NEXT ``TestClient`` to shut down
     raised ``RuntimeError: Event loop is closed`` inside ``close_clients()``.
     That surfaced as a failure in an unrelated test -
     ``test_schema_contract.py::TestSchemaSize::...422`` - which passes in
     isolation. A test whose verdict depends on whether a colleague has a Redis
     running, and on alphabetical ordering, is not a test.

THE FIX: reset the cached clients around every test, so no test can hand a
loop-bound client to the next one. The result is then identical whether Redis is
up or down, which is what the requirement actually asks for.

This is not "disabling" anything: the health probe still has to report
"down" by contacting a client that fails, and ``test_queue_boundary.py`` still
exercises a real RQ ``Queue`` over ``fakeredis`` - the bug being in RQ's job
construction, a mocked queue would have proved nothing. Isolation is applied at
the lifecycle boundary, not by deleting assertions.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_cached_clients():
    """Drop the cached engine/redis client before and after every test.

    Autouse and unconditional: the point is that a test cannot know or care
    whether some other test already built a client on a different event loop.

    Only the references are dropped - no connection is force-closed here,
    because closing requires the owning loop, which is exactly what is
    unavailable by this point. The sockets belong to a loop that has already
    ended, so the OS reclaims them; ``close_clients()`` remains the real
    shutdown path for a live process.
    """
    from app.core import dependencies

    def _clear() -> None:
        dependencies._engine = None
        dependencies._redis_client = None
        dependencies._session_factory = None

    _clear()
    yield
    _clear()
