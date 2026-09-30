"""The entitlement lifecycle, end to end, against a live PostgreSQL.

WHY THIS FILE EXISTS

`app/services/entitlements.py` computes what an account may actually reach, and
the decision it makes is deliberately NOT the stored one:

    # EFFECTIVE, not stored. An expired PREMIUM row still says PREMIUM in the
    # database, and reporting that to a client renders a dashboard full of
    # unlocked features that every endpoint then refuses.

So `entitlement_for` reads the CLOCK. The stored `tier`/`status` pair is billing
history; `is_premium` is the answer to "what can this account do right now".

The existing content-library tests prove the gate closes for a PREMIUM document.
They do NOT prove the other two halves, and `test_postgres_content_library.py`
never inserts a `subscriptions` row at all - every one of those tests runs
against the FREE default. That leaves the most expensive class of bug in a
paid product completely untested:

    a lapsed subscriber keeps reading premium material.

This file walks one student through the whole cycle - nothing, then paid, then
lapsed - against the same PREMIUM document each time, so the document is
constant and only the subscription moves. That is the shape that catches the
bug: if the gate ever became "did you ever subscribe", all three states would
still look correct.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.main import app
from app.models.content import ContentDocument
from app.models.user import Subscription, User
from app.services.entitlements import entitlements_for, resolve_viewer

from ._db import run_in_database

pytestmark = pytest.mark.postgres


class Actor:
    """A signed-in caller sharing this test's session.

    Overrides `get_current_principal` so the real role dependency still runs, and
    re-reads the user row per request, matching production. Without the re-read a
    rolled-back transaction expires the instance and the next request raises
    MissingGreenlet - a harness artefact that looks exactly like a product bug.
    """

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


async def make_student(session: Any) -> User:
    user = User(
        auth_user_id=f"paywall-{uuid.uuid4().hex[:10]}",
        email=f"paywall-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Paywall Student",
        role="STUDENT",
    )
    session.add(user)
    await session.commit()
    return user


async def make_premium_document(session: Any) -> ContentDocument:
    """A PREMIUM-tier document, which is the content the gate is about."""
    document = ContentDocument(
        title=f"Premium {uuid.uuid4().hex[:8]}",
        kind="STUDY_MATERIAL",
        bucket="question-pdfs",
        storage_path=f"originals/2026/09/{uuid.uuid4().hex}/material.pdf",
        original_filename="material.pdf",
        size_bytes=1024,
        checksum_sha256=uuid.uuid4().hex * 2,
        # COMPLETED, not READY: ck_document_status does not contain READY, and the
        # database refusing the fixture is the constraint doing its job.
        status="COMPLETED",
        is_published=True,
        access_tier="PREMIUM",
        page_count=1,
        extracted_chars=100,
    )
    session.add(document)
    await session.commit()
    return document


async def subscribe(session: Any, user: User, *, expires_at: datetime | None) -> Subscription:
    """Create the subscription, or move its expiry if it already exists.

    `uq_active_subscription_per_user` allows exactly one ACTIVE subscription per
    user, so a lapse is modelled by moving `expires_at` on the SAME row rather
    than inserting a second one. That is also what a real lapse looks like: the
    row keeps saying tier=PREMIUM, status=ACTIVE, and only the clock has moved.
    A second row would have tested a state the schema forbids.
    """
    existing = (
        (
            await session.execute(
                select(Subscription)
                .where(Subscription.user_id == user.id)
                .order_by(Subscription.expires_at.desc().nullslast())
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        existing.expires_at = expires_at
        await session.commit()
        return existing

    subscription = Subscription(
        user_id=user.id,
        tier="PREMIUM",
        status="ACTIVE",
        started_at=datetime.now(UTC) - timedelta(days=1),
        expires_at=expires_at,
        auto_renew=False,
        provider="razorpay",
        amount_paise=99900,
        currency="INR",
    )
    session.add(subscription)
    await session.commit()
    return subscription


def test_the_gate_follows_the_clock_not_the_stored_tier(database_url: str) -> None:
    """No subscription, then a live one, then a lapsed one - same document throughout.

    The third state is the one that matters. The row still says
    tier=PREMIUM, status=ACTIVE, which is exactly what a naive `tier == "PREMIUM"`
    check would keep honouring forever.
    """

    async def body(session) -> dict[str, Any]:
        student = await make_student(session)
        document = await make_premium_document(session)

        async def snapshot() -> dict[str, Any]:
            # Re-read on a clean instance so the value comes from the row.
            await session.refresh(student)
            view = await resolve_viewer(session, student)
            # Viewer carries `tier`; it has no can_read flag. Access is decided by
            # a WHERE clause built from the viewer's tier, which is why the HTTP
            # test below asserts on what the response CONTAINS, not its status.
            return {"tier": str(view.tier), "is_staff": bool(view.is_staff)}

        no_subscription = await snapshot()

        await subscribe(session, student, expires_at=datetime.now(UTC) + timedelta(days=30))
        await session.refresh(student)
        live = await entitlements_for(session, student.id)
        await session.refresh(student)
        with_subscription = {
            "tier": str(live.tier),
            "is_premium": bool(live.is_premium),
        }

        # Lapse by CLOCK, not by status: the row is left exactly as a real
        # expired-but-not-yet-cleaned-up subscription looks.
        await subscribe(session, student, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        lapsed_row = (
            (
                await session.execute(
                    select(Subscription)
                    .where(Subscription.user_id == student.id)
                    .order_by(Subscription.expires_at.desc())
                )
            )
            .scalars()
            .first()
        )
        await session.refresh(student)
        after = await entitlements_for(session, student.id)
        after_tier = {"tier": str(after.tier), "is_premium": bool(after.is_premium)}

        return {
            "no_subscription": no_subscription,
            "with_subscription": with_subscription,
            "after": after_tier,
            # Proof the row was NOT edited to make the assertion pass: it still
            # claims PREMIUM/ACTIVE, and is refused anyway.
            "lapsed_row_still_says": (lapsed_row.tier, lapsed_row.status),
            "document_tier": document.access_tier,
        }

    result = run_in_database(database_url, body)

    assert result["no_subscription"]["tier"] == "FREE"
    assert result["with_subscription"]["is_premium"] is True
    assert result["with_subscription"]["tier"] == "PREMIUM"
    # The assertion that matters: unchanged premium row, expired clock, no access.
    assert result["after"]["is_premium"] is False
    assert result["after"]["tier"] == "FREE"
    assert result["lapsed_row_still_says"] == ("PREMIUM", "ACTIVE")
    assert result["document_tier"] == "PREMIUM"


def test_a_lapsed_subscriber_is_refused_the_premium_document(database_url: str) -> None:
    """The same conclusion through the library route, not just the service.

    Asserted at the HTTP boundary because that is where the two systems are
    actually joined: a service-level test would pass even if the route stopped
    consulting the viewer entirely.
    """

    async def body(session) -> dict[str, Any]:
        student = await make_student(session)
        document = await make_premium_document(session)

        async def listing() -> dict[str, Any]:
            async with Actor(session, student) as client:
                response = await client.get("/api/v1/content/library")
                body_json = response.json()
                ids: list[str] = []
                for key in ("items", "data", "results", "documents"):
                    if isinstance(body_json, dict) and isinstance(body_json.get(key), list):
                        ids = [str(i.get("id")) for i in body_json[key] if isinstance(i, dict)]
                        break
                return {"status": response.status_code, "ids": ids, "keys": sorted(body_json)[:6]}

        before = await listing()

        await subscribe(session, student, expires_at=datetime.now(UTC) + timedelta(days=30))
        after_paid = await listing()

        await subscribe(session, student, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        after_lapsed = await listing()

        return {
            "before": before,
            "after_paid": after_paid,
            "after_lapsed": after_lapsed,
            "tier": document.access_tier,
            "document_id": str(document.id),
        }

    result = run_in_database(database_url, body)

    assert result["tier"] == "PREMIUM"
    # The library answers 200 for every caller - the gate filters the LIST, it
    # does not refuse the request. Asserting only on status would pass even if the
    # premium filter had been deleted entirely.
    assert result["before"]["status"] == 200
    assert result["after_paid"]["status"] == 200
    assert result["after_lapsed"]["status"] == 200

    # THE ASSERTION THAT MATTERS: the same document, visible only while paid.
    assert result["document_id"] in result["after_paid"]["ids"], (
        "a paying subscriber must see the PREMIUM document: "
        f"ids={result['after_paid']['ids']} keys={result['after_paid']['keys']}"
    )
    assert result["document_id"] not in result["before"]["ids"], (
        "a FREE viewer must not see the PREMIUM document"
    )
    assert result["document_id"] not in result["after_lapsed"]["ids"], (
        "a LAPSED subscriber must lose access again; the row still says "
        "tier=PREMIUM status=ACTIVE, so only the clock can be hiding this"
    )
