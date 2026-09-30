"""Keyset pagination against a live PostgreSQL, through the real HTTP route.

WHY THIS FILE EXISTS

`app/core/pagination.py` is the kind of code that passes its own unit tests
while being wrong. The keyset predicate is the whole product:

    (created_at, id) < (cursor_created_at, cursor_id)

and there are two ways to get it subtly wrong. Drop the `id` tiebreaker and
every row sharing a timestamp with the cursor is skipped. Flip `<` to `>` and
the walk runs the wrong way entirely. Neither shows up in a test that paginates
five rows with five distinct timestamps - which is exactly what a naive test
does, because fixtures are created in a loop microseconds apart and the
database helpfully gives them all distinct clocks.

So the fixtures here deliberately COLLIDE timestamps. A batch of rows sharing
one `created_at` is normal on an audit log, and it is the only input that can
tell the tiebreaker from its absence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.main import app
from app.models.engagement import AuditLog
from app.models.user import User

from ._db import run_in_database

pytestmark = pytest.mark.postgres

AUDIT = "/api/v1/admin/audit"


class Actor:
    def __init__(self, session: Any, user: User) -> None:
        self._session = session
        self._user = user

    async def __aenter__(self) -> httpx.AsyncClient:
        app.dependency_overrides[get_db] = lambda: self._session
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


async def make_admin(session: Any) -> User:
    user = User(
        auth_user_id=f"cursor-admin-{uuid.uuid4().hex[:10]}",
        email=f"cursor-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Cursor Admin",
        role="ADMIN",
    )
    session.add(user)
    await session.commit()
    return user


async def seed_logs(session: Any, actor: User, count: int, *, collide: bool = True) -> None:
    """Insert `count` audit rows, oldest first.

    `collide=True` gives every row the SAME created_at. That is the case a
    timestamp-only cursor gets wrong, so it is the default rather than an edge
    case bolted on afterwards.
    """
    base = datetime.now(UTC) - timedelta(hours=1)
    for index in range(count):
        session.add(
            AuditLog(
                actor_user_id=actor.id,
                actor_email=actor.email,
                actor_role=actor.role,
                action=f"cursor.test.{index}",
                summary=f"row {index}",
                target_type="TEST",
                target_id=str(index),
                changes={},
                # Same timestamp for every row when colliding.
                created_at=base if collide else base + timedelta(seconds=index),
            )
        )
    await session.commit()


def _rows(payload: dict[str, Any]) -> list[str]:
    return [row["id"] for row in payload["data"]]


def test_a_page_is_a_slice_and_carries_a_cursor(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        admin = await make_admin(session)
        await seed_logs(session, admin, 5)
        async with Actor(session, admin) as client:
            first = (await client.get(AUDIT, params={"limit": 2})).json()
            return {"first": first, "ids": _rows(first)}

    result = run_in_database(database_url, body)
    assert len(result["ids"]) == 2, "limit=2 must return exactly 2 rows"
    assert result["first"]["meta"]["hasMore"] is True
    assert result["first"]["meta"].get("nextCursor"), "a partial page must carry a cursor"
    # data stays a LIST: same envelope as the offset path, so a client never has
    # to detect which kind of response it received.
    assert isinstance(result["first"]["data"], list)


def test_walking_to_the_end_terminates_and_never_repeats(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        admin = await make_admin(session)
        await seed_logs(session, admin, 5)
        seen: list[str] = []
        pages: list[int] = []
        cursor: str | None = None
        async with Actor(session, admin) as client:
            for _ in range(10):  # hard stop: a non-terminating walk is a failure
                params: dict[str, Any] = {"limit": 2}
                if cursor:
                    params["cursor"] = cursor
                payload = (await client.get(AUDIT, params=params)).json()
                seen.extend(_rows(payload))
                pages.append(len(payload["data"]))
                cursor = payload["meta"].get("nextCursor")
                if not payload["meta"]["hasMore"]:
                    break
            return {
                "seen": seen,
                "pages": pages,
                "unique": len(set(seen)),
                "final_cursor": cursor,
                "final_has_more": payload["meta"]["hasMore"],
            }

    result = run_in_database(database_url, body)
    assert result["unique"] == len(result["seen"]), f"a row was returned twice: {result['seen']}"
    assert len(result["seen"]) == 5, result["seen"]
    assert result["final_has_more"] is False
    assert result["final_cursor"] is None, "the last page must not advertise a next cursor"
    assert result["pages"] == [2, 2, 1], result["pages"]


def test_a_corrupt_cursor_is_a_400_not_a_500(database_url: str) -> None:
    """The failure mode this guards: a 500 sends an operator to the server logs
    for what is a mistyped query parameter."""

    async def body(session) -> dict[str, Any]:
        admin = await make_admin(session)
        await seed_logs(session, admin, 2)
        statuses: dict[str, int] = {}
        async with Actor(session, admin) as client:
            for label, cursor in (
                ("not_base64", "!!!not-base64!!!"),
                ("base64_garbage", "bm90LWpzb24tYXQtYWxs"),
                ("empty_object", "e30"),
                ("bad_timestamp", "eyJ0Ijoibm90LWEtZGF0ZXRpbWUiLCJpIjoiMSJ9"),
            ):
                response = await client.get(AUDIT, params={"cursor": cursor})
                statuses[label] = response.status_code
        return statuses

    statuses = run_in_database(database_url, body)
    for label, code in statuses.items():
        assert code == 400, f"{label} gave {code}, expected 400 (a 500 means the error escaped)"


def test_inserts_between_pages_do_not_shift_the_window(database_url: str) -> None:
    """The keyset advantage, stated as a test.

    Offset pagination here would return the WRONG rows: inserting two rows at
    the top pushes everything down by two, so `OFFSET 2` re-reads the first page
    and the client sees two rows twice and silently misses two others. On an
    audit log that is "this action never happened", which is the one conclusion
    an audit log must never support.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_admin(session)
        await seed_logs(session, admin, 5, collide=False)
        async with Actor(session, admin) as client:
            first = (await client.get(AUDIT, params={"limit": 2})).json()
            page_one = _rows(first)
            cursor = first["meta"]["nextCursor"]

            # Two NEW rows, strictly newer than everything above.
            await seed_logs(session, admin, 2, collide=False)
            newer = datetime.now(UTC) - timedelta(days=1)
            for index in range(2):
                session.add(
                    AuditLog(
                        actor_user_id=admin.id,
                        actor_email=admin.email,
                        actor_role=admin.role,
                        action=f"cursor.late.{index}",
                        summary="inserted between pages",
                        target_type="TEST",
                        target_id=str(index),
                        changes={},
                        created_at=newer,
                    )
                )
            await session.commit()

            second = (await client.get(AUDIT, params={"limit": 2, "cursor": cursor})).json()
            return {"page_one": page_one, "page_two": _rows(second)}

    result = run_in_database(database_url, body)
    assert not set(result["page_one"]) & set(result["page_two"]), (
        f"a row appeared on both pages: {result}"
    )
    # The two rows that were on page 2 before the inserts are still page 2.
    # Under offset pagination these would have been pushed to page 3.
    assert len(result["page_two"]) == 2, result
