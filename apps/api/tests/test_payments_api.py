"""HTTP tests for the payment endpoints.

The service rules live in ``test_billing.py``. What is checked here is the layer
above - authentication, the shape of the responses, and above all the four things
that decide whether money turns into access:

  1. A client cannot name its own price.
  2. A forged Checkout callback cannot activate anything.
  3. The webhook is verified over the bytes that arrived, not over a re-encoding.
  4. A replay is a no-op, whichever path delivers it first.

The store and the gateway are test doubles, because both would otherwise need
PostgreSQL and a live Razorpay account. The doubles are deliberately faithful
about the one behaviour these tests depend on: ``record_event`` returns False for
an event id it has already archived, and ``mark_order_paid`` returns False for an
order that is already PAID. Those two booleans ARE the idempotency mechanism, so a
double that always returned True would make every replay test pass for the wrong
reason.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import payments as payments_module
from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.integrations.razorpay import GatewayOrder, GatewayPayment, RazorpayError
from app.main import app
from app.models.user import PaymentOrder, Subscription
from app.services.billing import PLANS_BY_CODE

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
KEY_ID = "rzp_test_key_id"
KEY_SECRET = "rzp_test_key_secret"
WEBHOOK_SECRET = "whsec_test_secret"
PROVIDER_ORDER = "order_PROVIDER123"
PAYMENT_ID = "pay_PROVIDER456"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


# ============================================================== test doubles


def make_order(**overrides: Any) -> PaymentOrder:
    """A CREATED order row, as the database would hold it."""
    defaults: dict[str, Any] = {
        "user_id": USER_ID,
        "plan_code": "PREMIUM_YEARLY",
        "tier": "PREMIUM",
        "amount_paise": 99_900,
        "currency": "INR",
        "receipt": "rcpt_" + "a" * 32,
        "status": "CREATED",
        "expires_at": NOW + timedelta(hours=24),
    }
    defaults.update(overrides)
    order = PaymentOrder(**defaults)
    order.id = overrides.get("id", uuid.uuid4())
    return order


def make_subscription(**overrides: Any) -> Subscription:
    defaults: dict[str, Any] = {
        "user_id": USER_ID,
        "tier": "PREMIUM",
        "status": "ACTIVE",
        "started_at": NOW,
        "expires_at": NOW + timedelta(days=365),
        "auto_renew": False,
        "provider": "razorpay",
        "amount_paise": 99_900,
        "currency": "INR",
    }
    defaults.update(overrides)
    return Subscription(**defaults)


class FakeSession:
    """Stands in for the request-scoped AsyncSession."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1
        if FakeStore.record_commits:
            FakeGateway.calls.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        return None


class FakeStore:
    """Faithful about idempotency, recording about everything else."""

    #: When set, commits are recorded in `FakeGateway.calls` so a test can assert
    #: that the order row was COMMITTED before the gateway was called, not merely
    #: that the two calls happened in that order.
    record_commits = False

    order: PaymentOrder | None = None
    provider_lookup: PaymentOrder | None = None
    subscription: Subscription | None = None
    seen_events: ClassVar[set[str]] = set()
    activate_calls: ClassVar[list[dict[str, Any]]] = []
    failed: ClassVar[list[dict[str, Any]]] = []
    events: ClassVar[list[dict[str, Any]]] = []
    marked_paid: ClassVar[list[dict[str, Any]]] = []
    created: ClassVar[list[dict[str, Any]]] = []
    activated_subscription: Subscription | None = None

    def __init__(self, session: Any) -> None:
        self._session = session

    async def create_order(self, **kwargs: Any) -> PaymentOrder:
        FakeStore.created.append(kwargs)
        if FakeStore.record_commits:
            FakeGateway.calls.append("db")
        # The real store COMMITS here (see SqlBillingStore.create_order); the fake
        # mirrors that, because a fake that only records the insert would let the
        # route defer the commit until after the gateway call without any test
        # noticing.
        await self._session.commit()
        order = make_order(
            user_id=kwargs["user_id"],
            plan_code=kwargs["plan_code"],
            tier=kwargs["tier"].value,
            amount_paise=kwargs["amount_paise"],
            currency=kwargs["currency"],
            receipt=kwargs["receipt"],
        )
        FakeStore.order = order
        return order

    async def attach_provider_order(self, order: PaymentOrder, provider_order_id: str) -> None:
        order.provider_order_id = provider_order_id

    async def order_for_user(
        self, *, order_id: uuid.UUID, user_id: uuid.UUID
    ) -> PaymentOrder | None:
        order = FakeStore.order
        if order is None or order.user_id != user_id or order.id != order_id:
            return None
        return order

    async def order_by_provider_order_id(self, provider_order_id: str) -> PaymentOrder | None:
        if FakeStore.provider_lookup is None:
            order = FakeStore.order
            if order is not None and order.provider_order_id == provider_order_id:
                return order
        return FakeStore.provider_lookup

    async def mark_order_paid(
        self,
        order: PaymentOrder,
        *,
        provider_payment_id: str,
        provider_status: str,
        paid_at: datetime,
    ) -> bool:
        FakeStore.marked_paid.append({"order": str(order.id), "payment": provider_payment_id})
        if order.status == "PAID":
            return False
        order.status = "PAID"
        order.provider_payment_id = provider_payment_id
        order.provider_status = provider_status
        order.paid_at = paid_at
        return True

    async def mark_order_failed(self, order: PaymentOrder, *, reason: str) -> None:
        if order.status == "PAID":
            return
        FakeStore.failed.append({"order": str(order.id), "reason": reason})
        order.status = "FAILED"
        order.failure_reason = reason

    async def active_subscription(self, user_id: uuid.UUID, *, for_update: bool = False):
        return FakeStore.subscription

    async def activate(self, **kwargs: Any) -> Subscription:
        FakeStore.activate_calls.append(kwargs)
        activation = kwargs["activation"]
        existing = FakeStore.subscription
        if existing is not None:
            existing.tier = activation.tier.value
            existing.status = activation.status.value
            existing.expires_at = activation.expires_at
            FakeStore.activated_subscription = existing
            return existing
        subscription = make_subscription(
            user_id=kwargs["user_id"],
            tier=activation.tier.value,
            status=activation.status.value,
            expires_at=activation.expires_at,
            amount_paise=kwargs["amount_paise"],
        )
        FakeStore.subscription = subscription
        FakeStore.activated_subscription = subscription
        return subscription

    async def record_event(self, **kwargs: Any) -> bool:
        FakeStore.events.append(kwargs)
        event_id = kwargs["event_id"]
        if event_id in FakeStore.seen_events:
            return False
        FakeStore.seen_events.add(event_id)
        return True

    async def mark_event_processed(self, event_id: str, *, error: str | None = None) -> None:
        return None


class FakeGateway:
    key_id = KEY_ID

    order_result: GatewayOrder | None = None
    payment_result: GatewayPayment | None = None
    raises: Exception | None = None
    created: ClassVar[list[dict[str, Any]]] = []
    fetched: ClassVar[list[str]] = []
    calls: ClassVar[list[str]] = []

    def __init__(self, settings: Any, *, base_url: str | None = None) -> None:
        self.settings = settings

    async def __aenter__(self) -> FakeGateway:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def create_order(self, **kwargs: Any) -> GatewayOrder:
        FakeGateway.created.append(kwargs)
        if FakeStore.record_commits:
            FakeGateway.calls.append("gateway")
        if FakeGateway.raises is not None:
            raise FakeGateway.raises
        if FakeGateway.order_result is not None:
            return FakeGateway.order_result
        return GatewayOrder(
            id=PROVIDER_ORDER,
            amount_paise=kwargs["amount_paise"],
            currency=kwargs.get("currency", "INR"),
            receipt=kwargs["receipt"],
            status="created",
            raw={},
        )

    async def fetch_payment(self, payment_id: str) -> GatewayPayment:
        FakeGateway.fetched.append(payment_id)
        if FakeGateway.raises is not None:
            raise FakeGateway.raises
        if FakeGateway.payment_result is not None:
            return FakeGateway.payment_result
        return GatewayPayment(
            id=payment_id,
            order_id=PROVIDER_ORDER,
            amount_paise=99_900,
            currency="INR",
            status="captured",
            captured=True,
            method="upi",
            email="student@example.com",
            raw={},
        )


# ================================================================ fixtures


@pytest.fixture(autouse=True)
def reset_doubles(monkeypatch):
    FakeStore.order = None
    FakeStore.provider_lookup = None
    FakeStore.subscription = None
    FakeStore.seen_events = set()
    FakeStore.activate_calls = []
    FakeStore.failed = []
    FakeStore.events = []
    FakeStore.marked_paid = []
    FakeStore.created = []
    FakeStore.activated_subscription = None
    FakeGateway.order_result = None
    FakeGateway.payment_result = None
    FakeGateway.raises = None
    FakeGateway.created = []
    FakeGateway.fetched = []
    FakeGateway.calls = []
    FakeStore.record_commits = False

    import app.repositories.billing as billing_module

    monkeypatch.setattr(billing_module, "SqlBillingStore", FakeStore)
    monkeypatch.setattr(payments_module, "SqlBillingStore", FakeStore)
    monkeypatch.setattr(payments_module, "RazorpayClient", FakeGateway)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def session() -> FakeSession:
    """The session the routes will be handed.

    The override is ALSO installed by the ``client`` fixture below, on purpose: a
    test that forgets to request this fixture would otherwise be handed a real
    ``AsyncSession``, and every ``await session.commit()`` in the route would be a
    silent no-op against an idle session - a test that passes while asserting
    nothing about durability. That is exactly how this file's commit test was
    briefly fooled.
    """
    return FakeSession()


@pytest.fixture
def client(session: FakeSession) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        auth_user_id="test-auth-uid-123", email="student@example.com", role="STUDENT", claims={}
    )
    app.dependency_overrides[payments_module.get_billing_user] = lambda: USER_ID
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,  # type: ignore[call-arg]
        razorpay_key_id=KEY_ID,
        razorpay_key_secret=KEY_SECRET,
        razorpay_webhook_secret=WEBHOOK_SECRET,
    )
    return TestClient(app)


@pytest.fixture
def unpayable_client(client: TestClient) -> TestClient:
    """A deployment without Razorpay keys."""
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    return client


def signed_webhook(payload: dict[str, Any], *, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Serialise a webhook body and sign those exact bytes.

    Signed here rather than in the route so the test computes its own truth: if
    the route ever verified a re-serialised body instead of the received one, the
    signature would not match and these tests would fail - which is the point.
    """
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, signature


def webhook_payload(**entity_overrides: Any) -> dict[str, Any]:
    entity = {
        "id": PAYMENT_ID,
        "order_id": PROVIDER_ORDER,
        "amount": 99_900,
        "currency": "INR",
        "status": "captured",
        "captured": True,
        "email": "student@example.com",
    }
    entity.update(entity_overrides)
    return {"id": "evt_1", "event": "payment.captured", "payload": {"payment": {"entity": entity}}}


def checkout_signature(*, order_id: str, payment_id: str, secret: str = KEY_SECRET) -> str:
    payload = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


# ================================================================ catalogue


class TestPlanCatalogue:
    def test_plans_are_public(self, client: TestClient) -> None:
        # No auth override removed here on purpose: the pricing page must render
        # for someone who has not signed up yet.
        app.dependency_overrides.pop(get_current_principal, None)
        response = client.get("/api/v1/payments/plans")
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["requestId"] != "-"
        codes = [plan["code"] for plan in body["data"]["plans"]]
        assert "PREMIUM_YEARLY" in codes
        assert "FREE" in codes

    def test_the_catalogue_quotes_integer_paise(self, client: TestClient) -> None:
        plans = client.get("/api/v1/payments/plans").json()["data"]["plans"]
        premium = next(plan for plan in plans if plan["code"] == "PREMIUM_YEARLY")
        assert premium["amountPaise"] == 99_900
        assert isinstance(premium["amountPaise"], int)

    def test_the_catalogue_exposes_no_secrets(self, client: TestClient) -> None:
        raw = client.get("/api/v1/payments/plans").text
        assert KEY_SECRET not in raw
        assert WEBHOOK_SECRET not in raw


# ==================================================================== orders


class TestCreateOrder:
    PATH = "/api/v1/payments/order"

    def test_creates_an_order_priced_by_the_server(self, client: TestClient, session) -> None:
        response = client.post(self.PATH, json={"plan_code": "PREMIUM_YEARLY"})
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["amountPaise"] == 99_900
        assert data["providerKeyId"] == KEY_ID
        assert data["providerOrderId"] == PROVIDER_ORDER
        # The amount the gateway was asked for is the catalogue price, not a
        # number that arrived in the request.
        assert FakeGateway.created[0]["amount_paise"] == 99_900
        assert session.commits >= 1

    def test_the_client_cannot_name_its_own_price(self, client: TestClient) -> None:
        """The whole security posture of this endpoint, in one request.

        `amountPaise: 1` in the body must be rejected outright rather than
        ignored, because an ignored field is one a future refactor might start
        reading. `extra="forbid"` on StrictRequest makes that structurally true.
        """
        response = client.post(
            self.PATH,
            json={
                "plan_code": "PREMIUM_YEARLY",
                "amount_paise": 1,
                "amountPaise": 1,
                "tier": "PREMIUM_PLUS_YEARLY",
            },
        )
        assert response.status_code == 422
        assert FakeGateway.created == []

    def test_rejects_the_free_tier(self, client: TestClient) -> None:
        # A zero-amount order that a webhook could activate is a free upgrade.
        response = client.post(self.PATH, json={"plan_code": "FREE"})
        assert response.status_code == 422
        assert FakeGateway.created == []

    def test_rejects_an_unknown_plan(self, client: TestClient) -> None:
        response = client.post(self.PATH, json={"plan_code": "PREMIUM_FOREVER"})
        assert response.status_code == 422
        assert response.json()["type"].endswith("plan")

    def test_the_order_is_committed_before_the_gateway_is_called(
        self, client: TestClient, session: FakeSession
    ) -> None:
        """Ordering AND durability, which are not the same thing.

        The first version of this test asserted only that the database was
        touched before the gateway call. That passed while ``create_order`` merely
        flushed, so the row was still inside a transaction that a crash during the
        gateway call would have rolled back - a Razorpay order with no local owner,
        and a webhook nobody can reconcile.

        Asserting the commit is what pins the guarantee: an uncommitted insert is
        not a record.
        """
        FakeStore.record_commits = True
        client.post(self.PATH, json={"plan_code": "PREMIUM_YEARLY"})
        assert session.commits >= 1, "the order row was never committed"
        assert FakeGateway.calls[:3] == ["db", "commit", "gateway"], (
            "the order must be durably committed before Razorpay is called: "
            f"got {FakeGateway.calls}"
        )

    def test_a_gateway_failure_is_a_bad_gateway_and_marks_the_order(
        self, client: TestClient, session
    ) -> None:
        FakeGateway.raises = RazorpayError("provider said no")
        response = client.post(self.PATH, json={"plan_code": "PREMIUM_YEARLY"})
        assert response.status_code == 502
        # The order row is committed, marked FAILED: accurate, and it expires.
        assert FakeStore.failed, "the failed order was not recorded"
        assert session.commits >= 1

    def test_an_unconfigured_deployment_answers_503_not_500(
        self, unpayable_client: TestClient
    ) -> None:
        response = unpayable_client.post(self.PATH, json={"plan_code": "PREMIUM_YEARLY"})
        assert response.status_code == 503
        assert FakeGateway.created == []

    def test_an_unmapped_user_is_404(self, client: TestClient) -> None:
        # A verified Supabase token whose profile row does not exist yet. The
        # order has to belong to a users.id, so this cannot proceed.
        app.dependency_overrides[payments_module.get_billing_user] = lambda: None
        response = client.post(self.PATH, json={"plan_code": "PREMIUM_YEARLY"})
        assert response.status_code == 404
        assert FakeGateway.created == []

    def test_the_key_secret_never_appears_in_the_response(self, client: TestClient) -> None:
        raw = client.post(self.PATH, json={"plan_code": "PREMIUM_YEARLY"}).text
        assert KEY_SECRET not in raw
        assert WEBHOOK_SECRET not in raw

    def test_unauthenticated_requests_are_rejected(self) -> None:
        app.dependency_overrides.clear()
        response = TestClient(app).post(self.PATH, json={"plan_code": "PREMIUM_YEARLY"})
        assert response.status_code in (401, 403)

    def test_a_malformed_body_is_422(self, client: TestClient) -> None:
        assert client.post(self.PATH, json={}).status_code == 422
        assert client.post(self.PATH, json={"plan_code": ""}).status_code == 422


# ============================================================ confirmation


class TestConfirmPayment:
    PATH = "/api/v1/payments/confirm"

    def _order(self, **overrides: Any) -> PaymentOrder:
        fields = {"provider_order_id": PROVIDER_ORDER, **overrides}
        order = make_order(**fields)
        FakeStore.order = order
        return order

    def _body(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "order_id": str(FakeStore.order.id) if FakeStore.order else str(uuid.uuid4()),
            "razorpay_payment_id": PAYMENT_ID,
            "razorpay_signature": checkout_signature(
                order_id=PROVIDER_ORDER, payment_id=PAYMENT_ID
            ),
        }
        body.update(overrides)
        return body

    def test_activates_a_premium_subscription(self, client: TestClient, session) -> None:
        self._order()
        response = client.post(self.PATH, json=self._body())
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["tier"] == "PREMIUM"
        assert data["extended"] is False
        assert FakeStore.activate_calls, "no subscription was written"

    def test_the_payment_is_read_back_from_the_gateway(self, client: TestClient) -> None:
        # The signature alone is not accepted as proof. Without this call, a
        # signature over a never-captured payment would be enough.
        self._order()
        client.post(self.PATH, json=self._body())
        assert FakeGateway.fetched == [PAYMENT_ID]

    def test_a_forged_signature_is_refused(self, client: TestClient) -> None:
        self._order()
        response = client.post(self.PATH, json=self._body(razorpay_signature="0" * 64))
        assert response.status_code == 400
        assert FakeStore.activate_calls == []

    def test_a_signature_over_the_wrong_pair_is_refused(self, client: TestClient) -> None:
        self._order()
        response = client.post(
            self.PATH,
            json=self._body(
                razorpay_signature=checkout_signature(
                    order_id=PROVIDER_ORDER, payment_id="pay_OTHER"
                )
            ),
        )
        assert response.status_code == 400
        assert FakeStore.activate_calls == []

    def test_a_signature_made_with_the_wrong_secret_is_refused(self, client: TestClient) -> None:
        self._order()
        response = client.post(
            self.PATH,
            json=self._body(
                razorpay_signature=checkout_signature(
                    order_id=PROVIDER_ORDER, payment_id=PAYMENT_ID, secret="attacker"
                )
            ),
        )
        assert response.status_code == 400
        assert FakeStore.activate_calls == []

    def test_another_users_order_cannot_be_confirmed(self, client: TestClient) -> None:
        FakeStore.order = make_order(user_id=OTHER_USER_ID, provider_order_id=PROVIDER_ORDER)
        response = client.post(self.PATH, json=self._body())
        assert response.status_code == 404
        assert FakeStore.activate_calls == []

    def test_an_order_that_never_reached_the_gateway_cannot_be_confirmed(
        self, client: TestClient
    ) -> None:
        self._order(provider_order_id=None)
        response = client.post(self.PATH, json=self._body())
        assert response.status_code == 400

    def test_an_uncaptured_payment_does_not_activate(self, client: TestClient) -> None:
        # The attack: authorize a payment, then claim it as paid. The money is
        # only held and the authorization can be reversed.
        self._order()
        FakeGateway.payment_result = GatewayPayment(
            id=PAYMENT_ID,
            order_id=PROVIDER_ORDER,
            amount_paise=99_900,
            currency="INR",
            status="authorized",
            captured=False,
            method="upi",
            email=None,
            raw={},
        )
        response = client.post(self.PATH, json=self._body())
        assert response.status_code == 409
        assert "not_captured" in response.text
        assert FakeStore.activate_calls == []

    def test_an_underpayment_does_not_activate(self, client: TestClient) -> None:
        # Pay Rs 1 against an Rs 999 order.
        self._order()
        FakeGateway.payment_result = GatewayPayment(
            id=PAYMENT_ID,
            order_id=PROVIDER_ORDER,
            amount_paise=100,
            currency="INR",
            status="captured",
            captured=True,
            method="upi",
            email=None,
            raw={},
        )
        response = client.post(self.PATH, json=self._body())
        assert response.status_code == 409
        assert "amount_mismatch" in response.text
        assert FakeStore.activate_calls == []

    def test_a_payment_belonging_to_another_order_does_not_activate(
        self, client: TestClient
    ) -> None:
        self._order()
        FakeGateway.payment_result = GatewayPayment(
            id=PAYMENT_ID,
            order_id="order_SOMEONE_ELSE",
            amount_paise=99_900,
            currency="INR",
            status="captured",
            captured=True,
            method="upi",
            email=None,
            raw={},
        )
        response = client.post(self.PATH, json=self._body())
        assert response.status_code == 409
        assert "order_mismatch" in response.text
        assert FakeStore.activate_calls == []

    def test_a_replay_does_not_extend_a_second_time(self, client: TestClient) -> None:
        """The callback and the webhook racing is the normal case, not an edge case."""
        self._order()
        first = client.post(self.PATH, json=self._body())
        assert first.status_code == 200
        first_expiry = FakeStore.activated_subscription.expires_at

        second = client.post(self.PATH, json=self._body())
        assert second.status_code == 200
        # The order is already PAID, so nothing is activated again and the expiry
        # does not move.
        assert second.json()["data"].get("alreadyProcessed") is True
        assert FakeStore.activated_subscription.expires_at == first_expiry
        assert len(FakeStore.activate_calls) == 1

    def test_a_gateway_outage_is_a_conflict_not_an_activation(self, client: TestClient) -> None:
        self._order()
        FakeGateway.raises = RazorpayError("timeout")
        response = client.post(self.PATH, json=self._body())
        assert response.status_code == 409
        assert FakeStore.activate_calls == []

    def test_event_id_is_the_payment_id_so_the_webhook_collides_with_it(
        self, client: TestClient
    ) -> None:
        self._order()
        client.post(self.PATH, json=self._body())
        event_ids = [event["event_id"] for event in FakeStore.events]
        assert event_ids == [f"checkout:{PAYMENT_ID}"]

    def test_an_unconfigured_deployment_answers_503(self, unpayable_client: TestClient) -> None:
        self._order()
        assert unpayable_client.post(self.PATH, json=self._body()).status_code == 503

    def test_confirming_without_a_local_user_is_404(self, client: TestClient) -> None:
        self._order()
        app.dependency_overrides[payments_module.get_billing_user] = lambda: None
        assert client.post(self.PATH, json=self._body()).status_code == 404

    def test_confirming_with_a_foreign_order_uuid_is_404(self, client: TestClient) -> None:
        self._order()
        response = client.post(self.PATH, json=self._body(order_id=str(uuid.uuid4())))
        assert response.status_code == 404
        assert FakeStore.activate_calls == []

    def test_the_body_rejects_extra_fields(self, client: TestClient) -> None:
        # A client that could send `tier` would be naming its own entitlement.
        self._order()
        response = client.post(self.PATH, json=self._body(tier="PREMIUM_PLUS"))
        assert response.status_code == 422


# =================================================================== webhook


class TestRazorpayWebhook:
    PATH = "/api/v1/webhooks/razorpay"

    def _post(self, client: TestClient, body: bytes, signature: str):
        return client.post(
            self.PATH,
            content=body,
            headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"},
        )

    def test_an_unsigned_request_is_rejected(self, client: TestClient) -> None:
        body, _ = signed_webhook(webhook_payload())
        headers = {"Content-Type": "application/json"}
        response = client.post(self.PATH, content=body, headers=headers)
        assert response.status_code == 401
        assert FakeStore.activate_calls == []

    def test_a_wrong_signature_is_rejected(self, client: TestClient) -> None:
        body, _ = signed_webhook(webhook_payload())
        response = self._post(client, body, "f" * 64)
        assert response.status_code == 401
        assert FakeStore.activate_calls == []

    def test_a_body_signed_with_the_wrong_secret_is_rejected(self, client: TestClient) -> None:
        body, signature = signed_webhook(webhook_payload(), secret="attacker")
        assert self._post(client, body, signature).status_code == 401

    def test_a_tampered_body_is_rejected(self, client: TestClient) -> None:
        """Signature is over the amount, so editing the amount must not verify."""
        body, signature = signed_webhook(webhook_payload())
        tampered = body.replace(b"99900", b"1")
        assert tampered != body
        assert self._post(client, tampered, signature).status_code == 401

    def test_verification_uses_the_received_bytes_not_a_re_encoding(
        self, client: TestClient
    ) -> None:
        """Non-canonical JSON that a round-trip through json.loads/json.dumps
        would change. A handler that re-serialised before verifying would reject
        this genuine delivery - and the tempting fix for that is to stop
        verifying, so the raw bytes are what gets signed.
        """
        FakeStore.order = make_order(provider_order_id=PROVIDER_ORDER)
        raw = (
            b'{"id":   "evt_custom", "event":"payment.captured",\n'
            b' "payload": {"payment": {"entity": {"id": "pay_PROVIDER456",'
            b' "order_id": "order_PROVIDER123", "amount": 99900, "currency": "INR",'
            b' "status": "captured", "captured": true}}}}'
        )
        # The bytes a re-encode would not reproduce: extra spaces after the
        # colon, a newline, and key order that json.dumps would reorder.
        assert json.dumps(json.loads(raw)).encode() != raw
        signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        response = self._post(client, raw, signature)
        assert response.status_code == 200
        assert response.json()["data"]["handled"] is True

    def test_a_captured_payment_activates(self, client: TestClient, session) -> None:
        FakeStore.order = make_order(provider_order_id=PROVIDER_ORDER)
        body, signature = signed_webhook(webhook_payload())
        response = self._post(client, body, signature)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["handled"] is True
        assert data["tier"] == "PREMIUM"
        assert session.commits >= 1

    def test_a_duplicate_delivery_is_acknowledged_and_not_reapplied(
        self, client: TestClient
    ) -> None:
        """200 for a duplicate, deliberately.

        A 4xx here makes the provider retry a delivery that can never succeed.
        """
        FakeStore.order = make_order(provider_order_id=PROVIDER_ORDER)
        body, signature = signed_webhook(webhook_payload())
        assert self._post(client, body, signature).status_code == 200
        expiry = FakeStore.activated_subscription.expires_at

        second = self._post(client, body, signature)
        assert second.status_code == 200
        assert second.json()["data"].get("alreadyProcessed") is True
        assert FakeStore.activated_subscription.expires_at == expiry
        assert len(FakeStore.activate_calls) == 1

    def test_the_webhook_wins_the_race_against_the_callback(self, client: TestClient) -> None:
        """Whichever path arrives second must not extend again.

        The webhook arrives first here, then the browser callback repays the same
        payment. The callback is refused by the event archive, not by luck.
        """
        order = make_order(provider_order_id=PROVIDER_ORDER)
        FakeStore.order = order
        body, signature = signed_webhook(webhook_payload())
        assert self._post(client, body, signature).json()["data"]["handled"] is True
        expiry = FakeStore.activated_subscription.expires_at

        callback = client.post(
            "/api/v1/payments/confirm",
            json={
                "order_id": str(order.id),
                "razorpay_payment_id": PAYMENT_ID,
                "razorpay_signature": checkout_signature(
                    order_id=PROVIDER_ORDER, payment_id=PAYMENT_ID
                ),
            },
        )
        assert callback.status_code == 200
        assert callback.json()["data"].get("alreadyProcessed") is True
        assert FakeStore.activated_subscription.expires_at == expiry

    def test_a_failed_payment_marks_the_order_without_activating(self, client: TestClient) -> None:
        FakeStore.order = make_order(provider_order_id=PROVIDER_ORDER)
        payload = webhook_payload(status="failed", captured=False)
        payload["event"] = "payment.failed"
        body, signature = signed_webhook(payload)
        response = self._post(client, body, signature)
        assert response.status_code == 200
        assert FakeStore.failed, "the failure was not recorded on the order"
        assert FakeStore.activate_calls == []

    def test_an_ignored_event_type_is_acknowledged_and_archived(self, client: TestClient) -> None:
        # An error here causes the provider to retry a genuine event forever.
        payload = webhook_payload()
        payload["event"] = "refund.processed"
        body, signature = signed_webhook(payload)
        response = self._post(client, body, signature)
        assert response.status_code == 200
        assert response.json()["data"] == {
            "received": True,
            "handled": False,
            "reason": "event_type_ignored",
        }
        assert FakeStore.events[0]["event_type"] == "refund.processed"

    def test_an_order_we_do_not_have_is_archived_not_retried(self, client: TestClient) -> None:
        FakeStore.order = None
        FakeStore.provider_lookup = None
        body, signature = signed_webhook(webhook_payload())
        response = self._post(client, body, signature)
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "order_not_found"
        assert FakeStore.activate_calls == []

    def test_an_event_without_an_order_reference_is_archived(self, client: TestClient) -> None:
        body, signature = signed_webhook(webhook_payload(order_id=None))
        response = self._post(client, body, signature)
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "missing_order_reference"

    def test_an_event_without_an_id_is_rejected(self, client: TestClient) -> None:
        # Without an id it cannot be deduplicated, so it cannot be handled safely.
        payload = webhook_payload()
        del payload["id"]
        body, signature = signed_webhook(payload)
        response = self._post(client, body, signature)
        assert response.status_code == 400

    def test_a_non_json_body_is_rejected(self, client: TestClient) -> None:
        raw = b"not json at all"
        signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        assert self._post(client, raw, signature).status_code == 400

    def test_a_rejected_payment_is_acknowledged_not_retried(
        self, client: TestClient, session
    ) -> None:
        """A 5xx here would make Razorpay retry a payment the service will refuse
        every time, and a retry storm is worse than an accurate log line."""
        FakeStore.order = make_order(provider_order_id=PROVIDER_ORDER)
        FakeGateway.payment_result = GatewayPayment(
            id=PAYMENT_ID,
            order_id=PROVIDER_ORDER,
            amount_paise=100,
            currency="INR",
            status="captured",
            captured=True,
            method="upi",
            email=None,
            raw={},
        )
        body, signature = signed_webhook(webhook_payload(amount=100))
        response = self._post(client, body, signature)
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "amount_mismatch"
        assert FakeStore.activate_calls == []

    def test_a_rejected_payment_is_still_archived_for_reconciliation(
        self, client: TestClient
    ) -> None:
        """The audit trail has to answer "the money left my account" questions.

        A rejected event that is never stored leaves support with nothing but a
        log line, and §13.1 requires the provider payloads to be persisted. It is
        stored AFTER the rejection decision and flagged with the reason, so it can
        never occupy an idempotency key a genuine event needs.
        """
        FakeStore.order = make_order(provider_order_id=PROVIDER_ORDER)
        FakeGateway.payment_result = GatewayPayment(
            id=PAYMENT_ID,
            order_id=PROVIDER_ORDER,
            amount_paise=100,
            currency="INR",
            status="captured",
            captured=True,
            method="upi",
            email=None,
            raw={},
        )
        body, signature = signed_webhook(webhook_payload(amount=100))
        self._post(client, body, signature)
        assert len(FakeStore.events) == 1, "the rejected event was not archived"
        assert FakeStore.events[0]["payload"]["payload"]["payment"]["entity"]["amount"] == 100

    def test_the_webhook_never_reaches_the_gateway_for_a_bad_signature(
        self, client: TestClient
    ) -> None:
        # An unverified request must not be able to make this service call out.
        body, _ = signed_webhook(webhook_payload())
        self._post(client, body, "a" * 64)
        assert FakeGateway.fetched == []

    def test_the_raw_payload_is_archived_for_reconciliation(self, client: TestClient) -> None:
        FakeStore.order = make_order(provider_order_id=PROVIDER_ORDER)
        body, signature = signed_webhook(webhook_payload())
        self._post(client, body, signature)
        stored = FakeStore.events[0]
        assert stored["signature_verified"] is True
        assert stored["payload"]["payload"]["payment"]["entity"]["amount"] == 99_900


# ============================================================== entitlements


class TestSubscriptionRead:
    PATH = "/api/v1/payments/subscription"

    def test_a_user_without_a_subscription_is_free(self, client: TestClient) -> None:
        response = client.get(self.PATH)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["tier"] == "FREE"
        assert data["isPremium"] is False
        assert "unlimited_mocks" not in data["entitlements"]

    def test_an_active_subscription_reports_its_entitlements(self, client: TestClient) -> None:
        FakeStore.subscription = make_subscription()
        data = client.get(self.PATH).json()["data"]
        assert data["tier"] == "PREMIUM"
        assert data["isPremium"] is True
        assert "unlimited_mocks" in data["entitlements"]
        assert data["expiresAt"] is not None

    def test_an_expired_subscription_reads_as_free(self, client: TestClient) -> None:
        """No cron job is involved: the clock decides, at read time.

        A row left saying ACTIVE with a past expiry - exactly what a job that did
        not run leaves behind - must not confer anything.
        """
        FakeStore.subscription = make_subscription(
            status="ACTIVE", expires_at=datetime.now(UTC) - timedelta(days=1)
        )
        data = client.get(self.PATH).json()["data"]
        assert data["entitlements"] == sorted(sorted_entitlements_for_free()), (
            "an expired subscription still reported paid entitlements"
        )
        assert data["isPremium"] is False

    def test_a_cancelled_subscription_reads_as_free(self, client: TestClient) -> None:
        FakeStore.subscription = make_subscription(status="CANCELLED")
        assert client.get(self.PATH).json()["data"]["isPremium"] is False

    def test_an_unmapped_user_is_404(self, client: TestClient) -> None:
        app.dependency_overrides[payments_module.get_billing_user] = lambda: None
        assert client.get(self.PATH).status_code == 404

    def test_the_response_never_leaks_the_provider_payment_id(self, client: TestClient) -> None:
        # Reported on the order, not the subscription read: the checkout page
        # already knows its own payment id, and an id in a JSON body ends up in
        # analytics.
        FakeStore.subscription = make_subscription()
        assert PAYMENT_ID not in client.get(self.PATH).text


def sorted_entitlements_for_free() -> list[str]:
    from app.services.billing import TIER_ENTITLEMENTS, Tier

    return sorted(TIER_ENTITLEMENTS[Tier.FREE])


# =========================================================== wiring / contract


class TestWiring:
    def test_the_routes_are_mounted_under_api_v1(self) -> None:
        paths = app.openapi()["paths"]
        for path in [
            "/api/v1/payments/plans",
            "/api/v1/payments/order",
            "/api/v1/payments/confirm",
            "/api/v1/payments/subscription",
            "/api/v1/webhooks/razorpay",
        ]:
            assert path in paths, f"{path} is not mounted"

    def test_the_webhook_is_documented_as_taking_no_body_model(self) -> None:
        """The webhook must stay Body-free so the raw bytes are what gets read.

        Adding a Pydantic body parameter would make FastAPI parse the body first,
        which is precisely the mistake the handler is written to avoid.
        """
        operation = app.openapi()["paths"]["/api/v1/webhooks/razorpay"]["post"]
        assert "requestBody" not in operation

    def test_the_order_endpoint_rejects_a_client_supplied_amount_in_the_schema(self) -> None:
        schema = app.openapi()["components"]["schemas"]["CreateOrderIn"]
        assert set(schema["properties"]) == {"plan_code"}
        assert schema.get("additionalProperties") is False

    def test_the_order_state_enum_matches_the_migration_check_constraint(self) -> None:
        """The enum and the CHECK constraint must not drift.

        A state this code can write but the constraint rejects is a 500 at the
        worst possible moment - after a customer has paid.
        """
        import re
        from pathlib import Path

        from app.models.user import PaymentOrder
        from app.services.billing import OrderState

        migration = next(
            path for path in Path("alembic/versions").glob("*_payments.py")
        ).read_text()
        # The name FOLLOWS the expression in the DDL, so the enum values are read
        # from the neighbourhood of the constraint name rather than from a regex
        # that assumes a given order.
        marker = migration.find("ck_payment_orders_status")
        assert marker != -1, "the status CHECK constraint is missing from the migration"
        migration_values = set(
            re.findall(r"'([A-Z_]+)'", migration[max(0, marker - 200) : marker + 60])
        )

        model_check = next(
            constraint
            for constraint in PaymentOrder.__table__.constraints
            if getattr(constraint, "name", None) == "ck_payment_orders_status"
        )
        model_values = set(re.findall(r"'([A-Z_]+)'", str(model_check.sqltext)))

        expected = {state.value for state in OrderState}
        assert migration_values == expected, "the migration and OrderState disagree"
        assert model_values == expected, "the model and OrderState disagree"

    def test_the_order_body_has_no_money_field_at_all(self) -> None:
        # Not "no float amount" - no amount FIELD. Asserting on the serialised
        # schema would also match the docstring's use of the word, which is a
        # test that fails for the wrong reason and passes for the wrong reason.
        properties = set(app.openapi()["components"]["schemas"]["CreateOrderIn"]["properties"])
        assert properties == {"plan_code"}

    def test_a_camel_case_body_is_refused_rather_than_coerced(self, client: TestClient) -> None:
        """The wire format is snake_case, and a drift from it is a 422, not a purchase.

        This test exists because the FIRST version of the browser checkout sent
        ``{"planCode": ...}``. Every unit test passed - a stubbed fetch proves what the
        client sends and nothing about what the server accepts - and only a live call
        against a running API showed the 422. The client is fixed; this test pins the
        server's half so the next drift fails a build instead of a student's payment.

        ``extra="forbid"`` on every inbound schema is what makes this a hard failure
        rather than a silent drop of the field, which is why it is asserted at the HTTP
        layer: relaxing that flag would move the failure somewhere much worse.
        """
        refused = client.post("/api/v1/payments/order", json={"planCode": "PREMIUM_YEARLY"})
        assert refused.status_code == 422
        fields = {error["field"] for error in refused.json()["errors"]}
        # Both halves of the mismatch are reported: the field the server wanted and the
        # field the client actually sent.
        assert "plan_code" in fields
        assert "planCode" in fields

        # The body the browser now sends is accepted and reaches the gateway.
        accepted = client.post("/api/v1/payments/order", json={"plan_code": "PREMIUM_YEARLY"})
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["data"]["providerOrderId"] == PROVIDER_ORDER

    def test_the_confirm_body_uses_the_api_field_names(self, client: TestClient) -> None:
        """Checkout's own field names are not the API's, on purpose.

        Razorpay hands the browser ``razorpay_order_id``; the API wants its OWN
        ``order_id``, because the local id is scoped to the authenticated user while a
        gateway id would let anyone who obtained one describe another user's order.
        """
        properties = set(app.openapi()["components"]["schemas"]["ConfirmPaymentIn"]["properties"])
        assert properties == {"order_id", "razorpay_payment_id", "razorpay_signature"}

        camel = client.post(
            "/api/v1/payments/confirm",
            json={
                "orderId": str(uuid.uuid4()),
                "razorpayPaymentId": PAYMENT_ID,
                "razorpaySignature": "sig",
            },
        )
        assert camel.status_code == 422

    def test_the_purchasable_plan_codes_are_all_in_the_catalogue(self) -> None:
        from app.services.billing import PURCHASABLE_CODES, public_catalogue

        codes = {plan["code"] for plan in public_catalogue()}
        assert PURCHASABLE_CODES <= codes
        assert PLANS_BY_CODE["PREMIUM_PLUS_YEARLY"].amount_paise == 1_29_900
        # 1,299 = 999 (Premium) + 299 (the mock pack add-on) - the arithmetic the
        # remaining features support after video solutions were withdrawn. The owner
        # confirmed this figure; see the comment above PLANS in services/billing.py.
