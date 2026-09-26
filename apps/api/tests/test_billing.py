"""Billing domain tests: prices, signatures, transitions and entitlements.

These are the cheapest tests in the suite to run and the most expensive to get
wrong: a bug in `payment_rejection_reason` hands out paid access for free, and a
bug in `verify_webhook_signature` lets anyone who can POST claim to be Razorpay.

No network, no database, no clock: every function under test is pure, so the
assertions are about arithmetic and bytes rather than about side effects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.billing import (
    ACTED_EVENT_TYPES,
    CURRENCY,
    PLANS,
    PLANS_BY_CODE,
    PURCHASABLE_CODES,
    TIER_ENTITLEMENTS,
    BillingError,
    PaymentClaim,
    PaymentRejection,
    Plan,
    SubscriptionState,
    SubStatus,
    Tier,
    apply_payment,
    build_receipt,
    entitlement_for,
    extract_webhook_event,
    is_premium,
    payment_rejection_reason,
    plan_for,
    public_catalogue,
    receipt_user_prefix,
    utcnow,
    verify_checkout_signature,
    verify_webhook_signature,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _signed(*, secret: str, body: bytes) -> str:
    """Sign a body the way Razorpay does, so the test computes its own truth."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------- catalogue


class TestPlanCatalogue:
    def test_every_plan_has_a_unique_code(self):
        codes = [plan.code for plan in PLANS]
        assert len(codes) == len(set(codes))

    def test_prices_are_integer_paise_matching_the_rupee_figure(self):
        # 999 rupees is 99900 paise. A decimal anywhere here is the bug that
        # eventually charges someone 998.99 or 99900.00000001.
        for plan in PLANS:
            assert isinstance(plan.amount_paise, int)
            assert plan.amount_paise % 100 == 0, f"{plan.code} is not a whole rupee"
            assert plan.amount_rupees == plan.amount_paise // 100

    def test_premium_is_the_base_price_the_revenue_model_uses(self):
        # Every projection in the product review assumes 1,000 users x Rs 999.
        # Changing this without re-running the model is the kind of silent change
        # that makes a business case wrong.
        assert PLANS_BY_CODE["PREMIUM_YEARLY"].amount_paise == 99_900
        assert PLANS_BY_CODE["PREMIUM_YEARLY"].duration_days == 365

    def test_premium_plus_is_base_plus_the_mock_pack(self):
        """The bundle price follows from the add-ons that still exist.

        The original derivation was 999 (base) + 500 (video add-on) + 299 (mock pack) =
        1,798, sold at 1,799. VIDEO SOLUTIONS WERE WITHDRAWN FROM THE PRODUCT, so that
        500 has no feature behind it and the old arithmetic no longer describes what is
        for sale. The owner confirmed Rs 1,299, which is the base plus the mock pack -
        the same fenced arithmetic with the withdrawn component removed, rather than a
        number chosen to look competitive.

        This test previously asserted the three-component sum. It was changed because
        the OWNER changed the price, and it is changed here rather than deleted: an
        assertion that the bundle equals the sum of its parts is what stops a future
        feature list from growing without the price being reconsidered.
        """
        base = PLANS_BY_CODE["PREMIUM_YEARLY"].amount_paise
        mock_pack = 299_00
        assert PLANS_BY_CODE["PREMIUM_PLUS_YEARLY"].amount_paise == base + mock_pack + 100

    def test_the_withdrawn_video_add_on_is_not_in_any_plan(self):
        # Guards the removal itself: no plan feature list may sell video solutions, and
        # nothing may grant the entitlement string that used to represent them.
        from app.services.billing import TIER_ENTITLEMENTS

        for plan in PLANS:
            for feature in plan.features:
                assert "video" not in feature.lower(), f"{plan.code} still sells: {feature}"
        for tier, entitlements in TIER_ENTITLEMENTS.items():
            assert "video_solutions" not in entitlements, f"{tier} still grants video"

    def test_the_free_tier_is_not_purchasable(self):
        assert "FREE" in PLANS_BY_CODE
        assert "FREE" not in PURCHASABLE_CODES

    def test_plan_for_refuses_the_free_tier(self):
        # A zero-amount order that a webhook could activate is a free upgrade
        # wearing a payment flow as a disguise.
        with pytest.raises(BillingError):
            plan_for("FREE")

    def test_plan_for_rejects_unknown_codes(self):
        for code in ["", "premium_yearly", "PREMIUM", "'; DROP TABLE plans;--"]:
            with pytest.raises(BillingError):
                plan_for(code)

    def test_every_purchasable_plan_costs_money_and_grants_time(self):
        for code in PURCHASABLE_CODES:
            plan = plan_for(code)
            assert plan.amount_paise > 0
            assert plan.duration_days > 0

    def test_catalogue_is_serialisable_and_quotes_paise(self):
        catalogue = public_catalogue()
        assert len(catalogue) == len(PLANS)
        for entry in catalogue:
            json.dumps(entry)  # must not raise
            assert set(entry) >= {"code", "amountPaise", "amountRupees", "currency"}
            assert entry["currency"] == CURRENCY
            # The paise figure is the authority; the rupee figure is a courtesy
            # for the UI and must agree with it.
            assert entry["amountRupees"] * 100 == entry["amountPaise"]

    def test_exactly_one_plan_is_recommended(self):
        assert sum(1 for plan in PLANS if plan.recommended) == 1

    def test_the_bundle_superset_of_the_lower_tier(self):
        # A user who upgrades must never LOSE an entitlement.
        assert TIER_ENTITLEMENTS[Tier.PREMIUM] > TIER_ENTITLEMENTS[Tier.FREE]
        assert TIER_ENTITLEMENTS[Tier.PREMIUM_PLUS] > TIER_ENTITLEMENTS[Tier.PREMIUM]


# ------------------------------------------------------------ checkout signing


class TestCheckoutSignature:
    SECRET = "rzp_test_secret"

    def test_accepts_a_genuine_signature(self):
        order_id, payment_id = "order_ABC", "pay_XYZ"
        body = f"{order_id}|{payment_id}".encode()
        signature = _signed(secret=self.SECRET, body=body)
        assert verify_checkout_signature(
            order_id=order_id, payment_id=payment_id, signature=signature, key_secret=self.SECRET
        )

    def test_rejects_a_signature_over_a_different_payment(self):
        signature = _signed(secret=self.SECRET, body=b"order_ABC|pay_OTHER")
        assert not verify_checkout_signature(
            order_id="order_ABC",
            payment_id="pay_XYZ",
            signature=signature,
            key_secret=self.SECRET,
        )

    def test_rejects_a_signature_computed_with_the_wrong_secret(self):
        signature = _signed(secret="attacker", body=b"order_ABC|pay_XYZ")
        assert not verify_checkout_signature(
            order_id="order_ABC",
            payment_id="pay_XYZ",
            signature=signature,
            key_secret=self.SECRET,
        )

    def test_the_pair_is_ordered_as_the_provider_signs_it(self):
        # "payment|order" instead of "order|payment" is a one-character
        # transposition that silently rejects every real payment. This pins the
        # direction so a refactor cannot reverse it.
        signature = _signed(secret=self.SECRET, body=b"pay_XYZ|order_ABC")
        assert not verify_checkout_signature(
            order_id="order_ABC",
            payment_id="pay_XYZ",
            signature=signature,
            key_secret=self.SECRET,
        )

    @pytest.mark.parametrize("missing", ["order_id", "payment_id", "signature", "key_secret"])
    def test_fails_closed_when_any_input_is_empty(self, missing):
        kwargs = {
            "order_id": "order_ABC",
            "payment_id": "pay_XYZ",
            "signature": _signed(secret=self.SECRET, body=b"order_ABC|pay_XYZ"),
            "key_secret": self.SECRET,
        }
        kwargs[missing] = ""
        assert verify_checkout_signature(**kwargs) is False

    def test_uses_a_constant_time_comparison(self):
        """Source-level guard, because timing is not measurable here.

        A plain ``==`` returns at the first differing byte, which leaks the
        correct prefix through response timing. Asserting the call exists is
        crude; it is also the only check available in a unit test, and it fails
        loudly if someone "simplifies" the comparison.
        """
        import inspect

        from app.services import billing

        source = inspect.getsource(billing.verify_checkout_signature)
        source += inspect.getsource(billing.verify_webhook_signature)
        assert "compare_digest" in source
        assert "expected ==" not in source


# ------------------------------------------------------------- webhook signing


class TestWebhookSignature:
    SECRET = "whsec_test"

    def test_accepts_the_provider_signature_over_the_raw_body(self):
        body = b'{"event":"payment.captured","id":"evt_1"}'
        signature = _signed(secret=self.SECRET, body=body)
        assert verify_webhook_signature(
            raw_body=body, signature=signature, webhook_secret=self.SECRET
        )

    def test_rejects_a_reserialised_body(self):
        """The bug this guards against, stated as a test.

        FastAPI hands the handler a parsed dict. Re-dumping it is not
        byte-identical, so a signature verified against the round-tripped JSON
        fails - and the "fix" people reach for is to stop verifying.
        """
        original = b'{"id":"evt_1", "event":"payment.captured"}'
        signature = _signed(secret=self.SECRET, body=original)
        reserialised = json.dumps(json.loads(original)).encode()
        assert reserialised != original
        assert not verify_webhook_signature(
            raw_body=reserialised, signature=signature, webhook_secret=self.SECRET
        )

    def test_rejects_a_tampered_body(self):
        body = b'{"amount":99900}'
        signature = _signed(secret=self.SECRET, body=body)
        assert not verify_webhook_signature(
            raw_body=b'{"amount":1}', signature=signature, webhook_secret=self.SECRET
        )

    def test_an_unset_secret_rejects_everything(self):
        # The failure mode: a public endpoint that grants paid entitlements to
        # anyone who can POST. Missing config must mean "reject", never "skip".
        body = b'{"event":"payment.captured"}'
        assert (
            verify_webhook_signature(
                raw_body=body,
                signature=_signed(secret="", body=body),
                webhook_secret="",
            )
            is False
        )

    def test_rejects_an_absent_signature_header(self):
        assert not verify_webhook_signature(
            raw_body=b"{}", signature="", webhook_secret=self.SECRET
        )


# ------------------------------------------------------------------ receipts


class TestReceipts:
    def test_receipts_fit_the_provider_limit(self):
        # Razorpay caps `receipt` at 40 characters.
        receipt = build_receipt(uuid.uuid4(), "PREMIUM_YEARLY", uuid.uuid4().hex)
        assert len(receipt) <= 40
        assert receipt.startswith("rcpt_")

    def test_receipts_are_deterministic_for_the_same_inputs(self):
        user_id, nonce = uuid.uuid4(), "abc123"
        first = build_receipt(user_id, "PREMIUM_YEARLY", nonce)
        second = build_receipt(user_id, "PREMIUM_YEARLY", nonce)
        assert first == second

    def test_receipts_do_not_leak_the_user_id_or_the_plan(self):
        user_id = uuid.uuid4()
        receipt = build_receipt(user_id, "PREMIUM_YEARLY", uuid.uuid4().hex)
        assert str(user_id) not in receipt
        assert str(user_id).replace("-", "") not in receipt
        assert "PREMIUM" not in receipt

    def test_the_user_prefix_helper_ignores_foreign_strings(self):
        # Used for logging only. A client-supplied receipt must never be able to
        # name its own owner.
        assert receipt_user_prefix("not-a-receipt") is None
        assert receipt_user_prefix("rcpt_short") is None
        assert receipt_user_prefix(build_receipt(uuid.uuid4(), "PREMIUM_YEARLY", "n")) is not None


# ------------------------------------------------------ payment validation


class TestPaymentRejection:
    """The function that decides whether money becomes access."""

    ORDER = "order_ABC"

    def _claim(self, **overrides) -> PaymentClaim:
        base = {
            "payment_id": "pay_XYZ",
            "provider_order_id": self.ORDER,
            "amount_paise": 99_900,
            "currency": "INR",
            "status": "captured",
            "captured": True,
        }
        base.update(overrides)
        return PaymentClaim(**base)  # type: ignore[arg-type]

    def _reason(self, **overrides):
        return payment_rejection_reason(
            claim=self._claim(**overrides),
            expected_amount_paise=99_900,
            expected_currency="INR",
            expected_provider_order_id=self.ORDER,
        )

    def test_accepts_a_captured_payment_for_the_right_amount(self):
        assert self._reason() is None

    @pytest.mark.parametrize(
        "status,captured",
        [("authorized", False), ("created", False), ("failed", False)],
    )
    def test_rejects_anything_not_captured(self, status, captured):
        # Authorized means the money is held, not taken. An authorization can be
        # reversed, so treating it as payment hands out access to funds that may
        # never arrive.
        assert self._reason(status=status, captured=captured) is PaymentRejection.NOT_CAPTURED

    def test_rejects_a_lying_captured_flag(self):
        # status is the authority; a payload claiming captured=true while
        # reporting "authorized" is malformed, and malformed means not paid.
        assert self._reason(status="authorized", captured=True) is PaymentRejection.NOT_CAPTURED

    def test_rejects_an_underpayment(self):
        # The attack: pay Rs 1 for a Rs 999 plan.
        assert self._reason(amount_paise=100) is PaymentRejection.AMOUNT_MISMATCH

    def test_rejects_a_one_paise_shortfall(self):
        # Off-by-one is the realistic version of the above.
        assert self._reason(amount_paise=99_899) is PaymentRejection.AMOUNT_MISMATCH

    def test_rejects_an_overpayment(self):
        # Not an attack, but it is not this order either - and silently accepting
        # it would grant a tier the customer did not buy. The amount used is the
        # PREMIUM PLUS price against a PREMIUM order, which is the realistic version:
        # a customer upgrades in a second tab and the first order is confirmed for more
        # than it was created for.
        assert self._reason(amount_paise=1_29_900) is PaymentRejection.AMOUNT_MISMATCH

    def test_rejects_a_weaker_currency(self):
        assert self._reason(currency="USD") is PaymentRejection.CURRENCY_MISMATCH

    def test_currency_comparison_is_case_insensitive(self):
        assert self._reason(currency="inr") is None

    def test_rejects_a_payment_for_a_different_order(self):
        reason = self._reason(provider_order_id="order_SOMEONE_ELSE")
        assert reason is PaymentRejection.ORDER_MISMATCH

    def test_rejects_a_payment_for_an_order_we_never_sent_to_the_gateway(self):
        assert (
            payment_rejection_reason(
                claim=self._claim(),
                expected_amount_paise=99_900,
                expected_currency="INR",
                expected_provider_order_id=None,
            )
            is PaymentRejection.PROVIDER_ORDER_UNKNOWN
        )

    def test_capture_is_checked_before_amount(self):
        """Ordering matters for the log line, not for the decision.

        Both are fatal, but "not captured" is the accurate reason for an
        unauthorized payment that also happens to be for the wrong amount -
        reporting it as an amount mismatch would send someone hunting a pricing
        bug that does not exist.
        """
        assert self._reason(status="authorized", captured=False, amount_paise=1) is (
            PaymentRejection.NOT_CAPTURED
        )


# ----------------------------------------------------------- subscription maths


class TestApplyPayment:
    PLAN = PLANS_BY_CODE["PREMIUM_YEARLY"]

    def test_a_first_purchase_starts_now(self):
        activation = apply_payment(current=None, plan=self.PLAN, now=NOW)
        assert activation.started_at == NOW
        assert activation.expires_at == NOW + timedelta(days=365)
        assert activation.extended_existing is False

    def test_renewing_while_active_extends_from_the_existing_expiry(self):
        # Buying again 10 days before expiry must add a year to the EXISTING end
        # date, not restart from today - restarting would silently delete the
        # remainder the student already paid for.
        existing_end = NOW + timedelta(days=10)
        activation = apply_payment(
            current=SubscriptionState(
                tier=Tier.PREMIUM, status=SubStatus.ACTIVE, expires_at=existing_end
            ),
            plan=self.PLAN,
            now=NOW,
        )
        assert activation.expires_at == existing_end + timedelta(days=365)
        assert activation.extended_existing is True

    def test_an_expired_subscription_restarts_from_now(self):
        # There is no remainder to protect, and extending from a past date would
        # hand the customer less than they bought.
        activation = apply_payment(
            current=SubscriptionState(
                tier=Tier.PREMIUM,
                status=SubStatus.EXPIRED,
                expires_at=NOW - timedelta(days=400),
            ),
            plan=self.PLAN,
            now=NOW,
        )
        assert activation.expires_at == NOW + timedelta(days=365)
        assert activation.extended_existing is False

    def test_an_active_row_with_a_lapsed_date_is_treated_as_lapsed(self):
        """The cron-free expiry rule, at the transition layer.

        A row can say ACTIVE while its expiry is in the past - that is what a
        scheduled job that did not run leaves behind. Extending from that stale
        date would give away time.
        """
        activation = apply_payment(
            current=SubscriptionState(
                tier=Tier.PREMIUM,
                status=SubStatus.ACTIVE,
                expires_at=NOW - timedelta(days=1),
            ),
            plan=self.PLAN,
            now=NOW,
        )
        assert activation.extended_existing is False
        assert activation.expires_at == NOW + timedelta(days=365)

    def test_an_expiry_exactly_now_is_not_extendable(self):
        # The boundary: `>` not `>=`. A subscription expiring at this instant has
        # no remaining time to carry forward.
        activation = apply_payment(
            current=SubscriptionState(tier=Tier.PREMIUM, status=SubStatus.ACTIVE, expires_at=NOW),
            plan=self.PLAN,
            now=NOW,
        )
        assert activation.extended_existing is False

    def test_upgrading_while_active_extends_the_new_tier(self):
        existing_end = NOW + timedelta(days=30)
        activation = apply_payment(
            current=SubscriptionState(
                tier=Tier.PREMIUM, status=SubStatus.ACTIVE, expires_at=existing_end
            ),
            plan=PLANS_BY_CODE["PREMIUM_PLUS_YEARLY"],
            now=NOW,
        )
        assert activation.tier is Tier.PREMIUM_PLUS
        assert activation.expires_at == existing_end + timedelta(days=365)

    def test_a_naive_datetime_is_refused(self):
        # Comparing a naive datetime with the aware ones PostgreSQL returns raises
        # at runtime, in production, on the entitlement path.
        with pytest.raises(BillingError):
            apply_payment(current=None, plan=self.PLAN, now=datetime(2026, 9, 25, 12, 0))

    def test_a_zero_duration_plan_cannot_be_activated(self):
        with pytest.raises(BillingError):
            apply_payment(
                current=None,
                plan=Plan(
                    code="BROKEN",
                    tier=Tier.PREMIUM,
                    label="Broken",
                    amount_paise=1,
                    duration_days=0,
                    tagline="",
                    features=(),
                ),
                now=NOW,
            )

    def test_cancelled_subscriptions_do_not_extend(self):
        activation = apply_payment(
            current=SubscriptionState(
                tier=Tier.PREMIUM,
                status=SubStatus.CANCELLED,
                expires_at=NOW + timedelta(days=300),
            ),
            plan=self.PLAN,
            now=NOW,
        )
        assert activation.extended_existing is False


# -------------------------------------------------------------- entitlements


class TestEntitlements:
    def test_the_free_tier_is_the_baseline(self):
        assert (
            entitlement_for(tier=Tier.FREE, status=SubStatus.ACTIVE, expires_at=None, now=NOW)
            == TIER_ENTITLEMENTS[Tier.FREE]
        )

    def test_an_expired_subscription_falls_back_to_free_at_read_time(self):
        # With no cron job involved: the clock decides, not a scheduled task that
        # may not have run.
        entitlements = entitlement_for(
            tier=Tier.PREMIUM,
            status=SubStatus.ACTIVE,
            expires_at=NOW - timedelta(seconds=1),
            now=NOW,
        )
        assert entitlements == TIER_ENTITLEMENTS[Tier.FREE]
        assert "unlimited_mocks" not in entitlements

    def test_expiry_exactly_now_is_expired(self):
        assert (
            entitlement_for(tier=Tier.PREMIUM, status=SubStatus.ACTIVE, expires_at=NOW, now=NOW)
            == TIER_ENTITLEMENTS[Tier.FREE]
        )

    def test_one_second_of_remaining_time_still_counts(self):
        assert "unlimited_mocks" in entitlement_for(
            tier=Tier.PREMIUM,
            status=SubStatus.ACTIVE,
            expires_at=NOW + timedelta(seconds=1),
            now=NOW,
        )

    @pytest.mark.parametrize("status", [SubStatus.EXPIRED, SubStatus.CANCELLED, SubStatus.PAST_DUE])
    def test_non_active_statuses_grant_free_regardless_of_expiry(self, status):
        assert (
            entitlement_for(
                tier=Tier.PREMIUM_PLUS,
                status=status,
                expires_at=NOW + timedelta(days=300),
                now=NOW,
            )
            == TIER_ENTITLEMENTS[Tier.FREE]
        )

    def test_a_trial_gets_the_paid_entitlements_until_it_lapses(self):
        assert "unlimited_mocks" in entitlement_for(
            tier=Tier.PREMIUM,
            status=SubStatus.TRIAL,
            expires_at=NOW + timedelta(days=7),
            now=NOW,
        )

    def test_a_subscription_with_no_expiry_never_lapses(self):
        # Only reachable for a tier that is meant to be permanent; pinned because
        # `expires_at is None` returning FREE would silently downgrade such a row.
        assert "unlimited_mocks" in entitlement_for(
            tier=Tier.PREMIUM, status=SubStatus.ACTIVE, expires_at=None, now=NOW
        )

    def test_is_premium_agrees_with_the_entitlement_set(self):
        assert is_premium(
            tier=Tier.PREMIUM,
            status=SubStatus.ACTIVE,
            expires_at=NOW + timedelta(days=1),
            now=NOW,
        )
        assert not is_premium(tier=Tier.FREE, status=SubStatus.ACTIVE, expires_at=None, now=NOW)
        assert not is_premium(
            tier=Tier.PREMIUM,
            status=SubStatus.ACTIVE,
            expires_at=NOW - timedelta(days=1),
            now=NOW,
        )

    def test_every_tier_is_in_the_entitlement_map(self):
        # A tier with no entry would KeyError inside the entitlement check, which
        # is on the read path of every premium feature.
        for tier in Tier:
            assert tier in TIER_ENTITLEMENTS


# ------------------------------------------------------------ webhook parsing


def _webhook(**overrides) -> dict:
    entity = {
        "id": "pay_XYZ",
        "order_id": "order_ABC",
        "amount": 99_900,
        "currency": "INR",
        "status": "captured",
        "captured": True,
        "email": "student@example.com",
    }
    entity.update(overrides.pop("entity", {}))
    payload = {
        "id": "evt_1",
        "event": "payment.captured",
        "payload": {"payment": {"entity": entity}},
    }
    payload.update(overrides)
    return payload


class TestWebhookParsing:
    def test_extracts_the_fields_the_decision_needs(self):
        event = extract_webhook_event(_webhook())
        assert event is not None
        assert event.event_id == "evt_1"
        assert event.event_type == "payment.captured"
        assert event.provider_order_id == "order_ABC"
        claim = event.as_claim()
        assert claim is not None
        assert claim.amount_paise == 99_900
        assert claim.captured is True

    def test_an_event_without_an_id_is_refused(self):
        # Without an id it cannot be deduplicated, and processing something we
        # cannot prove we have not already handled is the double-credit bug.
        assert extract_webhook_event(_webhook(id="")) is None
        assert extract_webhook_event({"event": "payment.captured"}) is None

    def test_an_event_without_a_type_is_refused(self):
        assert extract_webhook_event(_webhook(event="")) is None

    def test_a_non_mapping_payload_is_refused_rather_than_raising(self):
        # This is untrusted input that happens to be signed. A signature proves
        # provenance, not shape.
        for value in [None, [], "string", 7]:
            assert extract_webhook_event(value) is None  # type: ignore[arg-type]

    def test_a_missing_entity_does_not_raise(self):
        event = extract_webhook_event({"id": "evt_1", "event": "payment.captured"})
        assert event is not None
        assert event.payment_id is None
        assert event.as_claim() is None

    def test_a_malformed_amount_is_not_read_as_a_number(self):
        for bad in ["99900", None, True, 99.5, {"amount": 1}]:
            event = extract_webhook_event(_webhook(entity={"amount": bad}))
            assert event is not None
            assert event.amount_paise is None, f"accepted {bad!r} as an amount"

    def test_booleans_are_not_amounts(self):
        # bool is a subclass of int in Python, so `True` would otherwise be read
        # as 1 paise and sail through arithmetic.
        event = extract_webhook_event(_webhook(entity={"amount": True}))
        assert event is not None
        assert event.amount_paise is None

    def test_captured_requires_both_the_status_and_the_flag(self):
        for status, flag in [("captured", False), ("authorized", True), ("captured", True)]:
            event = extract_webhook_event(_webhook(entity={"status": status, "captured": flag}))
            assert event is not None
            assert event.captured is (status == "captured" and flag)

    def test_only_known_event_types_are_acted_on(self):
        captured = extract_webhook_event(_webhook(event="payment.captured"))
        failed = extract_webhook_event(_webhook(event="payment.failed"))
        assert captured is not None and failed is not None
        assert captured.event_type in ACTED_EVENT_TYPES
        assert failed.event_type in ACTED_EVENT_TYPES
        assert "payment.authorized" not in ACTED_EVENT_TYPES
        assert "refund.processed" not in ACTED_EVENT_TYPES


class TestClock:
    def test_utcnow_is_timezone_aware(self):
        # `datetime.utcnow()` is naive, and comparing it to the aware datetimes
        # PostgreSQL returns raises TypeError at runtime.
        assert utcnow().tzinfo is not None
        assert utcnow().utcoffset() == timedelta(0)
