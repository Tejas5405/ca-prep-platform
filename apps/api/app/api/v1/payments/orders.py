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

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.integrations.razorpay import (
    RazorpayClient,
    RazorpayError,
    RazorpayNotConfiguredError,
)
from app.repositories.billing import SqlBillingStore
from app.services.billing import (
    CURRENCY,
    BillingError,
    SubStatus,
    Tier,
    build_receipt,
    utcnow,
)
from app.services.gateway_config import (
    missing_for_checkout,
    resolved_payment_settings,
)
from app.services.plan_overrides import resolve_plan

from ._shared import (
    ORDER_TTL,
    CreateOrderIn,
    _entitlement_payload,
    _not_configured,
    _user_not_found,
    get_billing_user,
    logger,
)

router = APIRouter(tags=["payments"])

"""Reading the caller subscription and creating an order."""


@router.get("/payments/subscription", summary="Current subscription and entitlements")
async def get_subscription(
    session: AsyncSession = Depends(get_db),
    user_id: uuid.UUID | None = Depends(get_billing_user),
) -> Any:
    """What this user may do right now.

    Entitlements come from the subscription ROW, and the expiry is compared
    against the clock at read time, so a lapsed subscription is inert without
    waiting for any scheduled job to run.
    """
    if user_id is None:
        return _user_not_found()

    store = SqlBillingStore(session)
    now = utcnow()
    subscription = await store.active_subscription(user_id)
    if subscription is None:
        return success(
            _entitlement_payload(
                tier=Tier.FREE, sub_status=SubStatus.ACTIVE, expires_at=None, now=now
            ),
            request_id=get_request_id(),
        )

    return success(
        _entitlement_payload(
            tier=Tier(subscription.tier),
            sub_status=SubStatus(subscription.status),
            expires_at=subscription.expires_at,
            now=now,
        ),
        request_id=get_request_id(),
    )


@router.post(
    "/payments/order",
    status_code=status.HTTP_201_CREATED,
    summary="Create a Razorpay order for a plan",
)
async def create_order(
    payload: CreateOrderIn,
    session: AsyncSession = Depends(get_db),
    user_id: uuid.UUID | None = Depends(get_billing_user),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Create an order, priced by the server.

    ORDER OF OPERATIONS, WHICH MATTERS HERE

    The local order row is written BEFORE the gateway is called. If Razorpay were
    called first and this process died before persisting, a real order would exist
    on their side that this service cannot attribute to anyone - and its webhook
    would arrive naming an order nobody has heard of, which is not reconcilable
    without a human. Writing locally first means the worst case is an orphan row
    in CREATED state, which expires on its own.
    """
    settings = await resolved_payment_settings(session, settings)
    if not settings.payments_enabled():
        return _not_configured(missing_for_checkout(settings))
    if user_id is None:
        return _user_not_found()

    try:
        plan = await resolve_plan(session, payload.plan_code)
    except BillingError as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown plan",
            detail=str(exc),
            type_slug="plan",
        )

    store = SqlBillingStore(session)
    now = utcnow()
    receipt = build_receipt(user_id, plan.code, uuid.uuid4().hex)
    order = await store.create_order(
        user_id=user_id,
        plan_code=plan.code,
        tier=plan.tier,
        amount_paise=plan.amount_paise,
        currency=CURRENCY,
        receipt=receipt,
        expires_at=now + ORDER_TTL,
    )

    try:
        async with RazorpayClient(settings, base_url=settings.razorpay_base_url) as gateway:
            gateway_order = await gateway.create_order(
                amount_paise=plan.amount_paise,
                receipt=receipt,
                currency=CURRENCY,
                # Echoed back in the webhook, which helps when reconciling by
                # hand. Written as strings; the copy that comes back is NEVER used
                # for authorization.
                notes={"userId": str(user_id), "planCode": plan.code},
            )
            key_id = gateway.key_id
        await store.attach_provider_order(order, gateway_order.id)
        await session.commit()
    except RazorpayNotConfiguredError:
        # The keys were present a moment ago (checked above), so this is a race
        # with a config reload. Commit the order row: it is accurate, and it will
        # expire.
        await session.commit()
        return _not_configured()
    except RazorpayError as exc:
        await store.mark_order_failed(order, reason=str(exc))
        await session.commit()
        logger.error("Razorpay order creation failed: %s", exc)
        return problem(
            status=status.HTTP_502_BAD_GATEWAY,
            title="Payment provider error",
            detail="Could not start the payment. Try again shortly.",
            type_slug="payments",
        )

    return success(
        {
            "orderId": str(order.id),
            "planCode": plan.code,
            "amountPaise": plan.amount_paise,
            "amountRupees": plan.amount_rupees,
            "currency": CURRENCY,
            # The key ID is public by design - Checkout opens with it. The key
            # secret never leaves the server and has no accessor on the client.
            "providerKeyId": key_id,
            "providerOrderId": order.provider_order_id,
            "receipt": order.receipt,
        },
        request_id=get_request_id(),
    )
