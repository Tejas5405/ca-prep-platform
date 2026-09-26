"""Billing domain: plans, signature verification and subscription transitions.

Blueprint v3 §13.1 specifies the Razorpay flow and, more importantly, the four
rules that make it safe:

    "Never activate a subscription based only on frontend success callbacks."
    "Persist provider payment/order identifiers for auditability."
    "Treat webhook handling as idempotent."
    "Keep plan price and entitlement rules server-side."

Every function in this module exists to enforce one of those. The module is pure -
no HTTP, no database, no clock of its own - so each rule is unit-testable without
a payment provider, a network or a running PostgreSQL.

THE ONE THING THAT GOES WRONG IN PAYMENT CODE

Money integrations fail in a small number of well-known ways, and they are all
here by name so the tests can aim at them:

  1. Trusting the client's amount. The amount is looked up from PLANS by plan
     code. A request never carries a price.
  2. Comparing signatures with ``==``. String comparison short-circuits on the
     first differing byte, which leaks the correct prefix through timing. Every
     comparison here uses ``hmac.compare_digest``.
  3. Verifying a signature over re-serialised JSON. The webhook signature covers
     the RAW request body; parsing and re-dumping it changes key order and
     whitespace and the HMAC no longer matches. Verification takes bytes.
  4. Processing a webhook twice. Providers retry. Idempotency is keyed on the
     provider's event id, in this module's return type and in the database.
  5. Granting an entitlement from a claim the user controls. Entitlements are
     derived from the subscription row and nothing else.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final


class Tier(StrEnum):
    """Must stay in sync with the ``ck_subscriptions_tier`` CHECK constraint."""

    FREE = "FREE"
    PREMIUM = "PREMIUM"
    PREMIUM_PLUS = "PREMIUM_PLUS"


class SubStatus(StrEnum):
    """Must stay in sync with the ``ck_subscriptions_status`` CHECK constraint."""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    PAST_DUE = "PAST_DUE"
    TRIAL = "TRIAL"


class OrderState(StrEnum):
    """Lifecycle of a payment order, before it becomes an entitlement."""

    CREATED = "CREATED"
    PAID = "PAID"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


CURRENCY: Final = "INR"

#: Razorpay settles in paise. Integers only - floating-point currency is how a
#: rounding error becomes a customer complaint and an unreconcilable ledger.
PAISE_PER_RUPEE: Final = 100


@dataclass(frozen=True, slots=True)
class Plan:
    """A purchasable plan.

    ``amount_paise`` is the authority for what a customer is charged. The frontend
    displays these same numbers for a pricing table, and a contract test asserts
    the two agree - but if they ever diverge, the server wins and the client's
    number is discarded, because the order is built here.
    """

    code: str
    tier: Tier
    label: str
    amount_paise: int
    duration_days: int
    tagline: str
    features: tuple[str, ...]
    #: True for the plan shown as the default choice in the UI.
    recommended: bool = False

    @property
    def amount_rupees(self) -> int:
        return self.amount_paise // PAISE_PER_RUPEE


# --------------------------------------------------------------------- prices
#
# WHERE THESE NUMBERS COME FROM, AND WHY THEY ARE NOT INVENTED
#
# Every price below is derived from the verified revenue table in the product
# review (`ca-prep/Feature_Stack_Alignment_Review.md`), which corrected the
# superseded deck's arithmetic:
#
#   base subscription   1,000 x Rs 999     = Rs 9,99,000
#   mock premium          300 x Rs 299     = Rs 89,700
#
# PREMIUM is the base plan at Rs 999/year - the figure the whole revenue model
# rests on.
#
# PREMIUM_PLUS IS Rs 1,299 - DECIDED BY THE OWNER, NOT DERIVED BY DEFAULT.
#
# The original bundle was 999 (base) + 500 (video add-on) + 299 (mock pack) = 1,798,
# sold at Rs 1,799. Video solutions were withdrawn from the product, leaving that price
# buying strictly less than it advertised. The owner reviewed the two honest endings -
# keep 1,799 for the mock pack and priority support, or fall to 1,299 for the arithmetic
# the remaining features support - and confirmed 1,299.
#
# The differentiators are now exactly the entitlement strings below: the exam-day mock
# pack and priority support. Every line of the plan's feature list maps to one of them,
# so the pricing page cannot promise more than the API grants.
#
# These remain a BUSINESS decision, not an engineering one. They are isolated in
# this one tuple so changing a price is a one-line change that no other code
# depends on: nothing else in the codebase hardcodes an amount.
#
# The AI assistant is deliberately NOT a SKU. The review found hosted-LLM cost at
# 9x negative margin on a Rs 999/year plan, and the stack amendment approving it
# (SA-05) attaches a hard monthly spend ceiling as a condition. Selling it before
# that ceiling exists would be selling a loss.
PLANS: Final[tuple[Plan, ...]] = (
    Plan(
        code="FREE",
        tier=Tier.FREE,
        label="Free",
        amount_paise=0,
        duration_days=0,
        tagline="Everything you need to sit one group properly.",
        features=(
            "Full ICAI past-paper bank with source references",
            "Dated study plan for one group",
            "Spaced-repetition revision queue",
        ),
    ),
    Plan(
        code="PREMIUM_YEARLY",
        tier=Tier.PREMIUM,
        label="Premium",
        amount_paise=99_900,
        duration_days=365,
        tagline="Both groups, unlimited mocks, and progress projections.",
        features=(
            "Everything in Free",
            "Both groups planned together, tracked apart",
            "Unlimited timed mock exams with ICAI-pattern marking",
            "Progress projections and weak-topic breakdown",
        ),
        recommended=True,
    ),
    Plan(
        code="PREMIUM_PLUS_YEARLY",
        tier=Tier.PREMIUM_PLUS,
        label="Premium Plus",
        amount_paise=1_29_900,
        duration_days=365,
        tagline="Premium plus the exam-day mock pack and priority support.",
        # Every line here maps to a string in TIER_ENTITLEMENTS below. A feature list
        # that promises more than the entitlement map grants is how a customer ends up
        # paying for something the API refuses them.
        features=(
            "Everything in Premium",
            "Exam-day mock pack with full analysis",
            "Priority support",
        ),
    ),
)

PLANS_BY_CODE: Final[dict[str, Plan]] = {plan.code: plan for plan in PLANS}

#: Plans a user can actually be charged for. FREE is a tier, not a purchase, and
#: accepting it as an order code would create a zero-value order that a webhook
#: could activate - which is a free upgrade with extra steps.
PURCHASABLE_CODES: Final[frozenset[str]] = frozenset(
    plan.code for plan in PLANS if plan.amount_paise > 0
)

#: What a tier unlocks. Entitlement checks read this map, never the request.
TIER_ENTITLEMENTS: Final[dict[Tier, frozenset[str]]] = {
    Tier.FREE: frozenset({"past_papers", "planner_single_group", "spaced_repetition"}),
    Tier.PREMIUM: frozenset(
        {
            "past_papers",
            "planner_single_group",
            "spaced_repetition",
            "planner_both_groups",
            "unlimited_mocks",
            "progress_projections",
        }
    ),
    Tier.PREMIUM_PLUS: frozenset(
        {
            "past_papers",
            "planner_single_group",
            "spaced_repetition",
            "planner_both_groups",
            "unlimited_mocks",
            "progress_projections",
            "mock_exam_pack",
            "priority_support",
        }
    ),
}


class BillingError(ValueError):
    """Raised for a nil/unknown plan code or an unusable amount."""


def plan_for(code: str) -> Plan:
    """Look up a purchasable plan.

    Rejects the free tier as well as unknown codes: a zero-amount order that a
    webhook could activate is an entitlement bug, not a free trial.
    """
    plan = PLANS_BY_CODE.get(code)
    if plan is None:
        raise BillingError(f"unknown plan code: {code!r}")
    if plan.code not in PURCHASABLE_CODES:
        raise BillingError(f"plan {code!r} is not purchasable")
    return plan


def public_catalogue() -> list[dict[str, Any]]:
    """The pricing table, as the client sees it.

    Amounts are included for display only. The client is told what something
    costs; it is never asked.
    """
    return [
        {
            "code": plan.code,
            "tier": plan.tier.value,
            "label": plan.label,
            "amountPaise": plan.amount_paise,
            "amountRupees": plan.amount_rupees,
            "currency": CURRENCY,
            "durationDays": plan.duration_days,
            "tagline": plan.tagline,
            "features": list(plan.features),
            "recommended": plan.recommended,
        }
        for plan in PLANS
    ]


# ------------------------------------------------------- signature verification


def _hmac_hex(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_checkout_signature(
    *,
    order_id: str,
    payment_id: str,
    signature: str,
    key_secret: str,
) -> bool:
    """Verify the ``razorpay_signature`` returned to the browser by Checkout.

    Razorpay signs ``"{order_id}|{payment_id}"`` with the account key secret. This
    proves the pair came from Razorpay, but NOT that the payment succeeded and NOT
    that it is for the right amount - so it is a sufficient gate for "a checkout
    happened", never for "grant access". The caller must additionally fetch the
    payment from Razorpay's API and check status, amount and order ownership.
    """
    if not (order_id and payment_id and signature and key_secret):
        return False
    expected = _hmac_hex(key_secret, f"{order_id}|{payment_id}".encode())
    # compare_digest, not ==: a plain comparison returns at the first differing
    # byte, so response timing reveals how much of the signature was guessed.
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(*, raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    """Verify the ``X-Razorpay-Signature`` header over the RAW request body.

    ``raw_body`` is bytes on purpose. FastAPI has already parsed the JSON by the
    time a handler runs, and re-serialising that object does not reproduce the
    original bytes - key order, spacing and unicode escaping all differ - so the
    HMAC would never match and the "fix" would be to skip verification. Read the
    body with ``await request.body()`` and pass it here untouched.

    Fails closed on an empty secret: an unset webhook secret must reject events,
    not accept everything. That failure mode is a public endpoint that grants
    paid entitlements to anyone who can POST.
    """
    if not webhook_secret:
        return False
    if not signature:
        return False
    expected = _hmac_hex(webhook_secret, raw_body)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------- order identifiers


def build_receipt(user_id: uuid.UUID, plan_code: str, nonce: str) -> str:
    """Build a deterministic, opaque receipt id for a Razorpay order.

    Razorpay requires ``receipt`` to be unique per order and at most 40
    characters, and it is echoed back in the webhook. It carries no PII beyond a
    truncated user prefix - a receipt that leaks an email address ends up in
    support tickets and log aggregators.

    The nonce is supplied by the caller (a UUID) so this function stays pure and
    testable; the same inputs always produce the same receipt.
    """
    digest = hashlib.sha256(f"{user_id}:{plan_code}:{nonce}".encode()).hexdigest()
    return f"rcpt_{digest[:32]}"


def receipt_user_prefix(receipt: str) -> str | None:
    """Extract the opaque user prefix from a receipt, for reconciliation.

    Returns None for a string this service did not mint. Used only for logging -
    never for authorization, because a client-supplied receipt must not be able to
    name its own owner.
    """
    if not receipt.startswith("rcpt_") or len(receipt) != len("rcpt_") + 32:
        return None
    return receipt[5:13]


# ----------------------------------------------------- subscription transitions


@dataclass(frozen=True, slots=True)
class SubscriptionState:
    """The bits of a subscription row that transitions depend on."""

    tier: Tier
    status: SubStatus
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Activation:
    """The result of applying a successful payment to a subscription."""

    tier: Tier
    status: SubStatus
    started_at: datetime
    expires_at: datetime
    #: True when an existing unexpired subscription was extended rather than a
    #: new one created. Drives which SQL path runs.
    extended_existing: bool


def apply_payment(
    *,
    current: SubscriptionState | None,
    plan: Plan,
    now: datetime,
    payment_id: str | None = None,
) -> Activation:
    """Work out the new subscription state for a confirmed payment.

    RENEWAL SEMANTICS, STATED SO THEY CANNOT DRIFT

    Buying again while still subscribed EXTENDS from the existing expiry rather
    than from today. Starting the new term at ``now`` would silently delete the
    remainder of the term the student already paid for, which is the kind of bug
    that is only noticed by the customer who lost three months.

    An EXPIRED or CANCELLED subscription restarts from ``now`` - there is no
    remainder to protect, and extending from a past expiry would grant less than
    the customer bought.

    ``payment_id`` is accepted for audit logging by the caller and deliberately
    not used in the arithmetic: an entitlement must never depend on a provider
    string being well-formed.
    """
    if now.tzinfo is None:
        raise BillingError("apply_payment requires a timezone-aware datetime")

    duration = timedelta(days=plan.duration_days)
    if duration <= timedelta(0):
        raise BillingError(f"plan {plan.code!r} has no duration and cannot be activated")

    still_valid = (
        current is not None
        and current.status in (SubStatus.ACTIVE, SubStatus.TRIAL)
        and current.expires_at is not None
        and current.expires_at > now
    )

    if still_valid:
        # mypy needs the narrowing restated; the condition above guarantees it.
        assert current is not None and current.expires_at is not None
        return Activation(
            tier=plan.tier,
            status=SubStatus.ACTIVE,
            started_at=now,
            expires_at=current.expires_at + duration,
            extended_existing=True,
        )

    return Activation(
        tier=plan.tier,
        status=SubStatus.ACTIVE,
        started_at=now,
        expires_at=now + duration,
        extended_existing=False,
    )


def entitlement_for(
    *,
    tier: Tier,
    status: SubStatus,
    expires_at: datetime | None,
    now: datetime,
) -> frozenset[str]:
    """Resolve what a user may do right now.

    THE EXPIRY IS CHECKED HERE, NOT BY A CRON

    A nightly job that flips expired rows to EXPIRED is fine for reporting and
    terrible as the entitlement mechanism: if the job does not run, every expired
    subscription keeps working. Reading the clock at check time means an expired
    subscription is inert the second it expires, whether or not any job ran.

    A row whose status says ACTIVE but whose expiry is in the past is treated as
    expired. The database is the record; this function is the decision.
    """
    if status not in (SubStatus.ACTIVE, SubStatus.TRIAL):
        return TIER_ENTITLEMENTS[Tier.FREE]
    if expires_at is not None and expires_at <= now:
        return TIER_ENTITLEMENTS[Tier.FREE]
    return TIER_ENTITLEMENTS[tier]


def is_premium(
    *, tier: Tier, status: SubStatus, expires_at: datetime | None, now: datetime
) -> bool:
    return (
        entitlement_for(tier=tier, status=status, expires_at=expires_at, now=now)
        != (TIER_ENTITLEMENTS[Tier.FREE])
    )


class PaymentRejection(StrEnum):
    """Why a claimed payment was refused.

    An enum rather than a message so the reason can be logged, counted and
    asserted on. "Payment rejected" with a free-text reason is impossible to alert
    on, and a spike in one specific reason is exactly the signal worth alerting on.
    """

    NOT_CAPTURED = "not_captured"
    AMOUNT_MISMATCH = "amount_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    ORDER_MISMATCH = "order_mismatch"
    PROVIDER_ORDER_UNKNOWN = "provider_order_unknown"


@dataclass(frozen=True, slots=True)
class PaymentClaim:
    """What the gateway says about a payment, as plain values.

    Deliberately primitive rather than the integration's dataclass: the decision
    below is the security-critical part of this codebase, and it should be
    testable by constructing four numbers, not by standing up an HTTP mock.
    """

    payment_id: str
    provider_order_id: str | None
    amount_paise: int
    currency: str
    status: str
    captured: bool


def payment_rejection_reason(
    *,
    claim: PaymentClaim,
    expected_amount_paise: int,
    expected_currency: str,
    expected_provider_order_id: str | None,
) -> PaymentRejection | None:
    """Decide whether a claimed payment may activate an entitlement.

    Returns None when the payment is acceptable, otherwise the reason to refuse.
    The caller must refuse on any non-None value - there is no "log and continue"
    option, and the shape of this function (a single reason or None) is meant to
    make that impossible to fudge.

    CHECKS, AND WHAT EACH ONE ACTUALLY BUYS

    * NOT_CAPTURED - Razorpay reports ``authorized`` while funds are only held.
      An authorisation can be reversed, so treating it as payment hands out
      access to money that may not arrive. Only ``captured`` activates.

    * AMOUNT_MISMATCH - compared against the amount recorded on OUR order when it
      was created, not against the current plan price. This is what stops a
      client from paying for the cheap plan and claiming the expensive one: the
      order already knows what was bought, and a payment for a different figure is
      not the payment this order is waiting for.

    * CURRENCY_MISMATCH - a 999 figure in a weaker currency is not Rs 999. Cheap
      to check, and the failure mode is expensive.

    * ORDER_MISMATCH / PROVIDER_ORDER_UNKNOWN - the payment must belong to the
      order being fulfilled. Without this, a valid payment for a different order
      could be replayed against any pending order belonging to anyone, which is
      the horizontal-escalation version of this bug.
    """
    if not claim.captured or claim.status != "captured":
        return PaymentRejection.NOT_CAPTURED

    if claim.amount_paise != expected_amount_paise:
        return PaymentRejection.AMOUNT_MISMATCH

    if claim.currency.upper() != expected_currency.upper():
        return PaymentRejection.CURRENCY_MISMATCH

    if expected_provider_order_id is None:
        return PaymentRejection.PROVIDER_ORDER_UNKNOWN

    if claim.provider_order_id != expected_provider_order_id:
        return PaymentRejection.ORDER_MISMATCH

    return None


# --------------------------------------------------------- webhook payloads

#: Event types this service acts on. Anything else is archived and ignored, which
#: is deliberate: an unknown event type is not an error, and returning 4xx for one
#: would make the provider retry it forever.
ACTED_EVENT_TYPES: Final[frozenset[str]] = frozenset({"payment.captured", "payment.failed"})


@dataclass(frozen=True, slots=True)
class ParsedWebhook:
    """The parts of a webhook this service reads, pulled out of the payload."""

    event_id: str
    event_type: str
    payment_id: str | None
    provider_order_id: str | None
    amount_paise: int | None
    currency: str | None
    status: str | None
    captured: bool
    email: str | None

    def as_claim(self) -> PaymentClaim | None:
        """Turn this event into a claim, if it carries enough to be one."""
        if self.payment_id is None or self.amount_paise is None:
            return None
        return PaymentClaim(
            payment_id=self.payment_id,
            provider_order_id=self.provider_order_id,
            amount_paise=self.amount_paise,
            currency=self.currency or "",
            status=self.status or "",
            captured=self.captured,
        )


def extract_webhook_event(payload: Mapping[str, Any]) -> ParsedWebhook | None:
    """Pull the fields this service needs out of a Razorpay webhook body.

    Returns None when the payload has no event id - an event that cannot be
    deduplicated must not be processed, because processing it twice is then
    unavoidable.

    Razorpay nests the payment under ``payload.payment.entity``. Every field is
    read defensively: this is untrusted input that happens to be correctly signed,
    and a signature proves provenance, not shape. A missing key must produce a
    clean "cannot process" rather than a KeyError in the webhook path, where a 500
    causes the provider to retry the same broken payload indefinitely.
    """
    if not isinstance(payload, Mapping):
        return None

    event_id = payload.get("id")
    event_type = payload.get("event")
    if not isinstance(event_id, str) or not event_id:
        return None
    if not isinstance(event_type, str) or not event_type:
        return None

    entity: Mapping[str, Any] = {}
    body = payload.get("payload")
    if isinstance(body, Mapping):
        payment = body.get("payment")
        if isinstance(payment, Mapping):
            nested = payment.get("entity")
            if isinstance(nested, Mapping):
                entity = nested

    def _str(key: str) -> str | None:
        value = entity.get(key)
        return value if isinstance(value, str) else None

    def _int(key: str) -> int | None:
        value = entity.get(key)
        # bool is a subclass of int in Python, and `True` as an amount would be
        # silently accepted as 1 paise without this explicit rejection.
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    extra = payload.get("payload")
    notes: Mapping[str, Any] = {}
    if isinstance(extra, Mapping):
        payment = extra.get("payment")
        if isinstance(payment, Mapping):
            nested = payment.get("entity")
            if isinstance(nested, Mapping):
                raw_notes = nested.get("notes")
                if isinstance(raw_notes, Mapping):
                    notes = raw_notes

    status = _str("status")
    return ParsedWebhook(
        event_id=event_id,
        event_type=event_type,
        payment_id=_str("id"),
        provider_order_id=_str("order_id"),
        amount_paise=_int("amount"),
        currency=_str("currency"),
        status=status,
        captured=status == "captured" and bool(entity.get("captured", False)),
        email=_str("email")
        or (notes.get("email") if isinstance(notes.get("email"), str) else None),
    )


def utcnow() -> datetime:
    """Timezone-aware now.

    ``datetime.utcnow()`` returns a naive datetime, and comparing a naive datetime
    to the aware datetimes PostgreSQL returns raises ``TypeError`` at runtime. This
    is a one-line function so that mistake has exactly one place to hide.
    """
    return datetime.now(UTC)
