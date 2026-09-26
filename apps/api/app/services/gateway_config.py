"""Razorpay credentials an owner can enter once, without putting them in the browser.

Environment variables win. A value saved here is used only when the matching
environment variable is empty, so a deploy can still override a bad saved key
without a database edit.

The secret and the webhook secret are never copied into a response. The key id
is public by Razorpay's own design: Checkout has to receive it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.user import PaymentGatewayConfig

PROVIDER = "razorpay"


async def load_gateway(session: AsyncSession) -> PaymentGatewayConfig | None:
    execute = getattr(session, "execute", None)
    if execute is None:
        return None
    return (
        await execute(select(PaymentGatewayConfig).where(PaymentGatewayConfig.provider == PROVIDER))
    ).scalar_one_or_none()


async def resolved_payment_settings(session: AsyncSession, settings: Settings) -> Settings:
    """Settings the payment routes should use for this request.

    Returns the same object when the environment already has the full set, so
    the payment tests that inject keys never touch the database.
    """
    if (
        settings.razorpay_key_id
        and settings.razorpay_key_secret
        and settings.razorpay_webhook_secret
    ):
        return settings
    row = await load_gateway(session)
    if row is None:
        return settings
    updates: dict[str, str] = {}
    if not settings.razorpay_key_id and row.key_id:
        updates["razorpay_key_id"] = row.key_id
    if not settings.razorpay_key_secret and row.key_secret:
        updates["razorpay_key_secret"] = row.key_secret
    if not settings.razorpay_webhook_secret and row.webhook_secret:
        updates["razorpay_webhook_secret"] = row.webhook_secret
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def missing_for_checkout(settings: Settings) -> list[str]:
    missing = []
    if not settings.razorpay_key_id:
        missing.append("RAZORPAY_KEY_ID")
    if not settings.razorpay_key_secret:
        missing.append("RAZORPAY_KEY_SECRET")
    return missing


def gateway_status(settings: Settings, row: PaymentGatewayConfig | None) -> dict[str, Any]:
    """What an operator may see. No secret field exists on this dict."""
    key_id = settings.razorpay_key_id or (row.key_id if row else None)
    secret_set = bool(settings.razorpay_key_secret or (row and row.key_secret))
    webhook_set = bool(settings.razorpay_webhook_secret or (row and row.webhook_secret))
    env_pair = bool(settings.razorpay_key_id and settings.razorpay_key_secret)
    saved_pair = bool(row and row.key_id and row.key_secret)
    if env_pair:
        source = "environment"
    elif saved_pair:
        source = "saved"
    else:
        source = "missing"
    missing_checkout = []
    if not key_id:
        missing_checkout.append("RAZORPAY_KEY_ID")
    if not secret_set:
        missing_checkout.append("RAZORPAY_KEY_SECRET")
    return {
        "provider": PROVIDER,
        "checkoutReady": bool(key_id and secret_set),
        "webhookReady": bool(key_id and secret_set and webhook_set),
        "source": source,
        "keyId": key_id,
        "secretSet": secret_set,
        "webhookSecretSet": webhook_set,
        "missingForCheckout": missing_checkout,
        "missingForWebhook": [] if webhook_set else ["RAZORPAY_WEBHOOK_SECRET"],
        "note": (
            "Checkout uses RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET. "
            "RAZORPAY_WEBHOOK_SECRET is required for Razorpay's server events, not for "
            "the browser confirmation. A secret saved here is stored on the server and "
            "is not returned."
        ),
    }


async def save_gateway(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    key_id: str | None,
    key_secret: str | None,
    webhook_secret: str | None,
) -> PaymentGatewayConfig:
    """Replace only the fields the owner actually sent."""
    row = await load_gateway(session)
    if row is None:
        row = PaymentGatewayConfig(id=uuid.uuid4(), provider=PROVIDER)
        session.add(row)
    if key_id is not None:
        row.key_id = key_id
    if key_secret is not None:
        row.key_secret = key_secret
    if webhook_secret is not None:
        row.webhook_secret = webhook_secret
    row.updated_by = actor_id
    await session.flush()
    return row
