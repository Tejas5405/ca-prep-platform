"""Payment endpoints: plan catalogue, orders, confirmation and the webhook.

Blueprint v3 §13.1 endpoints, with the flow it specifies:

    POST /api/v1/payments/order      create a Razorpay order
    POST /api/v1/webhooks/razorpay   process verified payment events

plus three this service needs and the blueprint leaves implicit: the plan
catalogue the pricing page renders, the current entitlement, and the confirmation
call the browser makes after Checkout closes.

=============================================================================
THE ACTIVATION RULE, AND WHY THE BROWSER IS ALLOWED TO TRIGGER ANYTHING
=============================================================================
§13.1 is explicit: "Never activate a subscription based only on frontend success
callbacks."

That word ONLY is doing the work, and both paths here respect it. There are two
ways a payment becomes known - the webhook, and the browser reopening after
Checkout - and both take the same route through ``_confirm_payment``:

    1. The claim is authenticated: an HMAC over the order/payment pair for the
       browser path, or over the raw webhook body for the provider path.
    2. The payment is READ BACK FROM RAZORPAY'S API. This is what makes step 1
       insufficient on its own, and it is not skippable.
    3. The figure and status are compared against the order THIS SERVICE created.
    4. The event is archived under its event id, and that archive is the gate.

So the browser's callback can only ever cause a confirmation the server
independently verifies; a forged callback dies at step 2. The webhook and the
callback racing is the normal case rather than an edge case, and step 4 is what
makes it harmless.

=============================================================================
WHY THESE ROUTES USE A REQUEST-SCOPED SESSION, UNLIKE EVERY OTHER ROUTER
=============================================================================
Ingestion commits each pipeline step separately, because a job runs for minutes
and holding a transaction that long blocks unrelated writes.

Confirming a payment is the opposite shape: archiving the event, marking the order
paid and activating the subscription must be ONE transaction. If the activation
fails after the event was archived, the next delivery is treated as a duplicate
and skipped - so the student has paid and has no entitlement, permanently, with no
error anywhere. That is why this module takes ``get_db`` and the others do not.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.envelope import problem
from app.core.security import Principal, get_current_principal
from app.integrations.razorpay import (
    RazorpayClient,
    RazorpayError,
)
from app.repositories.billing import SqlBillingStore
from app.repositories.users import SqlUserRepository
from app.schemas.base import StrictRequest, UuidRef
from app.services.billing import (
    BillingError,
    PaymentClaim,
    SubscriptionState,
    SubStatus,
    Tier,
    apply_payment,
    entitlement_for,
    payment_rejection_reason,
    utcnow,
)
from app.services.plan_overrides import resolve_plan

router = APIRouter(tags=["payments"])

"""Constants, logger, the billing-user dependency, the request models and
_confirm_payment. Unchanged; only relocated."""


logger = logging.getLogger(__name__)


#: How long an unpaid order stays usable. Checkout is normally closed within
#: minutes; a day is generous and keeps abandoned orders from accumulating.
ORDER_TTL = timedelta(hours=24)


#: Header Razorpay signs webhooks with.
SIGNATURE_HEADER = "X-Razorpay-Signature"


class CreateOrderIn(StrictRequest):
    """Request body for creating an order.

    NOTE WHAT IS ABSENT: no ``amount``, no ``currency``, no ``tier``. The client
    names a PLAN CODE and the price is looked up server-side (§13.1: "Keep plan
    price and entitlement rules server-side"). A request that can name its own
    price is the most common way a payment integration is exploited, and the fix is
    for the field not to exist rather than for a validator to reject it.
    """

    plan_code: str = Field(min_length=1, max_length=40)


class ConfirmPaymentIn(StrictRequest):
    """The values Razorpay Checkout hands back to the browser.

    ``order_id`` is the LOCAL order id. The gateway's id is never accepted from
    the client as a way to name an order: the local id is scoped to the
    authenticated user by construction, whereas a gateway id would let anyone who
    obtained one describe another user's pending order.
    """

    #: ``UuidRef``, not ``uuid.UUID``: under ``strict=True`` a bare UUID field
    #: refuses the string a JSON client sends, which makes the endpoint
    #: unreachable rather than strict.
    order_id: UuidRef
    razorpay_payment_id: str = Field(min_length=1, max_length=120)
    razorpay_signature: str = Field(min_length=1, max_length=256)


def _not_configured(missing: list[str] | None = None) -> Any:
    """503, not 500: this deployment cannot take payments; the request did not fail."""
    names = missing or ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"]
    return problem(
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
        title="Payments unavailable",
        detail=(
            "Payments are not enabled. Missing "
            + ", ".join(names)
            + ". Set those on the API, or enter them once under Admin → Payments. "
            "The key secret is never returned to the browser."
        ),
        type_slug="payments",
    )


def _user_not_found() -> Any:
    return problem(
        status=status.HTTP_404_NOT_FOUND,
        title="User not found",
        detail="No local profile exists for this account yet.",
        type_slug="user",
    )


async def get_billing_user(
    principal: Principal = Depends(get_current_principal),
) -> uuid.UUID | None:
    """Resolve the authenticated principal to a local ``users.id``.

    A dependency rather than a call inside each handler so the mapping from
    auth user id to platform user id has one definition, and so tests can override
    it without a database.

    Returns None rather than raising: this codebase answers errors with the
    RFC 7807 envelope from the handler, and a raised HTTPException would produce
    FastAPI's default ``{"detail": ...}`` body instead - two error shapes for one
    API is a client-side bug factory.
    """
    user = await SqlUserRepository().get_by_auth_id(principal.auth_user_id)
    return None if user is None else user.id


def _entitlement_payload(
    *, tier: Tier, sub_status: SubStatus, expires_at: Any, now: Any
) -> dict[str, Any]:
    entitlements = entitlement_for(tier=tier, status=sub_status, expires_at=expires_at, now=now)
    free = entitlement_for(tier=Tier.FREE, status=SubStatus.ACTIVE, expires_at=None, now=now)
    return {
        "tier": tier.value,
        "status": sub_status.value,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "entitlements": sorted(entitlements),
        # Derived HERE. The client is told what it may do; it does not decide.
        "isPremium": entitlements != free,
    }


async def _confirm_payment(
    *,
    store: SqlBillingStore,
    session: AsyncSession,
    settings: Settings,
    order: Any,
    payment_id: str,
    event_id: str,
    event_type: str,
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    """Verify, validate and activate. The single path both flows share.

    Ordering is the design:

      1. Read the payment back from Razorpay - the claim is not trusted.
      2. Compare it against the order: capture status, amount, currency, order id.
      3. Archive the event under its id; a replay stops here.
      4. Apply the payment and activate, in the caller's transaction.

    Step 3 comes after step 2 deliberately. Archiving first would let a fraudulent
    event occupy the idempotency key, so the genuine event arriving later would be
    discarded as a duplicate - a denial of service aimed at a paying customer.
    """
    async with RazorpayClient(settings, base_url=settings.razorpay_base_url) as gateway:
        try:
            payment = await gateway.fetch_payment(payment_id)
        except RazorpayError as exc:
            logger.error("Could not read payment %s back from Razorpay: %s", payment_id, exc)
            return {"problem": "gateway_unavailable"}

    rejection = payment_rejection_reason(
        claim=PaymentClaim(
            payment_id=payment.id,
            provider_order_id=payment.order_id,
            amount_paise=payment.amount_paise,
            currency=payment.currency,
            status=payment.status,
            captured=payment.captured,
        ),
        expected_amount_paise=order.amount_paise,
        expected_currency=order.currency,
        expected_provider_order_id=order.provider_order_id,
    )
    if rejection is not None:
        # The identifiers a human needs to reconcile, and NOT the raw payload: a
        # payment entity carries the payer's email and card details, and payment
        # logs get pasted into support tickets.
        logger.warning(
            "Payment rejected (%s) for order %s payment %s",
            rejection.value,
            order.id,
            payment_id,
        )
        # The rejected event is STILL archived, with its reason. §13.1 requires
        # provider identifiers to be persisted "for auditability", and "support
        # says the money left my account but I have no access" is unanswerable
        # without the payload the gateway actually sent. It is archived here,
        # AFTER the decision, and marked processed-with-error - so the audit trail
        # exists without a rejected event ever occupying an idempotency key a
        # genuine event might later need.
        await store.record_event(
            provider="razorpay",
            event_id=event_id,
            event_type=event_type,
            payload=event_payload,
            signature_verified=True,
            user_id=order.user_id,
        )
        await store.mark_order_failed(order, reason=f"rejected: {rejection.value}")
        await store.mark_event_processed(event_id, error=f"rejected: {rejection.value}")
        await session.commit()
        return {"problem": "rejected", "reason": rejection.value}

    is_new = await store.record_event(
        provider="razorpay",
        event_id=event_id,
        event_type=event_type,
        payload=event_payload,
        signature_verified=True,
        user_id=order.user_id,
    )
    activated = await store.mark_order_paid(
        order,
        provider_payment_id=payment.id,
        provider_status=payment.status,
        paid_at=utcnow(),
    )

    if not is_new or not activated:
        # The other path got here first - the webhook landed before the browser
        # callback, or the provider retried. Report the truth without extending a
        # second time.
        await session.commit()
        return {"alreadyProcessed": True, "orderId": str(order.id)}

    try:
        plan = await resolve_plan(session, order.plan_code)
    except BillingError as exc:
        # The payment is real but names a plan no longer in the catalogue. That is
        # a deployment mistake, not a user error: leave the order PAID for
        # reconciliation rather than activating some other tier.
        logger.error("Paid order %s names plan %s: %s", order.id, order.plan_code, exc)
        await session.commit()
        return {"problem": "unknown_plan"}

    current = await store.active_subscription(order.user_id)
    activation = apply_payment(
        current=None
        if current is None
        else SubscriptionState(
            tier=Tier(current.tier),
            status=SubStatus(current.status),
            expires_at=current.expires_at,
        ),
        plan=plan,
        now=utcnow(),
        payment_id=payment.id,
    )

    subscription = await store.activate(
        user_id=order.user_id,
        activation=activation,
        plan_code=plan.code,
        amount_paise=order.amount_paise,
        currency=order.currency,
        provider="razorpay",
    )
    await store.mark_event_processed(event_id)
    await session.commit()

    # §13.1 ends with "entitlement cache invalidated" and "confirmation email
    # event queued". Neither is built: entitlements are read from PostgreSQL on
    # every request, which is correct and merely slower, and the email needs
    # Resend. Both are recorded in the build audit rather than faked here.
    return {
        "orderId": str(order.id),
        "tier": subscription.tier,
        "expiresAt": subscription.expires_at.isoformat() if subscription.expires_at else None,
        "extended": activation.extended_existing,
    }
