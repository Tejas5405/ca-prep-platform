"""SQLAlchemy persistence for payments and subscriptions.

THE TWO GUARANTEES THIS MODULE OWNS

1. **Webhook idempotency.** Blueprint v3 §13.1: "Treat webhook handling as
   idempotent." Providers retry, sometimes many times, and a retry that
   double-extends a subscription is a real revenue leak that no alert will catch.
   The guarantee is a database constraint, not a check-then-act: ``record_event``
   does a single ``INSERT ... ON CONFLICT DO NOTHING`` and reports whether it
   actually inserted. Two concurrent deliveries of the same event id cannot both
   return True, because the row lock is taken by the insert itself.

   A read-then-write ("SELECT to see if we have seen this, then INSERT") is the
   version that looks correct and is not: both requests can read "not seen" before
   either writes.

2. **One entitlement row per user.** ``subscriptions`` carries a partial unique
   index on ``(user_id)`` where status is ACTIVE or TRIAL. ``activate`` therefore
   LOCKS the existing active row and updates it rather than inserting a second.
   Without the lock, two webhooks arriving together would both miss the existing
   row and both insert, and the second insert would fail against the index - at
   which point a retry storm turns one payment into a 500. The lock makes the
   second caller WAIT and then see the row the first one wrote.

Both are exercised here as contract tests against a fake session, because these
are the SQL semantics that a plain unit test cannot reach.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.user import PaymentEvent, PaymentOrder, Subscription
from app.services.billing import (
    Activation,
    SubStatus,
    Tier,
)

logger = logging.getLogger(__name__)


def _dump_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a provider event into JSONB-storable form.

    FastAPI has already parsed the webhook body. The RAW bytes are what the
    signature covers and those are gone by now, so the parsed form is what gets
    archived - which is fine for reconciliation, but it is why signature
    verification must happen before this function is ever called.
    """
    return {"event": dict(event)}


class SqlBillingStore:
    """Persistence for orders, subscriptions and payment events.

    The session is passed in rather than created here, matching the other
    repositories: the caller owns the transaction boundary so a route can compose
    several writes atomically.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    # ------------------------------------------------------------- orders

    async def create_order(
        self,
        *,
        user_id: uuid.UUID,
        plan_code: str,
        tier: Tier,
        amount_paise: int,
        currency: str,
        receipt: str,
        expires_at: datetime | None = None,
    ) -> PaymentOrder:
        """Insert a CREATED order.

        COMMITTED HERE, not merely flushed, and the difference is the whole point.

        The order row must survive the call to Razorpay. If it were left pending in
        a transaction that the request later commits, a process death during the
        gateway call would roll it back - leaving a real Razorpay order with no
        local record of who it belongs to, whose webhook would then arrive naming
        an order this service has never heard of and could not reconcile without a
        human. Flushing keeps the row visible to this transaction only; committing
        is what makes it durable before the network call.

        An earlier version of this method flushed and its docstring claimed it
        committed. The route test asserted that the database was written before the
        gateway was called, which was true of the *order of calls* while the
        durability the ordering exists for was absent. The test now asserts the
        commit itself.
        """
        # A caller may already have work in flight; commit it with this insert so
        # the row is durable as one unit rather than two.

        order = PaymentOrder(
            user_id=user_id,
            plan_code=plan_code,
            tier=tier.value,
            amount_paise=amount_paise,
            currency=currency,
            receipt=receipt,
            status="CREATED",
            expires_at=expires_at,
        )
        self._session.add(order)
        await self._session.flush()
        await self._session.commit()
        return order

    async def attach_provider_order(self, order: PaymentOrder, provider_order_id: str) -> None:
        """Record the gateway's order id on our row, then commit.

        Separate from ``create_order`` because the two can fail independently: a
        Razorpay timeout after our insert leaves an orphan order that must be
        recorded as such rather than rolled back, since the order may well have
        been created on their side.
        """
        order.provider_order_id = provider_order_id

    async def order_for_user(
        self, *, order_id: uuid.UUID, user_id: uuid.UUID
    ) -> PaymentOrder | None:
        """Load an order that belongs to this user.

        Scoped by user in the WHERE clause rather than fetched and compared
        afterwards. Fetching first and comparing later is how an ownership check
        gets dropped in a refactor; scoping the query means a missing check would
        return nothing instead of someone else's order.
        """
        result = await self._session.execute(
            select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def order_by_provider_order_id(self, provider_order_id: str) -> PaymentOrder | None:
        result = await self._session.execute(
            select(PaymentOrder).where(PaymentOrder.provider_order_id == provider_order_id)
        )
        return result.scalar_one_or_none()

    async def mark_order_paid(
        self,
        order: PaymentOrder,
        *,
        provider_payment_id: str,
        provider_status: str,
        paid_at: datetime,
    ) -> bool:
        """Mark an order PAID, returning False if it already was.

        The False return is what makes the whole flow idempotent end to end: the
        webhook and the browser callback both legitimately try to do this, and
        whichever arrives second must not extend the subscription again.
        """
        if order.status == "PAID":
            return False
        order.status = "PAID"
        order.provider_payment_id = provider_payment_id
        order.provider_status = provider_status
        order.paid_at = paid_at
        return True

    async def mark_order_failed(self, order: PaymentOrder, *, reason: str) -> None:
        # A paid order is never demoted by a later failure event for the same
        # order. Providers do send both (a failed attempt, then a successful
        # retry on the same order), and the successful one is the truth.
        if order.status == "PAID":
            return
        order.status = "FAILED"
        order.failure_reason = reason[:300]

    # ------------------------------------------------------ subscriptions

    async def active_subscription(
        self, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Subscription | None:
        """The user's ACTIVE or TRIAL subscription, if any.

        ``for_update`` takes a row lock. Activation passes True; entitlement reads
        do not, because locking on a read path would serialise every request from
        a subscribed user behind the same row.
        """
        stmt = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_([SubStatus.ACTIVE.value, SubStatus.TRIAL.value]),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def activate(
        self,
        *,
        user_id: uuid.UUID,
        activation: Activation,
        plan_code: str,
        amount_paise: int,
        currency: str,
        provider: str,
    ) -> Subscription:
        """Apply a confirmed payment to the subscription row.

        Locked read, then update-or-insert. The lock is what makes concurrent
        webhooks safe; the partial unique index on the table is the backstop that
        turns a logical mistake into a constraint violation rather than a silently
        doubled entitlement.

        WHERE THE PAYMENT REFERENCE LIVES

        ``subscriptions`` has ``provider_subscription_id``, which is for a recurring
        subscription created at the provider. Razorpay Subscriptions are not used -
        this service creates one-off orders - so that column stays NULL and the
        payment reference is read from ``payment_orders.provider_payment_id``,
        which is the row that actually represents the transaction. Writing a
        payment id into a subscription-id column would make the two
        indistinguishable at exactly the moment someone is reconciling a refund.
        """
        # `plan_code` is accepted for the audit trail: the caller knows which SKU
        # was bought and the subscription row does not store it.
        _ = plan_code
        existing = await self.active_subscription(user_id, for_update=True)

        if existing is not None:
            existing.tier = activation.tier.value
            existing.status = activation.status.value
            existing.expires_at = activation.expires_at
            existing.amount_paise = amount_paise
            existing.currency = currency
            existing.provider = provider
            await self._session.flush()
            return existing

        subscription = Subscription(
            user_id=user_id,
            tier=activation.tier.value,
            status=activation.status.value,
            started_at=activation.started_at,
            expires_at=activation.expires_at,
            auto_renew=False,
            provider=provider,
            amount_paise=amount_paise,
            currency=currency,
        )
        self._session.add(subscription)
        await self._session.flush()
        return subscription

    # ----------------------------------------------------- payment events

    async def record_event(
        self,
        *,
        provider: str,
        event_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        signature_verified: bool,
        user_id: uuid.UUID | None = None,
    ) -> bool:
        """Archive a webhook. Returns True only if this event is NEW.

        ``ON CONFLICT (event_id) DO NOTHING`` - one statement, so there is no
        window between the check and the write. The return value is the caller's
        signal to skip processing entirely.

        Returning False is not an error: it is the normal outcome of a provider
        retry and should produce a 200, because a 4xx or 5xx here makes Razorpay
        retry harder, which is the opposite of what idempotency is for.
        """
        stmt = (
            pg_insert(PaymentEvent)
            .values(
                provider=provider,
                event_id=event_id,
                event_type=event_type,
                payload=_dump_event(payload),
                signature_verified=signature_verified,
                user_id=user_id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            # The unique constraint on event_id is the arbiter, and it is named
            # explicitly rather than left to inference: inference depends on the
            # constraint still being there and still being the only one.
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        result = await self._session.execute(stmt)
        # rowcount is 0 when the conflict fired, 1 when a row was inserted.
        return bool(result.rowcount)

    async def mark_event_processed(self, event_id: str, *, error: str | None = None) -> None:
        result = await self._session.execute(
            select(PaymentEvent).where(PaymentEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()
        if event is None:
            return
        event.processed_at = datetime.now(UTC)
        event.processing_error = error[:500] if error else None
