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

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.core.security import Principal, get_current_principal
from app.integrations.razorpay import (
    RazorpayClient,
    RazorpayError,
    RazorpayNotConfiguredError,
)
from app.models.user import User
from app.repositories.billing import SqlBillingStore
from app.repositories.users import SqlUserRepository
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit
from app.services.billing import (
    ACTED_EVENT_TYPES,
    CURRENCY,
    BillingError,
    PaymentClaim,
    SubscriptionState,
    SubStatus,
    Tier,
    apply_payment,
    build_receipt,
    entitlement_for,
    extract_webhook_event,
    payment_rejection_reason,
    utcnow,
    verify_checkout_signature,
    verify_webhook_signature,
)
from app.services.gateway_config import (
    gateway_status,
    load_gateway,
    missing_for_checkout,
    resolved_payment_settings,
    save_gateway,
)
from app.services.plan_overrides import resolve_plan, resolved_catalogue

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payments"])

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


# ------------------------------------------------------------------ catalogue


@router.get("/payments/plans", summary="List purchasable plans")
async def list_plans(session: AsyncSession = Depends(get_db)) -> Any:
    """The pricing table. Public, and deliberately so - a pricing page behind an
    auth wall is a pricing page nobody reads before deciding to sign up.

    Amounts come from the same resolver the order route uses. A price an
    administrator saved is the price this page shows.
    """
    return success({"plans": await resolved_catalogue(session)}, request_id=get_request_id())


# ---------------------------------------------------------------- entitlement


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


# --------------------------------------------------------------------- orders


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


# ---------------------------------------------------------------- confirmation


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
