"""Admin role promotion, end to end, including the Supabase claim write.

THE TEST PHASE 4 DEFERRED, AND WHY IT WAS DEFERRED

Phase 4 found and fixed a real defect: `SupabaseAuthAdmin()` was called with no
`settings`, so the call raised TypeError, the route's broad `except Exception`
swallowed it, and `claimUpdated` was reported **False on every role change in
production**. The database said EDITOR; the token kept saying STUDENT. No log
line, no error, no alert - the promotion simply did not take effect for the one
thing it was for.

The fix was verified by mypy, which is the wrong tool for this. It proved a
required argument is now passed. It cannot prove the claim was actually written
to Supabase, because the code never talks to Supabase in a unit test. `tests/
test_roles_api.py` covers this route with hand-written fakes, which is a test of
the fake: it would have passed identically before and after the fix.

So this file asserts the three things only a real stack can:

  1. the database row really changes (read back on a second connection);
  2. the outbound Supabase request is the documented one - `PUT
     {supabase_url}/auth/v1/admin/users/{auth_uid}` carrying
     `app_metadata.role` - rather than merely "some HTTP call happened";
  3. when Supabase fails, the DATABASE still commits and the endpoint still
     answers 200 with `claimUpdated: false`, because Postgres is the source of
     truth for authorization and the claim is a cache of it.

The third is the one that is easy to get wrong in the other direction. Rolling
the transaction back because the token write failed would leave the user unable
to log in at all, to fix a claim that is corrected on the next login anyway.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.main import app
from app.models.user import User

from ._db import observe, run_in_database

pytestmark = pytest.mark.postgres

SUPABASE_URL = "https://stub.supabase.test"


class Actor:
    def __init__(self, session: Any, user: User) -> None:
        self._session = session
        self._user = user

    async def __aenter__(self) -> httpx.AsyncClient:
        app.dependency_overrides[get_db] = lambda: self._session

        # The claim write needs a configured Supabase: `SupabaseAuthAdmin` returns
        # False WITHOUT CALLING ANYTHING when the secret key is absent, which
        # would make this test assert a false negative for the wrong reason.
        from app.core.config import get_settings

        real_settings = get_settings()
        app.dependency_overrides[get_settings] = lambda: real_settings.model_copy(
            update={
                "supabase_url": SUPABASE_URL,
                "supabase_secret_key": "stub-service-role-key",
            }
        )
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            auth_user_id=self._user.auth_user_id,
            email=self._user.email,
            role=self._user.role,
            claims={"sub": self._user.auth_user_id},
        )

        async def _caller() -> User:
            await self._session.refresh(self._user)
            return self._user

        from app.core.identity import get_current_user

        app.dependency_overrides[get_current_user] = _caller
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        return self._client

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()
        app.dependency_overrides.clear()


async def make_user(session: Any, role: str) -> User:
    user = User(
        auth_user_id=f"admin-e2e-{role.lower()}-{uuid.uuid4().hex[:10]}",
        email=f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        display_name=role.title(),
        role=role,
    )
    session.add(user)
    await session.commit()
    return user


class RecordingTransport(httpx.AsyncBaseTransport):
    """Captures the outbound Supabase call and answers with a scripted status.

    `SupabaseAuthAdmin` builds its own `httpx.AsyncClient` unless one is injected,
    and the route does not inject one - which is correct, it should not have to.
    So the client is intercepted where it is constructed rather than by changing
    the route.
    """

    def __init__(self, status: int) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"id": "stub"})


class _HttpxShim:
    """Redirects ONLY the client `supabase_auth` builds, not the process's.

    `import httpx` binds the MODULE, so `monkeypatch.setattr(httpx, "AsyncClient",
    ...)` is global: the test's own ASGI client is built through the same symbol
    and would be handed the recording transport too, so the test would assert
    against its own request instead of the outbound one. Replacing the name
    inside the `supabase_auth` module rebinds only what that module looks up.
    """

    def __init__(self, real: Any, transport: httpx.AsyncBaseTransport) -> None:
        self._real = real
        self._transport = transport

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def AsyncClient(self, *args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = self._transport
        return self._real.AsyncClient(*args, **kwargs)


@pytest.fixture
def stub_supabase(monkeypatch: pytest.MonkeyPatch):
    """Install a transport that records the claim write, and yield the recorder."""
    import app.integrations.supabase_auth as supabase_auth

    def _install(status: int = 200) -> RecordingTransport:
        transport = RecordingTransport(status)
        monkeypatch.setattr(supabase_auth, "httpx", _HttpxShim(httpx, transport))
        return transport

    return _install


def test_promoting_a_user_writes_the_database_and_the_supabase_claim(
    database_url: str, stub_supabase
) -> None:
    """The Phase 4 defect, as an end-to-end assertion rather than a type check."""

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        target = await make_user(session, "STUDENT")

        transport = stub_supabase(200)
        async with Actor(session, admin) as client:
            response = await client.patch(
                f"/api/v1/admin/users/{target.id}", json={"role": "EDITOR"}
            )

        return {
            "status": response.status_code,
            "body": response.json(),
            "target_auth_id": target.auth_user_id,
            "target_id": str(target.id),
            "requests": [
                {
                    "method": r.method,
                    "url": str(r.url),
                    "body": r.content.decode(),
                    "apikey": r.headers.get("apikey"),
                    "authorization": r.headers.get("Authorization"),
                }
                for r in transport.requests
            ],
        }

    result = run_in_database(database_url, body)

    assert result["status"] == 200
    # The route answers through the standard envelope: {"data": ..., "meta": ...}.
    data = result["body"]["data"]
    assert data["claimUpdated"] is True, (
        "claimUpdated was False: the Supabase claim was NOT written, which is the "
        "exact production failure Phase 4 fixed"
    )
    assert data["role"] == "EDITOR"

    # Exactly one outbound call, to the documented endpoint, for THIS user.
    assert len(result["requests"]) == 1, result["requests"]
    call = result["requests"][0]
    assert call["method"] == "PUT", call
    assert call["url"] == f"{SUPABASE_URL}/auth/v1/admin/users/{result['target_auth_id']}", call
    assert '"role":"EDITOR"' in call["body"].replace(" ", ""), call
    assert "app_metadata" in call["body"], call
    # Authenticated as the service_role key, not as the promoted user.
    assert call["apikey"], "the Supabase admin call must carry the service key"
    assert call["authorization"], "the Supabase admin call must be authorised"


def test_a_supabase_failure_still_commits_the_role(database_url: str, stub_supabase) -> None:
    """DB is the source of truth; the claim is a cache, so a cache miss is not a rollback."""

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        target = await make_user(session, "STUDENT")
        original_auth_id = target.auth_user_id

        transport = stub_supabase(500)
        async with Actor(session, admin) as client:
            response = await client.patch(
                f"/api/v1/admin/users/{target.id}", json={"role": "EDITOR"}
            )

        # Read the row back on a SECOND connection: the commit either happened
        # or it did not, and "the response said 200" is not evidence.
        async def persisted_role() -> str | None:
            async with observe(database_url) as reader:
                return (
                    await reader.execute(select(User.role).where(User.id == target.id))
                ).scalar_one_or_none()

        return {
            "status": response.status_code,
            "claim_updated": response.json()["data"]["claimUpdated"],
            "attempts": len(transport.requests),
            "auth_id_unchanged": original_auth_id,
            "role": await persisted_role(),
        }

    result = run_in_database(database_url, body)

    assert result["attempts"] == 1, "Supabase was never called, so this proves nothing"
    # The whole point: the promotion survives the token write failing.
    assert result["status"] == 200
    assert result["role"] == "EDITOR", (
        "a Supabase 500 must not roll back the promotion; the row is what "
        "authorization reads, and rolling it back locks the user out entirely"
    )
    assert result["claim_updated"] is False
