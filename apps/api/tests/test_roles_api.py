"""Role assignment over HTTP, with the Auth Admin call faked and nothing else.

WHY THESE TESTS ARE ABOUT REFUSALS

Granting a role is one line of SQL; the value is in what is REFUSED. The failure
modes are all one-directional — a student who can promote themselves, a
SUPER_ADMIN minted over HTTP, an admin who demotes the only admin — and each of
them is silent: the request succeeds, the row looks correct, and the problem is
discovered when someone audits permissions or when nobody can assign roles any
more. So most of this file asserts 403/409/422, and each assertion names the
specific escalation it stops.

WHAT IS FAKED

Only the network call to Supabase's Auth Admin API. That call is the one thing
here that cannot run locally, and faking it is not a compromise on the interesting
part: the question is not whether Supabase stores the claim (it does) but whether
this code sends the right write, in the right header scheme, to the right user, and
reports honestly when it could not.

The tests therefore assert the OUTGOING REQUEST: the URL carries the auth user id
(not the platform id), the body sets `app_metadata.role` rather than `user_metadata`
— the difference between a role the user cannot edit and the privilege-escalation
bug this codebase already had once — and an `sb_secret_...` key goes on the apikey
header alone.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import dependencies as deps
from app.core.config import Settings
from app.core.identity import get_current_user
from app.core.security import Principal, get_current_principal
from app.integrations.supabase_auth import SupabaseAuthAdmin, claim_headers
from app.main import app as app_module

PATH = "/api/v1/users/{user_id}/role"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "SUPABASE_URL": "https://zyrmlnpvylhcpyaoizyz.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_key",
        "DATABASE_URL": "postgresql+psycopg://postgres@127.0.0.1:5433/caprep_test",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


class FakeAuthAdmin:
    """Records the write instead of performing it."""

    def __init__(self, *, configured: bool = True, fail: bool = False) -> None:
        self.configured = configured
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> FakeAuthAdmin:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def set_role_claim(self, auth_user_id: str, role: str) -> bool:
        if self.fail:
            raise RuntimeError("Auth Admin refused")
        self.calls.append((auth_user_id, role))
        return self.configured


class UserStub:
    """A row the route can read and mutate, without a database."""

    def __init__(self, *, role: str = "STUDENT", email: str = "target@example.com") -> None:
        self.id = uuid.uuid4()
        self.auth_user_id = str(uuid.uuid4())
        self.email = email
        self.role = role
        self.deleted_at = None
        self.is_active = True


class SessionStub:
    def __init__(self, user: UserStub | None) -> None:
        self._user = user
        self.commits = 0

    async def get(self, model: Any, key: Any) -> UserStub | None:
        return self._user

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("this route should not run a query beyond the primary-key get")


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    """Wire the app to a stub admin, a stub session, and a chosen caller."""

    def build(
        *,
        caller_role: str = "ADMIN",
        caller: UserStub | None = None,
        target: UserStub | None = None,
        admin: FakeAuthAdmin | None = None,
        settings: Settings | None = None,
        target_missing: bool = False,
        raise_server_errors: bool = True,
    ) -> tuple[TestClient, SessionStub, FakeAuthAdmin, UserStub, UserStub]:
        caller_user = caller or UserStub(role=caller_role)
        target_user = target or UserStub()
        if target_missing:
            target_user = None  # type: ignore[assignment]
        session = SessionStub(target_user)
        fake_admin = admin or FakeAuthAdmin()

        monkeypatch.setattr("app.api.v1.users.SupabaseAuthAdmin", lambda _settings: fake_admin)
        monkeypatch.setattr("app.api.v1.users.get_settings", lambda: settings or _settings())

        # OVERRIDE `get_current_principal`, NOT `require_role(...)`.
        #
        # `require_role` builds a fresh closure on every call, so an override keyed
        # on one this test did not build never matches - the suite would exercise an
        # unstubbed gate and a wrong-role test would pass for the wrong reason.
        # Overriding the principal is also what keeps the ROLE GATE REAL: a STUDENT
        # caller is refused by the same dependency that refuses them in production.
        app_module.dependency_overrides[get_current_principal] = lambda: Principal(
            auth_user_id=caller_user.auth_user_id,
            email=caller_user.email,
            role=caller_role,
            claims={"sub": caller_user.auth_user_id},
        )
        app_module.dependency_overrides[get_current_user] = lambda: caller_user
        app_module.dependency_overrides[deps.get_db] = lambda: session

        client = TestClient(app_module, raise_server_exceptions=raise_server_errors)
        return client, session, fake_admin, caller_user, target_user

    yield build
    app_module.dependency_overrides.clear()


# ------------------------------------------------------------- the happy path


def test_an_admin_can_promote_a_student_and_the_claim_follows(harness) -> None:
    client, session, admin, _caller, target = harness()

    response = client.post(
        PATH.format(user_id=target.id), json={"role": "EDITOR", "reason": "QA team"}
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["previousRole"] == "STUDENT"
    assert body["role"] == "EDITOR"
    assert body["claimUpdated"] is True
    assert body["note"] is None
    # The row changed...
    assert target.role == "EDITOR"
    assert session.commits == 1
    # ...and the claim was written FOR THE AUTH USER ID, not the platform row id.
    assert admin.calls == [(target.auth_user_id, "EDITOR")]


def test_a_promotion_without_a_secret_key_says_so(harness) -> None:
    """The row is updated and the token is not — the caller must be told.

    This is the difference between "done" and "recorded, but the user cannot use it
    yet". Reporting success would leave an editor whose permissions depend on a
    deployment detail nobody wrote down.
    """
    client, _session, _admin, _caller, target = harness(admin=FakeAuthAdmin(configured=False))

    response = client.post(PATH.format(user_id=target.id), json={"role": "EDITOR"})

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["claimUpdated"] is False
    assert "SUPABASE_SECRET_KEY" in body["note"]
    assert target.role == "EDITOR", "the row write still happened and is still useful"


def test_the_claim_goes_to_app_metadata_not_user_metadata(harness, monkeypatch) -> None:
    """The privilege-escalation bug, asserted at the level of the outgoing request.

    `user_metadata` is writable by the authenticated user. A role read from there is
    a role the user can grant themselves; this codebase has already had that bug
    once. The write must target `app_metadata`.
    """
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"id": "u"})

    settings = _settings()
    admin = SupabaseAuthAdmin(
        settings, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    import asyncio

    auth_id = str(uuid.uuid4())
    assert asyncio.run(admin.set_role_claim(auth_id, "CONTENT_MANAGER")) is True

    assert len(sent) == 1
    request = sent[0]
    assert request.url.path.endswith(f"/auth/v1/admin/users/{auth_id}")
    assert b"app_metadata" in request.content
    assert b"user_metadata" not in request.content
    # An sb_ key must not be sent as a bearer token: it is not a JWT, and the
    # platform answers `403 Invalid Compact JWS` for that shape.
    assert request.headers["apikey"] == "sb_secret_test_key"
    assert "authorization" not in {k.lower() for k in request.headers}


def test_a_legacy_service_role_key_is_also_sent_as_a_bearer() -> None:
    """Two key systems that authenticate differently — the documented trap."""
    assert "Authorization" in claim_headers("eyJhbGciOiJIUzI1NiJ9.legacy")
    assert "Authorization" not in claim_headers("sb_secret_new")


# ------------------------------------------------------------------ refusals


def test_a_student_cannot_assign_roles(harness) -> None:
    """The escalation that matters most: the endpoint is not self-service."""
    client, session, admin, _caller, target = harness(caller_role="STUDENT")

    response = client.post(PATH.format(user_id=target.id), json={"role": "ADMIN"})

    # The REAL `require_permission(MANAGE_ROLES)` dependency answers this, not a stub.
    # The refusal names the PERMISSION rather than the role, which is what the admin
    # console needs in order to say "this account is missing MANAGE_ROLES" instead of
    # "you are not an ADMIN" - the role the student would need is not the point.
    assert response.status_code == 403, response.text
    assert "MANAGE_ROLES" in response.json()["detail"]
    assert target.role == "STUDENT"
    assert admin.calls == []
    assert session.commits == 0


def test_super_admin_cannot_be_minted_over_http(harness) -> None:
    """The last role that can be granted is deliberately not grantable here.

    If SUPER_ADMIN were reachable from an endpoint, one compromised admin session
    would escalate to a role no other admin can take back.
    """
    client, _session, admin, _caller, target = harness()

    response = client.post(PATH.format(user_id=target.id), json={"role": "SUPER_ADMIN"})

    assert response.status_code == 422, response.text
    assert "SUPER_ADMIN" not in response.json()["detail"].split("must be one of")[1].split(".")[0]
    assert target.role == "STUDENT"
    assert admin.calls == []


def test_an_unknown_role_is_refused_with_the_list(harness) -> None:
    client, _session, _admin, _caller, target = harness()

    response = client.post(PATH.format(user_id=target.id), json={"role": "WIZARD"})

    assert response.status_code == 422
    assert "EDITOR" in response.json()["detail"]
    assert target.role == "STUDENT"


def test_an_admin_cannot_demote_themselves(harness) -> None:
    """The lock-out that has no recovery path.

    With a small staff there is often exactly one admin. Demoting themselves while
    tidying up leaves nobody able to assign roles, and the fix is a database edit
    by the person who just locked themselves out.
    """
    caller = UserStub(role="ADMIN")
    client, session, admin, _caller, target = harness(caller=caller, target=caller)

    response = client.post(PATH.format(user_id=target.id), json={"role": "STUDENT"})

    assert response.status_code == 409, response.text
    assert "another admin" in response.json()["detail"]
    assert target.role == "ADMIN"
    assert admin.calls == []
    assert session.commits == 0


def test_an_admin_may_re_assert_their_own_role(harness) -> None:
    """The guard is on demotion, not on the caller's own row.

    A re-run of the same assignment is how an operator fixes a claim that never
    landed, so refusing every self-write would block the repair.
    """
    caller = UserStub(role="ADMIN")
    client, _session, admin, _caller, target = harness(caller=caller, target=caller)

    response = client.post(PATH.format(user_id=target.id), json={"role": "ADMIN"})

    assert response.status_code == 200, response.text
    assert admin.calls == [(caller.auth_user_id, "ADMIN")]


def test_an_unknown_or_deleted_user_is_a_404(harness) -> None:
    client, _session, admin, _caller, _target = harness(target_missing=True)
    missing = uuid.uuid4()

    response = client.post(PATH.format(user_id=missing), json={"role": "EDITOR"})

    assert response.status_code == 404
    assert admin.calls == []


def test_a_deleted_row_is_not_promoted(harness) -> None:
    """Soft-deleted users are gone; a role write to one is a write to nobody."""
    ghost = UserStub()
    ghost.deleted_at = object()
    client, _session, admin, _caller, target = harness(target=ghost)

    response = client.post(PATH.format(user_id=target.id), json={"role": "EDITOR"})

    assert response.status_code == 404
    assert admin.calls == []


def test_a_failed_claim_write_is_not_reported_as_success(harness) -> None:
    """A silently swallowed 401 from the Auth Admin API is a broken promotion loop."""
    # `raise_server_errors=False` because that is what a real client sees: the
    # framework turns the unhandled error into a 500 after logging it. The point of
    # the assertion is the STATUS the caller gets, not the traceback.
    client, _session, _admin, _caller, target = harness(
        admin=FakeAuthAdmin(fail=True), raise_server_errors=False
    )

    response = client.post(PATH.format(user_id=target.id), json={"role": "EDITOR"})

    # A 5xx, not a 200. Catching the error and reporting success is how a promotion
    # loop breaks without anyone noticing.
    assert response.status_code == 500, response.text
    assert "claimUpdated" not in response.text


def test_the_role_field_is_not_accepted_on_the_profile_endpoint(harness) -> None:
    """The regression guard: role must not creep back into `PATCH /me`.

    `StrictRequest` forbids extra fields, so this is a 422 rather than a silently
    ignored key — the important part, because a silently ignored `role` would let
    someone believe they had promoted themselves.
    """
    caller = UserStub(role="ADMIN")
    client, _session, _admin, _caller, _target = harness(caller=caller)

    response = client.patch("/api/v1/me", json={"role": "ADMIN"})

    assert response.status_code == 422, response.text
