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

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.user import User
from app.schemas.base import StrictRequest
from app.services.audit import AuditAction, record_audit
from app.services.gateway_config import (
    gateway_status,
    load_gateway,
    save_gateway,
)

router = APIRouter(tags=["payments"])

"""Read and write the payment gateway configuration."""


class GatewayIn(StrictRequest):
    """The three Razorpay values. Omitted fields are left as they are."""

    key_id: str | None = Field(default=None, min_length=8, max_length=80)
    key_secret: str | None = Field(default=None, min_length=8, max_length=200)
    webhook_secret: str | None = Field(default=None, min_length=8, max_length=200)


@router.get("/admin/payments/gateway", summary="Whether Razorpay keys are set")
async def read_gateway(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_PAYMENTS)),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Status only. The secret columns are not selected into this response."""
    row = await load_gateway(session)
    return success(gateway_status(settings, row), request_id=get_request_id())


@router.put("/admin/payments/gateway", summary="Save Razorpay keys on the server")
async def write_gateway(
    payload: GatewayIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Owner only. A saved key is what checkout uses when the environment is empty.

    The response is the same status object as the read. It does not echo the
    secret that was just sent.
    """
    if payload.key_id is None and payload.key_secret is None and payload.webhook_secret is None:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Nothing to save",
            detail="Send key_id, key_secret, webhook_secret, or any combination.",
            type_slug="payments",
        )
    if payload.key_id is not None and any(character.isspace() for character in payload.key_id):
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid key id",
            detail="RAZORPAY_KEY_ID cannot contain spaces.",
            type_slug="payments",
        )
    await save_gateway(
        session,
        actor_id=actor.id,
        key_id=payload.key_id,
        key_secret=payload.key_secret,
        webhook_secret=payload.webhook_secret,
    )
    await record_audit(
        session,
        AuditAction.GATEWAY_CONFIGURED,
        actor=actor,
        summary="Saved Razorpay credentials",
        target_type="payment_gateway",
        target_id="razorpay",
        changes={
            "fieldsSet": [
                name
                for name, value in (
                    ("key_id", payload.key_id),
                    ("key_secret", payload.key_secret),
                    ("webhook_secret", payload.webhook_secret),
                )
                if value is not None
            ]
        },
        request=request,
    )
    await session.commit()
    row = await load_gateway(session)
    return success(gateway_status(settings, row), request_id=get_request_id())
