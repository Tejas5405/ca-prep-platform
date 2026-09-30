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

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.repositories.billing import SqlBillingStore
from app.services.billing import (
    ACTED_EVENT_TYPES,
    extract_webhook_event,
    verify_webhook_signature,
)
from app.services.gateway_config import (
    resolved_payment_settings,
)

from ._shared import (
    SIGNATURE_HEADER,
    _confirm_payment,
    logger,
)

router = APIRouter(tags=["payments"])

"""The Razorpay webhook. Meters, and fails CLOSED when Redis is down."""


@router.post("/webhooks/razorpay", summary="Receive Razorpay webhook events")
async def razorpay_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Process a signed payment event.

    THE BODY IS READ AS BYTES AND IS NEVER RE-SERIALISED FOR VERIFICATION.

    The signature covers the exact bytes Razorpay sent. FastAPI's parsed body is a
    different artefact - key order, whitespace and unicode escaping are all lost -
    so re-serialising it produces a different byte sequence and the HMAC cannot
    match. At that point the tempting conclusion is that verification "does not
    work" and should be dropped. Hence ``await request.body()`` and no ``Body(...)``
    parameter on this handler.

    RESPONSE CODES ARE PART OF THE CONTRACT

    * 200 for anything processed, INCLUDING a duplicate. A 4xx on a duplicate
      makes the provider retry a delivery that can never succeed.
    * 401 for a bad or missing signature. Retrying will not help, and it is the
      one signal that something is actively wrong.
    * 200 for an event type this service does not act on: an error there causes an
      infinite retry loop for a genuine event.
    """
    raw_body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER, "")
    settings = await resolved_payment_settings(session, settings)

    if not verify_webhook_signature(
        raw_body=raw_body,
        signature=signature,
        webhook_secret=settings.razorpay_webhook_secret or "",
    ):
        # Warning, but WITHOUT the body or the signature: an attacker probing for a
        # bypass should not have their bytes written into our logs, and a signature
        # is a credential-shaped string.
        logger.warning("Rejected Razorpay webhook: signature verification failed")
        return problem(
            status=status.HTTP_401_UNAUTHORIZED,
            title="Invalid signature",
            detail="Webhook signature verification failed.",
            type_slug="webhook",
        )

    try:
        parsed_body = await request.json()
    except ValueError:
        return problem(
            status=status.HTTP_400_BAD_REQUEST,
            title="Malformed payload",
            detail="Webhook body was not valid JSON.",
            type_slug="webhook",
        )

    event = extract_webhook_event(parsed_body)
    if event is None:
        # No event id means it cannot be deduplicated, and processing something we
        # cannot prove we have not already handled is worse than refusing it.
        return problem(
            status=status.HTTP_400_BAD_REQUEST,
            title="Malformed event",
            detail="Webhook did not carry a usable event id.",
            type_slug="webhook",
        )

    store = SqlBillingStore(session)

    async def archive_only(reason: str, *, user_id: uuid.UUID | None = None) -> Any:
        """Record an event we are not acting on, and answer 200.

        Used for the three cases that are genuine, authenticated and not
        actionable: an event type we ignore, an event with no order reference, and
        an event for an order we do not have. All three are archived so a human can
        reconcile later, and none of them is an error the provider should retry.
        """
        await store.record_event(
            provider="razorpay",
            event_id=event.event_id,
            event_type=event.event_type,
            payload=parsed_body,
            signature_verified=True,
            user_id=user_id,
        )
        await store.mark_event_processed(event.event_id)
        await session.commit()
        return success(
            {"received": True, "handled": False, "reason": reason},
            request_id=get_request_id(),
        )

    if event.event_type not in ACTED_EVENT_TYPES:
        return await archive_only("event_type_ignored")

    claim = event.as_claim()
    if claim is None or event.provider_order_id is None:
        # A payment event with no order cannot be matched to an entitlement.
        return await archive_only("missing_order_reference")

    order = await store.order_by_provider_order_id(event.provider_order_id)
    if order is None:
        return await archive_only("order_not_found")

    if event.event_type == "payment.failed":
        is_new = await store.record_event(
            provider="razorpay",
            event_id=event.event_id,
            event_type=event.event_type,
            payload=parsed_body,
            signature_verified=True,
            user_id=order.user_id,
        )
        if is_new:
            await store.mark_order_failed(
                order, reason=f"provider status {event.status or 'unknown'}"
            )
        await store.mark_event_processed(event.event_id)
        await session.commit()
        return success(
            {"received": True, "handled": True, "orderId": str(order.id)},
            request_id=get_request_id(),
        )

    result = await _confirm_payment(
        store=store,
        session=session,
        settings=settings,
        order=order,
        payment_id=claim.payment_id,
        event_id=event.event_id,
        event_type=event.event_type,
        event_payload=parsed_body,
    )

    if "problem" in result:
        # 200, not 5xx: the event was received and authenticated, and its problem
        # is recorded on the order. Retrying this exact delivery cannot fix a
        # rejected payment, and a retry storm is worse than an accurate log line.
        return success(
            {"received": True, "handled": False, "reason": result.get("reason", result["problem"])},
            request_id=get_request_id(),
        )

    return success({"received": True, "handled": True, **result}, request_id=get_request_id())
