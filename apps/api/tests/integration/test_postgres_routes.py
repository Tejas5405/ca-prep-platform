"""The payment routes driven over HTTP against a live PostgreSQL.

WHY THIS FILE EXISTS, GIVEN ``test_postgres_payments.py`` ALREADY PASSES

That file tests the database. This one tests the path a request actually takes:
HTTP body -> route -> ``SqlBillingStore`` -> real SQL -> commit -> response. The
unit suite covers the same routes against a hand-written store double, which is a
test of the double: every ``select()``, every ``on_conflict_do_nothing``, and every
column name in the repository is unverified by it. A store double cannot fail on a
column that does not exist.

WHAT IS FAKED, AND WHY ONLY THAT

``RazorpayClient`` is replaced. It is a network boundary, and the three things that
matter about it - the amount it is asked for, what it returns, and whether it is
called at all - are recorded and asserted. Everything else is real: the app, its
dependency wiring, the session, the store, the database, the constraints.

THE SIGNATURE IS COMPUTED HERE, NOT STUBBED

``verify_webhook_signature`` and ``verify_checkout_signature`` are production code
on the critical path; replacing them would leave the one thing an attacker targets
untested. The tests therefore sign bodies themselves with the same HMAC the
provider uses, and the rejection cases sign with the wrong secret or tamper with a
signed byte.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.api.v1 import payments as payments_route
from app.core.config import Settings
from app.core.dependencies import get_db
from app.integrations.razorpay import GatewayOrder, GatewayPayment, RazorpayError
from app.main import app
from app.models.user import PaymentEvent, PaymentOrder, Subscription, User
from app.repositories.billing import SqlBillingStore
from app.services.billing import Tier, apply_payment, plan_for

from ._db import observe, run_in_database, sync_url

pytestmark = pytest.mark.postgres

#: Both are wrong on purpose and neither is a real credential: the point is that
#: verification is exercised, so the tests must be able to sign.
WEBHOOK_SECRET = "whsec_test_webhook_secret"
KEY_SECRET = "rzp_test_key_secret"
KEY_ID = "rzp_test_key_id"
SIGNATURE_HEADER = "X-Razorpay-Signature"

PREMIUM = plan_for("PREMIUM_YEARLY")
YEAR = timedelta(days=365)


# --------------------------------------------------------------------------- setup


def payment_settings() -> Settings:
    """Settings with the payment block populated.

    Built explicitly rather than read from the environment: the suite must not
    depend on a developer's ``.env``, and if the keys were absent the whole file
    would silently test the 503 path instead of the flow.
    """
    return Settings(
        _env_file=None,
        razorpay_key_id=KEY_ID,
        razorpay_key_secret=KEY_SECRET,
        razorpay_webhook_secret=WEBHOOK_SECRET,
    )


class FakeGateway:
    """Stands in for ``RazorpayClient``. Records calls; returns what it is given."""

    def __init__(
        self,
        *,
        order: GatewayOrder | None = None,
        payment: GatewayPayment | None = None,
        create_error: Exception | None = None,
        fetch_error: Exception | None = None,
        probe: Any = None,
    ) -> None:
        self.order = order
        self.payment = payment
        self.create_error = create_error
        self.fetch_error = fetch_error
        #: An async callable run while ``create_order`` is in flight, on its own
        #: connection, so a test can see the database as it is DURING the network
        #: call. This is the only vantage point from which "the row was committed
        #: before we called the gateway" is observable: by the time the request
        #: returns, a later commit in the route has written it anyway.
        self.probe = probe
        self.at_call: Any = None
        self.created: list[dict[str, Any]] = []
        self.fetched: list[str] = []

    @property
    def key_id(self) -> str:
        return KEY_ID

    async def __aenter__(self) -> FakeGateway:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def create_order(self, **kwargs: Any) -> GatewayOrder:
        # Recorded BEFORE the error is raised: a call that fails still happened, and
        # "the gateway was never called" is an assertion some tests make.
        self.created.append(kwargs)
        if self.probe is not None:
            self.at_call = await self.probe()
        if self.create_error is not None:
            raise self.create_error
        assert self.order is not None, "test did not configure a gateway order"
        return self.order

    async def fetch_payment(self, payment_id: str) -> GatewayPayment:
        self.fetched.append(payment_id)
        if self.fetch_error is not None:
            raise self.fetch_error
        assert self.payment is not None, "test did not configure a gateway payment"
        return self.payment


def gateway_order(order_id: str = "order_TESTGATEWAY1") -> GatewayOrder:
    return GatewayOrder(
        id=order_id,
        amount_paise=PREMIUM.amount_paise,
        currency="INR",
        receipt="receipt_test",
        status="created",
        raw={},
    )


def gateway_payment(
    *,
    payment_id: str = "pay_TESTGATEWAY1",
    order_id: str = "order_TESTGATEWAY1",
    amount_paise: int = PREMIUM.amount_paise,
    currency: str = "INR",
    status: str = "captured",
    captured: bool = True,
) -> GatewayPayment:
    return GatewayPayment(
        id=payment_id,
        order_id=order_id,
        amount_paise=amount_paise,
        currency=currency,
        status=status,
        captured=captured,
        method="card",
        email="student@example.com",
        raw={},
    )


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """The provider's signature scheme: HMAC-SHA256 over the exact bytes, hex."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def checkout_signature(provider_order_id: str, payment_id: str) -> str:
    """What Razorpay Checkout signs: ``order_id|payment_id`` under the key secret."""
    return sign(f"{provider_order_id}|{payment_id}".encode(), KEY_SECRET)


def webhook_body(
    *,
    event_id: str = "evt_TEST1",
    event_type: str = "payment.captured",
    payment_id: str = "pay_TESTGATEWAY1",
    provider_order_id: str = "order_TESTGATEWAY1",
    amount_paise: int = PREMIUM.amount_paise,
    status: str = "captured",
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "event": event_type,
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": provider_order_id,
                        "amount": amount_paise,
                        "currency": "INR",
                        "status": status,
                        "captured": status == "captured",
                        "email": "student@example.com",
                    }
                }
            },
        }
    ).encode()


async def make_user(session: Any, auth_user_id: str) -> User:
    user = User(
        auth_user_id=auth_user_id,
        email=f"{auth_user_id}@example.com",
        display_name="Integration Student",
    )
    session.add(user)
    await session.commit()
    return user


async def make_order(
    session: Any,
    *,
    user_id: uuid.UUID,
    provider_order_id: str = "order_TESTGATEWAY1",
    amount_paise: int = PREMIUM.amount_paise,
    status: str = "CREATED",
) -> PaymentOrder:
    order = PaymentOrder(
        user_id=user_id,
        plan_code=PREMIUM.code,
        tier=PREMIUM.tier.value,
        amount_paise=amount_paise,
        currency="INR",
        receipt=f"rcpt_{uuid.uuid4().hex[:16]}",
        status=status,
        provider_order_id=provider_order_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    session.add(order)
    await session.commit()
    return order


class Client:
    """An HTTP client for the app that has not started a lifespan.

    ``ASGITransport`` rather than ``TestClient``: TestClient runs the app in its own
    thread with its own event loop, and the session under test belongs to this
    loop. Sharing one would reintroduce the cross-loop failure the harness exists
    to avoid.
    """

    def __init__(self, session: Any, user_id: uuid.UUID | None, settings: Settings) -> None:
        self._session = session
        self._user_id = user_id
        self._settings = settings

    async def __aenter__(self) -> httpx.AsyncClient:
        app.dependency_overrides[get_db] = lambda: self._session
        app.dependency_overrides[payments_route.get_billing_user] = lambda: self._user_id
        app.dependency_overrides[payments_route.get_settings] = lambda: self._settings
        transport = httpx.ASGITransport(app=app)
        self._client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        return self._client

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()
        # Cleared unconditionally: FastAPI's override registry is module-global, so a
        # leaked override would silently redirect another test's database session.
        app.dependency_overrides.clear()


@pytest.fixture
def use_gateway(monkeypatch: pytest.MonkeyPatch):
    """Install a fake gateway and hand it to the test."""

    def _install(gateway: FakeGateway) -> FakeGateway:
        monkeypatch.setattr(
            payments_route,
            "RazorpayClient",
            lambda *args, **kwargs: gateway,
        )
        return gateway

    return _install


async def scalar(url: str, stmt: Any) -> Any:
    """One value, read on a connection that did not write it."""
    async with observe(url) as session:
        return (await session.execute(stmt)).scalar_one()


async def rows(url: str, stmt: Any) -> list[Any]:
    async with observe(url) as session:
        return list((await session.execute(stmt)).scalars().all())


def all_orders() -> Any:
    return select(PaymentOrder).order_by(PaymentOrder.created_at)


def all_subscriptions() -> Any:
    return select(Subscription).order_by(Subscription.created_at)


# ------------------------------------------------------------------ creating an order


class TestCreateOrderRoute:
    def test_the_route_prices_the_plan_persists_the_order_and_returns_the_ids(
        self, database_url: str, use_gateway: Any
    ) -> None:
        gateway = use_gateway(FakeGateway(order=gateway_order()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-create-1")
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(
                    "/api/v1/payments/order", json={"plan_code": PREMIUM.code}
                )

            assert response.status_code == 201, response.text
            data = response.json()["data"]
            assert data["amountPaise"] == 99_900
            assert data["currency"] == "INR"
            assert data["providerKeyId"] == KEY_ID
            assert data["providerOrderId"] == "order_TESTGATEWAY1"

            # Read back on a second connection: the row must exist in the database,
            # not merely in the session that wrote it.
            stored = await rows(database_url, all_orders())
            assert len(stored) == 1
            order = stored[0]
            assert str(order.id) == data["orderId"]
            assert order.user_id == user.id
            assert order.amount_paise == 99_900
            assert order.status == "CREATED"
            assert order.provider_order_id == "order_TESTGATEWAY1"
            assert order.receipt == data["receipt"]

        run_in_database(database_url, body)
        assert gateway.created, "the gateway order was never created"

    def test_the_gateway_is_asked_for_the_server_side_price_only(
        self, database_url: str, use_gateway: Any
    ) -> None:
        gateway = use_gateway(FakeGateway(order=gateway_order()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-create-2")
            async with Client(session, user.id, payment_settings()) as client:
                await client.post("/api/v1/payments/order", json={"plan_code": PREMIUM.code})

        run_in_database(database_url, body)
        sent = gateway.created[0]
        assert sent["amount_paise"] == 99_900
        assert sent["currency"] == "INR"
        assert sent["notes"]["planCode"] == PREMIUM.code

    def test_a_client_supplied_price_is_rejected_by_the_schema(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """The field does not exist, so ``extra="forbid"`` refuses the request."""
        gateway = use_gateway(FakeGateway(order=gateway_order()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-create-3")
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(
                    "/api/v1/payments/order",
                    json={"plan_code": PREMIUM.code, "amount_paise": 1},
                )
            assert response.status_code == 422
            assert await scalar(database_url, select(func.count()).select_from(PaymentOrder)) == 0

        run_in_database(database_url, body)
        assert gateway.created == []

    def test_an_unknown_plan_creates_no_order_and_never_reaches_the_gateway(
        self, database_url: str, use_gateway: Any
    ) -> None:
        gateway = use_gateway(FakeGateway(order=gateway_order()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-create-4")
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(
                    "/api/v1/payments/order", json={"plan_code": "FREE_FOREVER"}
                )
            assert response.status_code == 422
            assert await scalar(database_url, select(func.count()).select_from(PaymentOrder)) == 0

        run_in_database(database_url, body)
        assert gateway.created == []

    def test_the_order_row_is_already_committed_when_the_gateway_is_called(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """The property the write-before-call ordering exists for.

        If the process dies during the gateway call, Razorpay has a real order and
        this service must already have a row naming its owner. A route that wrote
        the row last would still pass every test that reads the database after the
        response - the row would be there by then, written by the route's own later
        commit - so the assertion has to be made from inside the call.
        """

        async def probe() -> list[PaymentOrder]:
            return await rows(database_url, all_orders())

        gateway = use_gateway(FakeGateway(order=gateway_order(), probe=probe))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-create-probe")
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(
                    "/api/v1/payments/order", json={"plan_code": PREMIUM.code}
                )
            assert response.status_code == 201, response.text

        run_in_database(database_url, body)
        # A SEPARATE connection read this while the gateway call was in flight, so
        # it can only have seen committed data.
        assert gateway.at_call is not None, "the probe never ran"
        assert len(gateway.at_call) == 1, "no order row was visible mid-call"
        assert gateway.at_call[0].status == "CREATED"
        assert gateway.at_call[0].amount_paise == 99_900

    def test_a_gateway_failure_leaves_a_failed_row_because_the_row_came_first(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(create_error=RazorpayError("gateway exploded")))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-create-5")
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(
                    "/api/v1/payments/order", json={"plan_code": PREMIUM.code}
                )
            assert response.status_code == 502
            stored = await rows(database_url, all_orders())
            assert len(stored) == 1
            assert stored[0].status == "FAILED"
            assert stored[0].failure_reason
            assert stored[0].provider_order_id is None

        run_in_database(database_url, body)

    def test_without_keys_the_route_is_unavailable_and_writes_nothing(
        self, database_url: str, use_gateway: Any
    ) -> None:
        gateway = use_gateway(FakeGateway(order=gateway_order()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-create-6")
            settings = Settings(_env_file=None)  # no razorpay keys at all
            assert settings.payments_enabled() is False
            async with Client(session, user.id, settings) as client:
                response = await client.post(
                    "/api/v1/payments/order", json={"plan_code": PREMIUM.code}
                )
            assert response.status_code == 503
            assert await scalar(database_url, select(func.count()).select_from(PaymentOrder)) == 0

        run_in_database(database_url, body)
        assert gateway.created == []

    def test_an_unauthenticated_caller_creates_nothing(
        self, database_url: str, use_gateway: Any
    ) -> None:
        gateway = use_gateway(FakeGateway(order=gateway_order()))

        async def body(session: Any) -> None:
            async with Client(session, None, payment_settings()) as client:
                response = await client.post(
                    "/api/v1/payments/order", json={"plan_code": PREMIUM.code}
                )
            # 404 rather than 401: the principal resolved, but no local account is
            # linked to it yet, and an order must belong to a row that exists.
            assert response.status_code in {401, 404}
            assert await scalar(database_url, select(func.count()).select_from(PaymentOrder)) == 0

        run_in_database(database_url, body)
        assert gateway.created == []


# ------------------------------------------------------------- browser confirmation


class TestConfirmRoute:
    def _path(self) -> str:
        return "/api/v1/payments/confirm"

    def test_a_correctly_signed_confirmation_activates_the_subscription(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-confirm-1")
            order = await make_order(session, user_id=user.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                "razorpay_signature": checkout_signature("order_TESTGATEWAY1", "pay_TESTGATEWAY1"),
            }
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(self._path(), json=payload)

            assert response.status_code == 200, response.text
            stored = await rows(database_url, all_subscriptions())
            assert len(stored) == 1
            subscription = stored[0]
            assert subscription.user_id == user.id
            assert subscription.status == "ACTIVE"
            assert subscription.tier == "PREMIUM"
            assert subscription.provider == "razorpay"
            assert subscription.amount_paise == 99_900
            assert abs((subscription.expires_at - datetime.now(UTC)) - YEAR) < timedelta(minutes=5)

            order_row = (await rows(database_url, all_orders()))[0]
            assert order_row.status == "PAID"
            assert order_row.provider_payment_id == "pay_TESTGATEWAY1"
            assert order_row.paid_at is not None

        run_in_database(database_url, body)

    def test_a_confirm_for_another_users_order_is_a_404_and_activates_nothing(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            owner = await make_user(session, "test-auth-confirm-owner")
            attacker = await make_user(session, "test-auth-confirm-attacker")
            order = await make_order(session, user_id=owner.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                "razorpay_signature": checkout_signature("order_TESTGATEWAY1", "pay_TESTGATEWAY1"),
            }
            async with Client(session, attacker.id, payment_settings()) as client:
                response = await client.post(self._path(), json=payload)

            assert response.status_code == 404
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0
            assert (await rows(database_url, all_orders()))[0].status == "CREATED"

        run_in_database(database_url, body)

    def test_a_tampered_amount_read_back_from_the_gateway_is_refused(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """The signed callback can name a real payment; the amount still decides."""
        use_gateway(FakeGateway(payment=gateway_payment(amount_paise=1)))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-confirm-2")
            order = await make_order(session, user_id=user.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                "razorpay_signature": checkout_signature("order_TESTGATEWAY1", "pay_TESTGATEWAY1"),
            }
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(self._path(), json=payload)

            assert response.status_code == 409, response.text
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0
            stored = (await rows(database_url, all_orders()))[0]
            assert stored.status == "FAILED"
            # The rejected event is still archived, so support can answer "the money
            # left my account": the payload is on file with the reason.
            events = (await rows(database_url, select(PaymentEvent)))[0]
            assert events.processing_error is not None

        run_in_database(database_url, body)

    def test_a_payment_for_a_different_order_is_refused(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment(order_id="order_SOMEONE_ELSE")))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-confirm-3")
            order = await make_order(session, user_id=user.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                "razorpay_signature": checkout_signature("order_TESTGATEWAY1", "pay_TESTGATEWAY1"),
            }
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(self._path(), json=payload)

            assert response.status_code == 409
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0

        run_in_database(database_url, body)

    def test_an_uncaptured_payment_does_not_grant_entitlements(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment(status="authorized", captured=False)))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-confirm-4")
            order = await make_order(session, user_id=user.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                "razorpay_signature": checkout_signature("order_TESTGATEWAY1", "pay_TESTGATEWAY1"),
            }
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(self._path(), json=payload)

            assert response.status_code == 409
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0

        run_in_database(database_url, body)

    def test_a_wrong_signature_never_reaches_the_gateway(
        self, database_url: str, use_gateway: Any
    ) -> None:
        gateway = use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-confirm-5")
            order = await make_order(session, user_id=user.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                # Signed with the wrong secret - i.e. what an attacker can produce.
                "razorpay_signature": sign(
                    b"order_TESTGATEWAY1|pay_TESTGATEWAY1", "not_the_secret"
                ),
            }
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(self._path(), json=payload)

            assert response.status_code == 400
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0
            assert (await rows(database_url, all_orders()))[0].status == "CREATED"

        run_in_database(database_url, body)
        assert gateway.fetched == [], "an unverified callback must not cost an API call"

    def test_the_gateway_failing_does_not_grant_entitlements(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(fetch_error=RazorpayError("gateway down")))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-confirm-6")
            order = await make_order(session, user_id=user.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                "razorpay_signature": checkout_signature("order_TESTGATEWAY1", "pay_TESTGATEWAY1"),
            }
            async with Client(session, user.id, payment_settings()) as client:
                response = await client.post(self._path(), json=payload)

            assert response.status_code == 409
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0
            assert (await rows(database_url, all_orders()))[0].status == "CREATED"

        run_in_database(database_url, body)

    def test_confirming_twice_does_not_extend_the_subscription_twice(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """The callback and a provider retry both legitimately do this."""
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-confirm-7")
            order = await make_order(session, user_id=user.id)
            payload = {
                "order_id": str(order.id),
                "razorpay_payment_id": "pay_TESTGATEWAY1",
                "razorpay_signature": checkout_signature("order_TESTGATEWAY1", "pay_TESTGATEWAY1"),
            }
            async with Client(session, user.id, payment_settings()) as client:
                first = await client.post(self._path(), json=payload)
                second = await client.post(self._path(), json=payload)

            assert first.status_code == 200
            assert second.status_code == 200
            subscription = (await rows(database_url, all_subscriptions()))[0]
            assert len(await rows(database_url, all_subscriptions())) == 1
            # The expiry is anchored to ONE payment: 365 days from activation, not
            # 730 from a second application.
            assert abs((subscription.expires_at - datetime.now(UTC)) - YEAR) < timedelta(minutes=5)

        run_in_database(database_url, body)


# ------------------------------------------------------------------ provider webhook


class TestWebhookRoute:
    _path = "/api/v1/webhooks/razorpay"

    async def _post(self, session: Any, body_bytes: bytes, *, secret: str = WEBHOOK_SECRET):
        async with Client(session, None, payment_settings()) as client:
            return await client.post(
                self._path,
                content=body_bytes,
                headers={
                    SIGNATURE_HEADER: sign(body_bytes, secret),
                    "Content-Type": "application/json",
                },
            )

    def test_a_signed_capture_activates_the_subscription_without_the_browser(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-1")
            await make_order(session, user_id=user.id)
            response = await self._post(session, webhook_body())

            assert response.status_code == 200, response.text
            assert response.json()["data"]["handled"] is True
            subscription = (await rows(database_url, all_subscriptions()))[0]
            assert subscription.user_id == user.id
            assert subscription.status == "ACTIVE"
            assert abs((subscription.expires_at - datetime.now(UTC)) - YEAR) < timedelta(minutes=5)
            assert (await rows(database_url, all_orders()))[0].status == "PAID"
            event = (await rows(database_url, select(PaymentEvent)))[0]
            assert event.event_id == "evt_TEST1"
            assert event.signature_verified is True
            assert event.processed_at is not None

        run_in_database(database_url, body)

    def test_a_replayed_webhook_is_archived_once_and_the_subscription_is_not_extended(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-2")
            await make_order(session, user_id=user.id)
            payload = webhook_body()
            first = await self._post(session, payload)
            second = await self._post(session, payload)

            assert first.status_code == 200
            # 200 again, deliberately: a 4xx here makes the provider retry a delivery
            # that can never succeed.
            assert second.status_code == 200
            assert await scalar(database_url, select(func.count()).select_from(PaymentEvent)) == 1
            subscriptions = await rows(database_url, all_subscriptions())
            assert len(subscriptions) == 1
            assert abs((subscriptions[0].expires_at - datetime.now(UTC)) - YEAR) < timedelta(
                minutes=5
            )

        run_in_database(database_url, body)

    def test_a_webhook_signed_with_the_wrong_secret_writes_nothing(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-3")
            await make_order(session, user_id=user.id)
            response = await self._post(session, webhook_body(), secret="attacker_secret")

            assert response.status_code == 401
            assert await scalar(database_url, select(func.count()).select_from(PaymentEvent)) == 0
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0
            assert (await rows(database_url, all_orders()))[0].status == "CREATED"

        run_in_database(database_url, body)

    def test_a_single_tampered_byte_invalidates_an_otherwise_valid_body(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """The signature covers the bytes, so re-serialising the parsed body breaks it."""
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-4")
            await make_order(session, user_id=user.id)
            original = webhook_body()
            tampered = original.replace(b"pay_TESTGATEWAY1", b"pay_ATTACKERXXX")
            async with Client(session, None, payment_settings()) as client:
                response = await client.post(
                    self._path,
                    content=tampered,
                    headers={
                        # The genuine signature for the ORIGINAL body.
                        SIGNATURE_HEADER: sign(original),
                        "Content-Type": "application/json",
                    },
                )

            assert response.status_code == 401
            assert await scalar(database_url, select(func.count()).select_from(PaymentEvent)) == 0

        run_in_database(database_url, body)

    def test_an_event_type_we_do_not_act_on_is_archived_and_answered_200(
        self, database_url: str, use_gateway: Any
    ) -> None:
        gateway = use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-5")
            await make_order(session, user_id=user.id)
            response = await self._post(
                session,
                webhook_body(event_id="evt_TEST_OTHER", event_type="refund.created"),
            )

            assert response.status_code == 200, response.text
            assert response.json()["data"]["handled"] is False
            event = (await rows(database_url, select(PaymentEvent)))[0]
            assert event.event_type == "refund.created"
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0

        run_in_database(database_url, body)
        assert gateway.fetched == []

    def test_an_event_for_an_order_we_do_not_have_is_archived_not_acted_on(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-6")
            await make_order(session, user_id=user.id, provider_order_id="order_OURS")
            response = await self._post(session, webhook_body(provider_order_id="order_NOT_OURS"))

            assert response.status_code == 200, response.text
            body_data = response.json()["data"]
            assert body_data["handled"] is False
            assert body_data["reason"] == "order_not_found"
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0

        run_in_database(database_url, body)

    def test_a_failed_payment_is_recorded_without_granting_access(
        self, database_url: str, use_gateway: Any
    ) -> None:
        use_gateway(FakeGateway(payment=gateway_payment()))

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-7")
            await make_order(session, user_id=user.id)
            response = await self._post(
                session,
                webhook_body(
                    event_id="evt_TEST_FAILED", event_type="payment.failed", status="failed"
                ),
            )

            assert response.status_code == 200, response.text
            assert response.json()["data"]["handled"] is True
            assert await scalar(database_url, select(func.count()).select_from(Subscription)) == 0
            order = (await rows(database_url, all_orders()))[0]
            assert order.status == "FAILED"
            assert order.failure_reason

        run_in_database(database_url, body)

    def test_a_malformed_body_with_a_valid_signature_is_a_400(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """Signed but not JSON: provenance is not shape."""
        use_gateway(FakeGateway(payment=gateway_payment()))
        not_json = b"{not json at all"

        async def body(session: Any) -> None:
            response = await self._post(session, not_json)
            assert response.status_code == 400
            assert await scalar(database_url, select(func.count()).select_from(PaymentEvent)) == 0

        run_in_database(database_url, body)

    def test_a_body_without_an_event_id_is_refused_rather_than_processed(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """Without an id it cannot be deduplicated, and processing it twice is then
        unavoidable - so it is refused instead."""
        gateway = use_gateway(FakeGateway(payment=gateway_payment()))
        no_id = json.dumps({"event": "payment.captured", "payload": {}}).encode()

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-hook-8")
            await make_order(session, user_id=user.id)
            response = await self._post(session, no_id)
            assert response.status_code == 400
            assert await scalar(database_url, select(func.count()).select_from(PaymentEvent)) == 0

        run_in_database(database_url, body)
        assert gateway.fetched == []


# ----------------------------------------------------------------- subscription read


class TestSubscriptionReadRoute:
    def test_the_entitlement_payload_reflects_the_database_row(
        self, database_url: str, use_gateway: Any
    ) -> None:
        """Entitlements are read from PostgreSQL on every request, not cached."""

        async def body(session: Any) -> None:
            user = await make_user(session, "test-auth-read-1")
            settings = payment_settings()
            async with Client(session, user.id, settings) as client:
                before = await client.get("/api/v1/payments/subscription")
                assert before.status_code == 200
                assert before.json()["data"]["tier"] == "FREE"

                # Activation through the store the routes use, then read the same
                # endpoint again: an unchanged answer would mean a stale source.
                store = SqlBillingStore(session)
                await make_order(session, user_id=user.id)
                await store.activate(
                    user_id=user.id,
                    activation=apply_payment(
                        current=None, plan=PREMIUM, now=datetime.now(UTC), payment_id="pay_x"
                    ),
                    plan_code=PREMIUM.code,
                    amount_paise=PREMIUM.amount_paise,
                    currency="INR",
                    provider="razorpay",
                )
                await session.commit()

                after = await client.get("/api/v1/payments/subscription")

            assert after.status_code == 200, after.text
            assert after.json()["data"]["tier"] == Tier.PREMIUM.value

        run_in_database(database_url, body)


class TestReadOnlySanity:
    """Guards against the harness silently running against nothing."""

    def test_the_database_is_reachable(self, database_url: str) -> None:
        assert sync_url() is not None

        async def body(session: Any) -> None:
            assert await scalar(database_url, select(func.count()).select_from(User)) == 0

        run_in_database(database_url, body)
