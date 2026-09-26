"""Identity provisioning and barring, end to end, against a real database.

WHY THIS FILE EXISTS

`GET /me` was returning 500 for every authenticated caller. The cause was not the
route: `get_current_user` called `SqlUserRepository.provision`, and that method did
not exist. The whole suite stayed green, because the API contract tests override
`get_current_user` with a stub user - so the default dependency chain, the one every
real request takes, had never been executed by a test. That is the shape of bug this
file catches: not a wrong answer, but an untested path.

So the tests below override exactly ONE dependency, `get_current_principal`, which is
the boundary a signed token crosses. Everything past it is the real code: the real
repository, the real session, the real commit, on real PostgreSQL. Assertions about
durability read their evidence through a SECOND connection (`observe`), because a
read on the writing session can be answered from its identity map or from an
uncommitted transaction - and would therefore pass even with no commit at all.

WHAT "PROVISIONING" HAS TO GET RIGHT, AND WHY EACH ONE IS A TEST

  * **First request works.** The account row is created on first sight, so a student
    who signed in with Google never sees a second sign-up form.
  * **Second request reuses it.** Provisioning twice creates a second row or fails
    on the unique index, depending on which race you lose.
  * **Two simultaneous requests converge.** The app opens several screens at once
    and each fires a request; both can arrive before either committed. The insert
    runs in a SAVEPOINT so the loser re-reads instead of 500ing.
  * **Email is not the identity.** A password account and a Google account can
    legitimately share an address; collapsing them would merge two people.
  * **Staff role comes from server configuration, never the request.** A token
    claiming ADMIN must not promote its own row, and an email that merely LOOKS
    administrative must not be on the allow-list.
  * **A barred account stays barred.** A suspended or erased row must not be
    re-provisioned by the suspended person signing in again. This was a REAL defect
    found while writing this file: `get_by_auth_id` documented that it filtered
    `is_active` and did not, so deactivation was unenforceable.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import dependencies as deps
from app.core.config import get_settings
from app.core.security import Principal, get_current_principal
from app.main import app as app_module
from app.models.user import User

from ._db import async_url, observe, run_in_database

pytestmark = pytest.mark.postgres

#: An address a test may configure as a bootstrap administrator. Spelled out
#: rather than taken from a fixture: the point of the test is that THIS value in
#: configuration produces an ADMIN row.
BOOTSTRAP_EMAIL = "admin@caprep.in"

SUBJECT = "ae7f8f5e-6d1c-4a51-9f2c-2f4c1a4d0b31"


# ------------------------------------------------------------------ the harness


@pytest.fixture(autouse=True)
def _app_on_the_test_database(monkeypatch: pytest.MonkeyPatch):
    """Point the APP's engine at the test database, and give it a clean one.

    The client is created before any route runs, so the engine is built from these
    settings - which means the test never has to override `get_db`, and therefore
    never replaces the session layer it is trying to exercise.

    `DATABASE_URL` (not `TEST_DATABASE_URL`): the app's own configuration reads the
    former, and the whole point is to exercise the app's own resolution.
    """
    url = async_url()
    assert url is not None
    sync = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    monkeypatch.setenv("DATABASE_URL", sync)
    monkeypatch.delenv("DIRECT_DATABASE_URL", raising=False)
    # The allow-list is EMPTY by default, and that default is the security
    # property: an unset value grants nothing. Cleared here so a developer's own
    # `.env` cannot make a student an admin in the middle of the suite, and set
    # explicitly by the one test that needs it.
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAILS", raising=False)

    def reset() -> None:
        get_settings.cache_clear()
        # Module globals rather than an lru_cache, so they are cleared by hand.
        # An engine built on a previous test's closed event loop is the failure
        # this prevents - the same trap `_db.py` documents for its own engine.
        deps._engine = None
        deps._session_factory = None

    reset()
    yield
    reset()


@contextlib.contextmanager
def signed_in(principal: Principal) -> Iterator[TestClient]:
    """A client that presents a verified token, on the real dependency chain.

    WRAPPED IN `with` SO THE ENGINE IS DISPOSED ON THE RIGHT LOOP. The engine's
    pooled connections belong to the loop the app ran on; disposing them after that
    loop has closed is the classic source of intermittent "Event loop is closed"
    teardown errors. So the app's own `close_clients` shutdown hook is called
    through the still-open portal.
    """
    prior = app_module.dependency_overrides.get(get_current_principal)
    app_module.dependency_overrides[get_current_principal] = lambda: principal
    try:
        with TestClient(app_module) as client:
            try:
                yield client
            finally:
                client.portal.call(deps.close_clients)
    finally:
        if prior is None:
            app_module.dependency_overrides.pop(get_current_principal, None)
        else:
            app_module.dependency_overrides[get_current_principal] = prior


def _principal(*, sub: str = SUBJECT, email: str | None = "student@example.com") -> Principal:
    return Principal(auth_user_id=sub, email=email, role="STUDENT", claims={"sub": sub})


def _user_repo():
    """Imported lazily so a broken module fails inside a test, not at collection."""
    from app.repositories.users import SqlUserRepository

    return SqlUserRepository()


async def _users(session, *, auth_user_id: str | None = None) -> list[User]:
    stmt = select(User).order_by(User.created_at)
    if auth_user_id is not None:
        stmt = stmt.where(User.auth_user_id == auth_user_id)
    return list((await session.execute(stmt)).scalars().all())


async def _count(session, *, auth_user_id: str) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.auth_user_id == auth_user_id)
    )


# -------------------------------------------------------------------- provision


def test_the_first_authenticated_request_creates_the_account(database_url: str) -> None:
    """The path that was returning 500, exercised the way a request takes it."""

    async def body(session) -> dict[str, Any]:
        with signed_in(_principal()) as client:
            response = client.get("/api/v1/me")

        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        # The response is addressed BY the verified subject, so a token cannot read
        # someone else's profile by changing a parameter.
        assert payload["email"] == "student@example.com"
        assert payload["role"] == "STUDENT"

        async with observe(database_url) as other:
            return {"rows": await _users(other, auth_user_id=SUBJECT)}

    rows = run_in_database(database_url, body)["rows"]
    # Read on a SEPARATE connection: a row visible only to the writing session
    # would mean the commit never happened, and every later request would
    # provision again.
    assert len(rows) == 1
    assert rows[0].email == "student@example.com"
    assert rows[0].is_active is True
    assert rows[0].deleted_at is None


def test_a_second_request_reuses_the_same_row(database_url: str) -> None:
    """Signing in repeatedly must not produce two accounts, or one 500 on the index."""

    async def body(session) -> None:
        # One client, three requests: the realistic shape, and it keeps the engine
        # on a single event loop.
        with signed_in(_principal()) as client:
            for _ in range(3):
                assert client.get("/api/v1/me").status_code == 200

        async with observe(database_url) as other:
            assert await _count(other, auth_user_id=SUBJECT) == 1

    run_in_database(database_url, body)


def test_two_requests_racing_converge_on_one_account(database_url: str) -> None:
    """Both callers succeed and resolve to the SAME row.

    The genuine interleaving is not reproducible from a test - it depends on which
    transaction commits first - so the shared state is created directly and the
    second caller is made to hit the unique index. That is the loser's path either
    way: it inserts, PostgreSQL rejects it, and the SAVEPOINT recovery read has to
    find the winner instead of failing the request.
    """

    async def body(session) -> None:
        repo = _user_repo()
        winner = await repo.provision(session, auth_user_id=SUBJECT, email="raced@example.com")
        await session.commit()

        loser = await repo.provision(session, auth_user_id=SUBJECT, email="raced@example.com")
        # Not merely "no exception": the loser must return the WINNER's row. A
        # caller handed a detached, unsaved User would write progress against a
        # primary key that does not exist.
        assert loser.id == winner.id

        async with observe(database_url) as other:
            assert await _count(other, auth_user_id=SUBJECT) == 1
            assert (await _users(other))[0].id == winner.id

    run_in_database(database_url, body)


def test_the_same_email_under_two_subjects_is_two_accounts(database_url: str) -> None:
    """Email addresses are recycled; subjects are not.

    A student who signed up with a password and later signs in with Google can
    legitimately present the same address under a second subject. Keying accounts on
    the email would either merge two identities or, with a unique column, break the
    second sign-in outright.
    """

    async def body(session) -> None:
        second = "b1c2d3e4-1111-4222-8333-444455556666"
        overlapping = _principal(sub=SUBJECT, email="shared@example.com")

        with signed_in(overlapping) as client:
            assert client.get("/api/v1/me").status_code == 200

        overlapping = Principal(
            auth_user_id=second,
            email="shared@example.com",
            role="STUDENT",
            claims={"sub": second},
        )
        with signed_in(overlapping) as client:
            assert client.get("/api/v1/me").status_code == 200

        async with observe(database_url) as other:
            rows = await _users(other)
        assert {row.auth_user_id for row in rows} == {SUBJECT, second}

    run_in_database(database_url, body)


def test_an_empty_allow_list_promotes_nobody(database_url: str) -> None:
    """The default is no administrators at all.

    A shipped default list would be a backdoor in every deployment that never
    touched the setting, so the default is empty and this test pins it. It is the
    reason the test below has to opt in explicitly.
    """

    async def body(session) -> None:
        with signed_in(_principal(email=BOOTSTRAP_EMAIL)) as client:
            response = client.get("/api/v1/me")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["role"] == "STUDENT"

    run_in_database(database_url, body)


def test_a_configured_bootstrap_email_becomes_an_admin_and_nothing_else_does(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staff roles come from server configuration, never from the request.

    The last three cases are the ones that matter: an address that merely LOOKS
    administrative must not be promoted, or the allow-list is decoration. The
    near-misses are the shapes a naive `endswith` or `startswith` check would let
    through - a subdomain suffix and a longer local part.
    """
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAILS", BOOTSTRAP_EMAIL)
    get_settings.cache_clear()

    async def body(session) -> None:
        cases = [
            (BOOTSTRAP_EMAIL, "ADMIN"),
            ("Admin@Caprep.IN", "ADMIN"),
            ("admin@caprep.in.evil.example", "STUDENT"),
            ("administrator@caprep.in", "STUDENT"),
            (None, "STUDENT"),
        ]
        for index, (email, expected) in enumerate(cases):
            sub = f"00000000-0000-4000-8000-00000000000{index}"
            principal = Principal(
                auth_user_id=sub,
                email=email,
                role="STUDENT",
                claims={"sub": sub},
            )
            with signed_in(principal) as client:
                response = client.get("/api/v1/me")
            assert response.status_code == 200, response.text
            assert response.json()["data"]["role"] == expected, email

    run_in_database(database_url, body)


def test_a_token_without_an_email_still_gets_a_student_account(database_url: str) -> None:
    """Supabase omits the email claim for some providers and for anonymous sign-ins.

    Provisioning has to handle the absence - and the absence must never widen what
    the account can do.
    """

    async def body(session) -> None:
        with signed_in(_principal(email=None)) as client:
            response = client.get("/api/v1/me")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["role"] == "STUDENT"
        assert response.json()["data"]["email"] is None

    run_in_database(database_url, body)


def test_a_token_claiming_admin_does_not_promote_its_own_row(database_url: str) -> None:
    """The role in the token is what `require_role` reads - and only Supabase's Admin
    API can set it, which needs a secret this deployment may not hold.

    The database row is the record of what someone IS. Provisioning writes it from
    the signed email and nothing else.
    """

    async def body(session) -> None:
        principal = Principal(
            auth_user_id=SUBJECT,
            email="student@example.com",
            role="ADMIN",
            claims={"sub": SUBJECT, "app_metadata": {"role": "ADMIN"}},
        )
        with signed_in(principal) as client:
            assert client.get("/api/v1/me").status_code == 200

        async with observe(database_url) as other:
            rows = await _users(other, auth_user_id=SUBJECT)
        assert rows[0].role == "STUDENT"

    run_in_database(database_url, body)


def test_concurrent_first_requests_do_not_deadlock(database_url: str) -> None:
    """Three OVERLAPPING provisioning calls, not three sequential ones.

    The sequential race above proves the recovery read works; it does not prove the
    savepoint survives real concurrency, which is when a blocked insert actually
    waits on another transaction's index entry. Against a real server this is the
    difference between "the code path exists" and "it works under load".
    """

    async def body(session) -> None:
        factory = deps.get_session_factory()

        async def one() -> Any:
            async with factory() as inner:
                user = await _user_repo().provision(
                    inner, auth_user_id=SUBJECT, email="parallel@example.com"
                )
                await inner.commit()
                return user.id

        results = await asyncio.gather(one(), one(), one(), return_exceptions=True)

        failures = [r for r in results if isinstance(r, BaseException)]
        assert not failures, failures
        # Every caller saw the same account.
        assert len(set(results)) == 1

        async with observe(database_url) as other:
            assert await _count(other, auth_user_id=SUBJECT) == 1

    run_in_database(database_url, body)


# ---------------------------------------------------------------------- barring


def test_a_deactivated_account_is_refused_and_is_not_recreated(database_url: str) -> None:
    """Deactivation must survive the user signing in again.

    This is the defect this file found. `get_by_auth_id` filtered `deleted_at` and
    documented a filter of `is_active` that it did not apply, so a suspended
    student's very next request provisioned a SECOND row for the same subject and
    let them straight back in. A control the subject can undo by retrying is not a
    control.
    """

    async def body(session) -> None:
        user = await _user_repo().provision(
            session, auth_user_id=SUBJECT, email="suspended@example.com"
        )
        user.is_active = False
        await session.commit()
        suspended_id = user.id

        with signed_in(_principal(email="suspended@example.com")) as client:
            response = client.get("/api/v1/me")

        assert response.status_code == 403, response.text
        assert "deactivated" in response.json()["detail"].lower()

        async with observe(database_url) as other:
            rows = await _users(other, auth_user_id=SUBJECT)
        # One row, still inactive: the refusal did not provision a replacement.
        assert len(rows) == 1
        assert rows[0].id == suspended_id
        assert rows[0].is_active is False

    run_in_database(database_url, body)


def test_a_deleted_account_is_refused_rather_than_resurrected(database_url: str) -> None:
    """An erasure is not undone by signing in.

    The soft-deleted row still occupies the unique index on `auth_user_id`, so a
    token whose subject maps to it cannot provision a fresh account - and it must
    not resolve to the erased one either. Both readings are refused.
    """

    async def body(session) -> None:
        user = await _user_repo().provision(session, auth_user_id=SUBJECT, email="gone@example.com")
        user.deleted_at = datetime.now(UTC)
        await session.commit()

        with signed_in(_principal(email="gone@example.com")) as client:
            response = client.get("/api/v1/me")

        assert response.status_code == 403, response.text

        async with observe(database_url) as other:
            assert await _count(other, auth_user_id=SUBJECT) == 1

    run_in_database(database_url, body)


def test_the_race_recovery_read_can_see_a_barred_row(database_url: str) -> None:
    """The recovery read is deliberately NOT filtered by activity.

    It has to find a suspended row rather than treat it as a free slot on the
    unique index - so if it returned None here, the loser of a race would insert
    again and fail forever. Whether the caller may USE the row it recovered is
    decided in `get_current_user`, and that decision is asserted separately by the
    two tests above.
    """

    async def body(session) -> None:
        repo = _user_repo()
        winner = await repo.provision(
            session, auth_user_id=SUBJECT, email="race-barred@example.com"
        )
        winner.is_active = False
        await session.commit()
        winner_id = winner.id

        recovered = await repo.provision(
            session, auth_user_id=SUBJECT, email="race-barred@example.com"
        )
        assert recovered.id == winner_id

        # The same read, with the flags off, must NOT see it - that asymmetry is
        # the whole reason the flags exist.
        assert await repo.get_by_auth_id(SUBJECT) is None
        assert (await repo.get_by_auth_id(SUBJECT, include_inactive=True)) is not None

    run_in_database(database_url, body)


def test_barring_is_independent_per_subject(database_url: str) -> None:
    """Suspending one account must not lock out anyone else.

    A guard written on the wrong key - a shared email, a role, a global flag -
    passes a single-account test and locks out the platform. So this one suspends
    one subject and signs in as another.
    """

    async def body(session) -> None:
        barred = await _user_repo().provision(
            session, auth_user_id=SUBJECT, email="shared@example.com"
        )
        barred.is_active = False
        await session.commit()

        other_sub = str(uuid.uuid4())
        other = Principal(
            auth_user_id=other_sub,
            email="shared@example.com",
            role="STUDENT",
            claims={"sub": other_sub},
        )
        with signed_in(other) as client:
            assert client.get("/api/v1/me").status_code == 200

        async with observe(database_url) as other_session:
            assert await _count(other_session, auth_user_id=other_sub) == 1

    run_in_database(database_url, body)
