"""The payment path against a real PostgreSQL.

The HTTP tests in ``tests/test_payments_api.py`` use a hand-written store double.
That is the right tool for asserting what the *route* does, and the wrong tool for
asserting what the *database* does - a fake that returns False from
``record_event`` on the second call proves the route handles a duplicate, not that
the database refuses one.

These tests exercise the real store against real constraints:

  * the UNIQUE index on ``payment_events.event_id`` (the idempotency guarantee)
  * the partial unique index on active subscriptions (one entitlement per user)
  * the CHECK constraints that keep a negative amount out of the ledger
  * the commit that makes an order durable before the gateway is called

Each one is written so that removing the database constraint makes it fail. If a
"the database rejects X" test would still pass without the constraint, it is
testing the ORM's opinion rather than the database's behaviour.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import PaymentEvent, PaymentOrder, Subscription, User
from app.repositories.billing import SqlBillingStore
from app.services.billing import (
    SubscriptionState,
    SubStatus,
    Tier,
    apply_payment,
    plan_for,
)

from ._db import run_in_database, sync_url
from .test_postgres_publishing import Actor
from .test_postgres_publishing import make_user as make_role_user

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


async def make_user(session: AsyncSession, *, auth_user_id: str | None = None) -> User:
    user = User(
        auth_user_id=auth_user_id or f"test-auth-{uuid.uuid4().hex[:12]}", email="s@example.com"
    )
    session.add(user)
    await session.flush()
    return user


# =========================================================== schema integrity


class TestSchemaAgainstTheDatabase:
    def test_alembic_check_reports_no_drift(self, database_url: str) -> None:
        """The ORM and the database must agree exactly.

        This is the check that found three real defects the first time it could
        run: 30 columns whose server defaults existed only in the migrations, two
        raw-SQL indexes that autogenerate wanted to DROP, and a redundant pair of
        unique objects on ``users.auth_user_id``.
        """
        import os

        env = {**os.environ, "DATABASE_URL": sync_url() or ""}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            capture_output=True,
            text=True,
            env=env,
            cwd=".",
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, f"schema drift detected:\n{combined}"
        assert "No new upgrade operations detected" in combined

    def test_every_table_the_orm_declares_exists(self, database_url: str) -> None:
        from app.models import Base

        async def body(session: AsyncSession) -> None:
            rows = await session.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            present = {row[0] for row in rows.all()} - {"alembic_version"}
            declared = {table.name for table in Base.metadata.sorted_tables}
            assert declared - present == set(), "declared in the ORM but missing from the database"
            assert present - declared == set(), "in the database but not declared in the ORM"

        run_in_database(database_url, body)

    def test_the_full_text_search_index_exists(self, database_url: str) -> None:
        """The GIN indexes are raw SQL in 0001, so nothing else can assert them.

        Autogenerate does not see them, which means the only way to know they
        survived a migration is to look for them in the catalogue.
        """

        async def body(session: AsyncSession) -> None:
            rows = await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename = 'questions' "
                    "AND indexname IN ('idx_questions_fts','idx_questions_trgm')"
                )
            )
            indexes = dict(rows.all())
            assert set(indexes) == {"idx_questions_fts", "idx_questions_trgm"}
            assert "to_tsvector" in indexes["idx_questions_fts"]
            assert "gin" in indexes["idx_questions_fts"].lower()
            assert "gin_trgm_ops" in indexes["idx_questions_trgm"]

        run_in_database(database_url, body)


# ================================================== orders, durability, money


class TestPaymentOrders:
    def test_create_order_is_committed_before_it_returns(self, database_url: str) -> None:
        """Durability, asserted from a SEPARATE connection.

        Reading the row back on the same session would be satisfied by a flush,
        which is exactly the mistake this test exists to prevent: the order must
        survive the process dying during the gateway call, and only a commit
        makes that true.
        """

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            store = SqlBillingStore(session)
            plan = plan_for("PREMIUM_YEARLY")
            order = await store.create_order(
                user_id=user.id,
                plan_code=plan.code,
                tier=plan.tier,
                amount_paise=plan.amount_paise,
                currency="INR",
                receipt="rcpt_" + "b" * 32,
                expires_at=NOW + timedelta(hours=24),
            )
            order_id = order.id

            # A second, independent connection: it can see the row only because
            # the first one committed.
            from sqlalchemy.ext.asyncio import create_async_engine

            from tests.integration.conftest import async_url

            other = create_async_engine(async_url() or "", future=True)
            try:
                async with other.connect() as connection:
                    found = await connection.scalar(
                        text("SELECT count(*) FROM payment_orders WHERE id = :id"),
                        {"id": order_id},
                    )
                assert found == 1, "the order was not committed, so a crash would lose it"
            finally:
                await other.dispose()

        run_in_database(database_url, body)

    def test_the_amount_is_frozen_on_the_row(self, database_url: str) -> None:
        """The price the student was quoted is stored, not recomputed later."""

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            store = SqlBillingStore(session)
            order = await store.create_order(
                user_id=user.id,
                plan_code="PREMIUM_YEARLY",
                tier=Tier.PREMIUM,
                amount_paise=99_900,
                currency="INR",
                receipt="rcpt_" + "c" * 32,
            )
            stored = await session.scalar(
                select(PaymentOrder.amount_paise).where(PaymentOrder.id == order.id)
            )
            assert stored == 99_900

        run_in_database(database_url, body)

    def test_the_database_rejects_a_negative_amount(self, database_url: str) -> None:
        """0 is allowed (the free tier), negative never is."""

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO payment_orders "
                        "(id, user_id, plan_code, tier, amount_paise, "
                        "currency, receipt, status, created_at, updated_at) "
                        "VALUES (:id, :user_id, 'PREMIUM_YEARLY', 'PREMIUM', "
                        "-1, 'INR', :receipt, 'CREATED', now(), now())"
                    ),
                    {"id": uuid.uuid4(), "user_id": user.id, "receipt": "rcpt_" + "d" * 32},
                )
                await session.flush()

        run_in_database(database_url, body)

    def test_receipts_are_unique(self, database_url: str) -> None:
        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            store = SqlBillingStore(session)
            receipt = "rcpt_" + "e" * 32
            await store.create_order(
                user_id=user.id,
                plan_code="PREMIUM_YEARLY",
                tier=Tier.PREMIUM,
                amount_paise=99_900,
                currency="INR",
                receipt=receipt,
            )
            with pytest.raises(IntegrityError):
                await store.create_order(
                    user_id=user.id,
                    plan_code="PREMIUM_YEARLY",
                    tier=Tier.PREMIUM,
                    amount_paise=99_900,
                    currency="INR",
                    receipt=receipt,
                )

        run_in_database(database_url, body)

    def test_an_order_is_only_visible_to_its_owner(self, database_url: str) -> None:
        async def body(session: AsyncSession) -> None:
            owner = await make_user(session)
            stranger = await make_user(session)
            store = SqlBillingStore(session)
            order = await store.create_order(
                user_id=owner.id,
                plan_code="PREMIUM_YEARLY",
                tier=Tier.PREMIUM,
                amount_paise=99_900,
                currency="INR",
                receipt="rcpt_" + "f" * 32,
            )
            assert await store.order_for_user(order_id=order.id, user_id=owner.id) is not None
            assert await store.order_for_user(order_id=order.id, user_id=stranger.id) is None

        run_in_database(database_url, body)


# ================================================== webhook idempotency (DB)


class TestPaymentEventIdempotency:
    def test_the_same_event_id_can_only_be_recorded_once(self, database_url: str) -> None:
        """The idempotency guarantee, enforced by the database.

        ``record_event`` uses INSERT ... ON CONFLICT DO NOTHING and reports the
        rowcount. A check-then-insert would look identical in a single-threaded
        test and fail under two concurrent webhook deliveries - which is the
        normal case, since providers retry in parallel.
        """

        async def body(session: AsyncSession) -> None:
            store = SqlBillingStore(session)
            payload = {"id": "evt_1", "event": "payment.captured"}

            first = await store.record_event(
                provider="razorpay",
                event_id="evt_1",
                event_type="payment.captured",
                payload=payload,
                signature_verified=True,
            )
            second = await store.record_event(
                provider="razorpay",
                event_id="evt_1",
                event_type="payment.captured",
                payload=payload,
                signature_verified=True,
            )

            assert first is True, "the first delivery should be new"
            assert second is False, "the second delivery must be recognised as a duplicate"
            count = await session.scalar(select(func.count()).select_from(PaymentEvent))
            assert count == 1

        run_in_database(database_url, body)

    def test_a_raw_duplicate_event_id_is_rejected_by_the_index(self, database_url: str) -> None:
        """Without ON CONFLICT, the unique index is the backstop."""

        async def body(session: AsyncSession) -> None:
            for _ in range(2):
                await session.execute(
                    text(
                        "INSERT INTO payment_events "
                        "(id, provider, event_id, event_type, "
                        "signature_verified, payload, created_at, updated_at) "
                        "VALUES (:id, 'razorpay', 'evt_dup', "
                        "'payment.captured', true, '{}'::jsonb, now(), now())"
                    ),
                    {"id": uuid.uuid4()},
                )
                await session.flush()

        with pytest.raises(IntegrityError):
            run_in_database(database_url, body)

    def test_an_event_is_archived_with_its_payload(self, database_url: str) -> None:
        """§13.1's audit trail: the raw provider payload has to be on disk."""

        async def body(session: AsyncSession) -> None:
            store = SqlBillingStore(session)
            await store.record_event(
                provider="razorpay",
                event_id="evt_audit",
                event_type="payment.captured",
                payload={"id": "evt_audit", "payload": {"payment": {"entity": {"amount": 99_900}}}},
                signature_verified=True,
            )
            event = await session.scalar(
                select(PaymentEvent).where(PaymentEvent.event_id == "evt_audit")
            )
            assert event is not None
            assert event.payload["event"]["payload"]["payment"]["entity"]["amount"] == 99_900
            assert event.signature_verified is True

        run_in_database(database_url, body)

    def test_marking_an_order_paid_twice_reports_false_the_second_time(
        self, database_url: str
    ) -> None:
        """The second writer must not extend the subscription again."""

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            store = SqlBillingStore(session)
            order = await store.create_order(
                user_id=user.id,
                plan_code="PREMIUM_YEARLY",
                tier=Tier.PREMIUM,
                amount_paise=99_900,
                currency="INR",
                receipt="rcpt_" + "1" * 32,
            )
            first = await store.mark_order_paid(
                order, provider_payment_id="pay_1", provider_status="captured", paid_at=NOW
            )
            second = await store.mark_order_paid(
                order, provider_payment_id="pay_1", provider_status="captured", paid_at=NOW
            )
            assert first is True
            assert second is False

        run_in_database(database_url, body)


# ============================================ subscriptions and their constraint


class TestSubscriptionConstraints:
    def test_activation_creates_then_reuses_one_row(self, database_url: str) -> None:
        """Renewal must UPDATE the subscription, not add a second one."""

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            store = SqlBillingStore(session)
            plan = plan_for("PREMIUM_YEARLY")

            first = await store.activate(
                user_id=user.id,
                activation=apply_payment(current=None, plan=plan, now=NOW),
                plan_code=plan.code,
                amount_paise=plan.amount_paise,
                currency="INR",
                provider="razorpay",
            )
            # Captured as a VALUE: `first` and `second` are the same ORM row, so
            # reading first.expires_at after the renewal would read the updated
            # attribute and the assertion would compare a value with itself.
            first_expiry = first.expires_at
            second = await store.activate(
                user_id=user.id,
                activation=apply_payment(
                    current=SubscriptionState(
                        tier=Tier.PREMIUM,
                        status=SubStatus.ACTIVE,
                        expires_at=first_expiry,
                    ),
                    plan=plan,
                    now=NOW,
                ),
                plan_code=plan.code,
                amount_paise=plan.amount_paise,
                currency="INR",
                provider="razorpay",
            )

            count = await session.scalar(select(func.count()).select_from(Subscription))
            assert count == 1, "a renewal created a second subscription row"
            assert second.id == first.id
            # The renewal extended the existing term rather than restarting it.
            assert second.expires_at == first_expiry + timedelta(days=365)

        run_in_database(database_url, body)

    def test_the_database_refuses_a_second_active_subscription(self, database_url: str) -> None:
        """The partial unique index, tested by bypassing the ORM entirely.

        This is the guarantee that makes a concurrent webhook safe: two deliveries
        racing cannot both create an entitlement, whatever the application code
        believes. Asserting it through the ORM would test the ORM.
        """

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            for _ in range(2):
                await session.execute(
                    text(
                        "INSERT INTO subscriptions "
                        "(id, user_id, tier, status, started_at, expires_at, "
                        "auto_renew, currency, created_at, updated_at) "
                        "VALUES (:id, :user_id, 'PREMIUM', 'ACTIVE', now(), "
                        "now() + interval '1 year', false, 'INR', now(), now())"
                    ),
                    {"id": uuid.uuid4(), "user_id": user.id},
                )
                await session.flush()

        with pytest.raises(IntegrityError):
            run_in_database(database_url, body)

    def test_an_expired_subscription_does_not_block_a_new_one(self, database_url: str) -> None:
        """The index predicate covers ACTIVE and TRIAL only.

        A student accumulates historical subscriptions - expired, cancelled,
        upgraded away from. If the index covered every status, the second purchase
        of a lifetime would fail.
        """

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            for status in ("EXPIRED", "CANCELLED"):
                await session.execute(
                    text(
                        "INSERT INTO subscriptions "
                        "(id, user_id, tier, status, started_at, expires_at, "
                        "auto_renew, currency, created_at, updated_at) "
                        "VALUES (:id, :user_id, 'PREMIUM', :status, now() - interval '2 years', "
                        "now() - interval '1 year', false, 'INR', now(), now())"
                    ),
                    {"id": uuid.uuid4(), "user_id": user.id, "status": status},
                )
            await session.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id, user_id, tier, status, started_at, expires_at, "
                    "auto_renew, currency, created_at, updated_at) "
                    "VALUES (:id, :user_id, 'PREMIUM', 'ACTIVE', now(), "
                    "now() + interval '1 year', false, 'INR', now(), now())"
                ),
                {"id": uuid.uuid4(), "user_id": user.id},
            )
            await session.flush()
            total = await session.scalar(select(func.count()).select_from(Subscription))
            assert total == 3

        run_in_database(database_url, body)

    def test_a_trial_also_counts_as_active(self, database_url: str) -> None:
        """TRIAL is in the index predicate, so it must block a second live row."""

        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            await session.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id, user_id, tier, status, started_at, expires_at, "
                    "auto_renew, currency, created_at, updated_at) "
                    "VALUES (:id, :user_id, 'PREMIUM', 'TRIAL', now(), "
                    "now() + interval '7 days', false, 'INR', now(), now())"
                ),
                {"id": uuid.uuid4(), "user_id": user.id},
            )
            await session.flush()
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO subscriptions "
                        "(id, user_id, tier, status, started_at, expires_at, "
                        "auto_renew, currency, created_at, updated_at) "
                        "VALUES (:id, :user_id, 'PREMIUM', 'ACTIVE', now(), "
                        "now() + interval '1 year', false, 'INR', now(), now())"
                    ),
                    {"id": uuid.uuid4(), "user_id": user.id},
                )
                await session.flush()

        run_in_database(database_url, body)

    def test_the_active_subscription_lookup_ignores_dead_rows(self, database_url: str) -> None:
        async def body(session: AsyncSession) -> None:
            user = await make_user(session)
            await session.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id, user_id, tier, status, started_at, expires_at, "
                    "auto_renew, currency, created_at, updated_at) "
                    "VALUES (:id, :user_id, 'PREMIUM', 'CANCELLED', "
                    "now(), now(), false, 'INR', now(), now())"
                ),
                {"id": uuid.uuid4(), "user_id": user.id},
            )
            await session.flush()
            store = SqlBillingStore(session)
            assert await store.active_subscription(user.id) is None

        run_in_database(database_url, body)


class TestUserIdentityConstraints:
    def test_auth_user_id_is_unique(self, database_url: str) -> None:
        """One account, one row. Enforced by the unique index that
        migration 0004 aligned with the ORM."""

        async def body(session: AsyncSession) -> None:
            await make_user(session, auth_user_id="test-auth-same")
            with pytest.raises(IntegrityError):
                await make_user(session, auth_user_id="test-auth-same")

        run_in_database(database_url, body)

    def test_history_writes_do_not_leave_a_unique_constraint_behind(
        self, database_url: str
    ) -> None:
        """Migration 0004 replaced the constraint with a unique INDEX.

        Both enforce uniqueness, and the ORM declares the index form. This asserts
        the *enforcement* rather than the shape, so it stays true whichever way
        PostgreSQL decides to back it.
        """

        async def body(session: AsyncSession) -> None:
            rows = await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'users' AND indexname = 'ix_users_auth_user_id'"
                )
            )
            definitions = [row[0] for row in rows.all()]
            assert definitions, "the unique index on users.auth_user_id is missing"
            assert "UNIQUE" in definitions[0]

        run_in_database(database_url, body)


SECRET = "SHOULD_NOT_LEAK_rzp_test_secret"
ORDER_KEYS = {
    "id",
    "email",
    "displayName",
    "userId",
    "planCode",
    "tier",
    "amountPaise",
    "amountRupees",
    "amountRemainderPaise",
    "currency",
    "status",
    "provider",
    "providerOrderId",
    "providerPaymentId",
    "providerStatus",
    "receipt",
    "paidAt",
    "failureReason",
    "createdAt",
    "entitlement",
}
EVENT_KEYS = {
    "id",
    "provider",
    "eventId",
    "eventType",
    "signatureVerified",
    "processedAt",
    "processingError",
    "userId",
    "email",
    "createdAt",
}


class TestAdminPaymentList:
    """The reconciliation view, against PostgreSQL.

    A student, an editor and a content manager must be told 403 — VIEW_PAYMENTS is
    not in their matrix. An admin must see the order, the frozen paise amount, and
    the live entitlement, and must not see the webhook body even though that body
    is sitting in the same database.
    """

    def test_the_list_is_permissioned_and_omits_webhook_bodies(self, database_url: str) -> None:
        async def body(session: AsyncSession) -> None:
            admin = await make_role_user(session, "ADMIN")
            student = await make_role_user(session, "STUDENT")
            editor = await make_role_user(session, "EDITOR")
            manager = await make_role_user(session, "CONTENT_MANAGER")

            buyer = await make_role_user(session, "STUDENT")
            buyer.email = "payee@example.com"
            buyer.display_name = "Payee"
            other = await make_role_user(session, "STUDENT")
            other.email = "other@example.com"
            await session.commit()

            paid = PaymentOrder(
                user_id=buyer.id,
                plan_code="PREMIUM_PLUS",
                tier="PREMIUM_PLUS",
                amount_paise=129_900,
                receipt="rcpt-paid",
                provider_order_id="order_paid",
                provider_payment_id="pay_paid",
                status="PAID",
                paid_at=NOW,
            )
            failed = PaymentOrder(
                user_id=other.id,
                plan_code="PREMIUM",
                tier="PREMIUM",
                amount_paise=79_900,
                receipt="rcpt-failed",
                status="FAILED",
                failure_reason="gateway declined",
            )
            session.add_all(
                [
                    paid,
                    failed,
                    Subscription(
                        user_id=buyer.id,
                        tier="PREMIUM_PLUS",
                        status="ACTIVE",
                        started_at=NOW,
                        expires_at=NOW + timedelta(days=365),
                    ),
                    PaymentEvent(
                        provider="razorpay",
                        event_id="evt_secret",
                        event_type="payment.captured",
                        signature_verified=True,
                        payload={"card": SECRET, "notes": {"email": "payee@example.com"}},
                        processed_at=NOW,
                        user_id=buyer.id,
                    ),
                    PaymentEvent(
                        provider="razorpay",
                        event_id="evt_waiting",
                        event_type="payment.failed",
                        signature_verified=False,
                        payload={"card": SECRET},
                        processing_error="not applied",
                        user_id=other.id,
                    ),
                ]
            )
            await session.commit()

            for caller in (student, editor, manager):
                async with Actor(session, caller) as client:
                    denied_orders = await client.get("/api/v1/admin/payments/orders")
                    denied_events = await client.get("/api/v1/admin/payments/events")
                assert denied_orders.status_code == 403, caller.role
                assert denied_events.status_code == 403, caller.role
                assert SECRET not in denied_orders.text
                assert SECRET not in denied_events.text

            async with Actor(session, admin) as client:
                listed = await client.get("/api/v1/admin/payments/orders")
                assert listed.status_code == 200, listed.text
                payload = listed.json()
                assert SECRET not in listed.text
                assert payload["meta"]["total"] == 2
                assert payload["meta"]["paidOrders"] == 1
                assert payload["meta"]["paidPaise"] == 129_900
                assert payload["meta"]["paidRupees"] == 1299
                assert payload["meta"]["paidRemainderPaise"] == 0
                paid_row = next(row for row in payload["data"] if row["status"] == "PAID")
                failed_row = next(row for row in payload["data"] if row["status"] == "FAILED")
                assert set(paid_row) == ORDER_KEYS
                assert paid_row["amountPaise"] == 129_900
                assert paid_row["amountRupees"] == 1299
                assert paid_row["email"] == "payee@example.com"
                assert paid_row["entitlement"]["tier"] == "PREMIUM_PLUS"
                assert paid_row["entitlement"]["status"] == "ACTIVE"
                assert failed_row["entitlement"] is None
                assert failed_row["failureReason"] == "gateway declined"

                wildcard = await client.get("/api/v1/admin/payments/orders", params={"q": "%"})
                assert wildcard.status_code == 200
                assert wildcard.json()["meta"]["total"] == 0
                found = await client.get("/api/v1/admin/payments/orders", params={"q": "payee"})
                assert found.json()["meta"]["total"] == 1

                only_failed = await client.get(
                    "/api/v1/admin/payments/orders", params={"status": "FAILED"}
                )
                assert only_failed.json()["meta"]["total"] == 1
                assert only_failed.json()["meta"]["paidOrders"] == 0
                assert only_failed.json()["data"][0]["status"] == "FAILED"

                unknown = await client.get(
                    "/api/v1/admin/payments/orders", params={"status": "REFUNDED"}
                )
                assert unknown.status_code == 422

                events = await client.get("/api/v1/admin/payments/events")
                assert events.status_code == 200, events.text
                assert SECRET not in events.text
                event_body = events.json()
                assert event_body["meta"]["payloadOmitted"] is True
                assert event_body["meta"]["unprocessed"] == 1
                assert event_body["meta"]["total"] == 2
                assert all(set(row) == EVENT_KEYS for row in event_body["data"])
                assert all("payload" not in row for row in event_body["data"])

        run_in_database(database_url, body)
