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

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import success
from app.services.plan_overrides import resolved_catalogue

router = APIRouter(tags=["payments"])

"""The public plan catalogue."""


@router.get("/payments/plans", summary="List purchasable plans")
async def list_plans(session: AsyncSession = Depends(get_db)) -> Any:
    """The pricing table. Public, and deliberately so - a pricing page behind an
    auth wall is a pricing page nobody reads before deciding to sign up.

    Amounts come from the same resolver the order route uses. A price an
    administrator saved is the price this page shows.
    """
    return success({"plans": await resolved_catalogue(session)}, request_id=get_request_id())
