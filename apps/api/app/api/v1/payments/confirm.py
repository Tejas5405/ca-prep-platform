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
from app.repositories.billing import SqlBillingStore
from app.services.billing import (
    verify_checkout_signature,
)
from app.services.gateway_config import (
    missing_for_checkout,
    resolved_payment_settings,
)

from ._shared import (
    ConfirmPaymentIn,
    _confirm_payment,
    _not_configured,
    _user_not_found,
    get_billing_user,
)

router = APIRouter(tags=["payments"])

"""Client-side payment confirmation."""


@router.post("/payments/confirm", summary="Confirm a Checkout result from the browser")
async def confirm_payment(
    payload: ConfirmPaymentIn,
    session: AsyncSession = Depends(get_db),
    user_id: uuid.UUID | None = Depends(get_billing_user),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Confirm a payment the browser says just happened.

    The signature proves Razorpay produced these three values together. It does
    NOT prove the payment is captured, nor that it is for the right amount - which
    is why the shared path still reads the payment back and compares it.
    """
    settings = await resolved_payment_settings(session, settings)
    if not settings.payments_enabled():
        return _not_configured(missing_for_checkout(settings))
    if user_id is None:
        return _user_not_found()

    store = SqlBillingStore(session)
    order = await store.order_for_user(order_id=payload.order_id, user_id=user_id)
    if order is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Order not found",
            detail="No such order for this account.",
            type_slug="order",
        )

    # The signature is recomputed over the GATEWAY order id, because that is what
    # Razorpay signed. An order that never reached the gateway has no gateway id,
    # so it cannot be confirmed at all - correct, since nothing was paid.
    if not order.provider_order_id or not verify_checkout_signature(
        order_id=order.provider_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
        key_secret=settings.razorpay_key_secret or "",
    ):
        return problem(
            status=status.HTTP_400_BAD_REQUEST,
            title="Invalid signature",
            detail="This payment could not be verified.",
            type_slug="payments",
        )

    result = await _confirm_payment(
        store=store,
        session=session,
        settings=settings,
        order=order,
        payment_id=payload.razorpay_payment_id,
        # Deterministic for the browser path: the payment id IS the event, so the
        # callback and any later webhook for the same payment collide on one
        # idempotency key instead of extending twice.
        event_id=f"checkout:{payload.razorpay_payment_id}",
        event_type="payment.confirmed_by_client",
        event_payload={"source": "client_callback", "paymentId": payload.razorpay_payment_id},
    )

    if "problem" in result:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Payment not confirmed",
            detail=(
                f"The payment could not be confirmed ({result.get('reason', result['problem'])})."
            ),
            type_slug="payments",
        )

    return success(result, request_id=get_request_id())
