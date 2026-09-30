"""The owner's back office: platform numbers, people, permissions, settings, logs.

WHAT THIS FILE IS FOR

Everything here answers a question the owner has to answer without a database client:
"What is the platform doing?", "who is on it?", "what may they do?", "who changed
this?", and "turn that feature off". Each route requires a PERMISSION rather than a
role, so a delegated accountant can be given payments and nothing else - see
``app/core/permissions.py`` for why that distinction exists and what it costs.

WHY THE PERMISSION CHECKS READ THE DATABASE

``require_permission`` resolves the caller's role from ``users.role`` on every
request instead of trusting the token claim alone. An access token lives for an hour;
"revoke this person's admin access" must not mean "it takes effect within an hour",
especially when the permission being revoked gates deleting the entire library.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem
from app.core.permissions import (
    Permission,
    require_permission,
)
from app.models.user import PaymentEvent, PaymentOrder, Subscription, User

from ._shared import (
    _ilike_contains,
)

router = APIRouter(tags=["admin"])

"""Read-only views over orders and payment events."""


#: The order lifecycle. Not the subscription vocabulary: PAID is not ACTIVE.
ORDER_STATUSES = frozenset({"CREATED", "PAID", "FAILED", "EXPIRED"})


ORDER_STATUSES = frozenset({"CREATED", "PAID", "FAILED", "EXPIRED"})
#: A live entitlement. TRIAL is included because the partial unique index treats it
#: as the one current subscription, the same way the billing store does.
_LIVE_SUBSCRIPTION = ("ACTIVE", "TRIAL")


def _money(paise: int) -> dict[str, int]:
    """Whole rupees plus a remainder, so the screen never divides currency."""
    rupees, remainder = divmod(int(paise), 100)
    return {"amountRupees": rupees, "amountRemainderPaise": remainder}


def _order_filters(
    status_filter: str | None,
    plan_code: str | None,
    q: str | None,
) -> list[Any]:
    conditions: list[Any] = []
    if status_filter:
        conditions.append(PaymentOrder.status == status_filter)
    if plan_code and plan_code.strip():
        conditions.append(PaymentOrder.plan_code == plan_code.strip())
    if q and q.strip():
        conditions.append(User.email.ilike(_ilike_contains(q.strip()), escape="\\"))
    return conditions


@router.get(
    "/admin/payments/orders",
    summary="Payment orders",
    description=(
        "Checkout intents for reconciliation. A PAID order is not an entitlement; "
        "that is read from subscriptions and returned beside the order. Webhook "
        "bodies are not on this route."
    ),
)
async def list_payment_orders(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    plan_code: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_PAYMENTS)),
) -> Any:
    """Who tried to pay, for how much, and whether that became access.

    There is no refund action here. `payments.refund_recorded` exists as an audit
    name and nothing writes it; a button that marked an order refunded would
    disagree with Razorpay, which this process cannot call without keys and which
    this route does not call even when keys exist.
    """
    if status_filter and status_filter not in ORDER_STATUSES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown order status",
            detail=f"Status must be one of {', '.join(sorted(ORDER_STATUSES))}.",
            type_slug="payments",
        )

    conditions = _order_filters(status_filter, plan_code, q)
    base = select(PaymentOrder, User.email, User.display_name).join(
        User, User.id == PaymentOrder.user_id
    )
    if conditions:
        base = base.where(*conditions)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(PaymentOrder.created_at.desc(), PaymentOrder.id.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    ).all()

    totals_stmt = (
        select(
            PaymentOrder.status,
            func.count(),
            func.coalesce(func.sum(PaymentOrder.amount_paise), 0),
        )
        .select_from(PaymentOrder)
        .join(User, User.id == PaymentOrder.user_id)
        .group_by(PaymentOrder.status)
    )
    if conditions:
        totals_stmt = totals_stmt.where(*conditions)
    totals = (await session.execute(totals_stmt)).all()
    by_status = dict.fromkeys(sorted(ORDER_STATUSES), 0)
    paid_orders = 0
    paid_paise = 0
    for status_name, count, paise in totals:
        by_status[str(status_name)] = int(count)
        if status_name == "PAID":
            paid_orders = int(count)
            paid_paise = int(paise)

    user_ids = [order.user_id for order, _email, _name in rows]
    entitlements: dict[Any, Subscription] = {}
    if user_ids:
        live = (
            (
                await session.execute(
                    select(Subscription).where(
                        Subscription.user_id.in_(user_ids),
                        Subscription.status.in_(_LIVE_SUBSCRIPTION),
                    )
                )
            )
            .scalars()
            .all()
        )
        entitlements = {row.user_id: row for row in live}

    items = []
    for order, email, display_name in rows:
        money = _money(order.amount_paise)
        live_sub = entitlements.get(order.user_id)
        items.append(
            {
                "id": str(order.id),
                "email": email,
                "displayName": display_name,
                "userId": str(order.user_id),
                "planCode": order.plan_code,
                "tier": order.tier,
                "amountPaise": int(order.amount_paise),
                "amountRupees": money["amountRupees"],
                "amountRemainderPaise": money["amountRemainderPaise"],
                "currency": order.currency,
                "status": order.status,
                "provider": order.provider,
                "providerOrderId": order.provider_order_id,
                "providerPaymentId": order.provider_payment_id,
                "providerStatus": order.provider_status,
                "receipt": order.receipt,
                "paidAt": order.paid_at,
                "failureReason": order.failure_reason,
                "createdAt": order.created_at,
                "entitlement": None
                if live_sub is None
                else {
                    "status": live_sub.status,
                    "tier": live_sub.tier,
                    "expiresAt": live_sub.expires_at,
                },
            }
        )

    paid = _money(paid_paise)
    return paginated(
        items,
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
        extra={
            "paidOrders": paid_orders,
            "paidPaise": paid_paise,
            "paidRupees": paid["amountRupees"],
            "paidRemainderPaise": paid["amountRemainderPaise"],
            "byStatus": by_status,
        },
    )


@router.get(
    "/admin/payments/events",
    summary="Payment webhook events",
    description=(
        "Webhook rows for reconciliation. The raw payload column is not selected: "
        "it can contain payment-method details and is kept only for idempotency."
    ),
)
async def list_payment_events(
    event_type: str | None = Query(default=None, max_length=80),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.VIEW_PAYMENTS)),
) -> Any:
    """What the gateway told us, without the body it told us in.

    `PaymentEvent.payload` is deliberately absent from the SELECT. A list that
    loaded it and then deleted the key would still have pulled customer payment
    details into the process, and a later edit could put them back on the wire.
    """
    conditions: list[Any] = []
    if event_type and event_type.strip():
        conditions.append(PaymentEvent.event_type == event_type.strip())
    if q and q.strip():
        conditions.append(User.email.ilike(_ilike_contains(q.strip()), escape="\\"))

    # Columns, not the mapped class: selecting the class would load `payload`.
    columns = (
        PaymentEvent.id,
        PaymentEvent.provider,
        PaymentEvent.event_id,
        PaymentEvent.event_type,
        PaymentEvent.signature_verified,
        PaymentEvent.processed_at,
        PaymentEvent.processing_error,
        PaymentEvent.user_id,
        PaymentEvent.created_at,
        User.email,
    )
    base = select(*columns).outerjoin(User, User.id == PaymentEvent.user_id)
    if conditions:
        base = base.where(*conditions)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(PaymentEvent.created_at.desc(), PaymentEvent.id.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
    ).all()

    unprocessed_stmt = (
        select(func.count())
        .select_from(PaymentEvent)
        .outerjoin(User, User.id == PaymentEvent.user_id)
        .where(PaymentEvent.processed_at.is_(None))
    )
    if conditions:
        unprocessed_stmt = unprocessed_stmt.where(*conditions)
    unprocessed = (await session.execute(unprocessed_stmt)).scalar_one()

    return paginated(
        [
            {
                "id": str(row.id),
                "provider": row.provider,
                "eventId": row.event_id,
                "eventType": row.event_type,
                "signatureVerified": bool(row.signature_verified),
                "processedAt": row.processed_at,
                "processingError": row.processing_error,
                "userId": str(row.user_id) if row.user_id else None,
                "email": row.email,
                "createdAt": row.created_at,
            }
            for row in rows
        ],
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
        extra={"payloadOmitted": True, "unprocessed": int(unprocessed)},
    )
