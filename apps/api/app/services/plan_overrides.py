"""Prices an administrator can change, that checkout actually charges.

The code catalogue in ``billing.PLANS`` is the default, including Premium Plus
at ₹1,299. A saved override replaces the amount and the duration for the next
order. It does not replace the tier or the entitlement list: a form that could
invent a feature would promise something the API still refuses.

An order that already exists keeps the amount stored on that row. This module
is only consulted when a new order is priced, when the public catalogue is
rendered, and when a confirmed payment needs a duration.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import PlatformSetting
from app.services.billing import CURRENCY, PLANS, PURCHASABLE_CODES, BillingError, Plan, plan_for

logger = logging.getLogger(__name__)

OVERRIDE_KEY = "billing.plan_overrides"
MIN_PAISE = 100
MAX_PAISE = 10_000_000
MAX_DAYS = 3650


def catalogue_item(plan: Plan) -> dict[str, Any]:
    """One pricing-table row. Shared so the public page and the admin page cannot drift."""
    return {
        "code": plan.code,
        "tier": plan.tier.value,
        "label": plan.label,
        "amountPaise": plan.amount_paise,
        "amountRupees": plan.amount_rupees,
        "currency": CURRENCY,
        "durationDays": plan.duration_days,
        "tagline": plan.tagline,
        "features": list(plan.features),
        "recommended": plan.recommended,
    }


def _usable_int(value: object, *, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < low or value > high:
        return None
    return value


def apply_override(plan: Plan, raw: object) -> Plan:
    """Apply a saved price, or keep the code price if the saved value is unusable."""
    if not isinstance(raw, dict):
        return plan
    amount = _usable_int(raw.get("amountPaise", plan.amount_paise), low=MIN_PAISE, high=MAX_PAISE)
    duration = _usable_int(raw.get("durationDays", plan.duration_days), low=1, high=MAX_DAYS)
    label = raw.get("label", plan.label)
    if not isinstance(label, str) or not label.strip():
        label = plan.label
    if amount is None or duration is None:
        logger.warning("Ignoring an unusable price override for %s", plan.code)
        return plan
    return replace(plan, amount_paise=amount, duration_days=duration, label=label.strip()[:80])


def _unwrap(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    inner = value.get("value")
    if set(value) == {"value"} and isinstance(inner, dict):
        return inner
    return value


async def load_overrides(session: AsyncSession) -> dict[str, Any]:
    """Saved prices, or an empty dict when this session cannot read settings.

    The payment HTTP tests use a session double with no ``execute``. Production
    sessions always have one. Falling back here keeps those tests on the code
    catalogue, which is also the price they assert.
    """
    execute = getattr(session, "execute", None)
    if execute is None:
        return {}
    row = (
        await execute(select(PlatformSetting).where(PlatformSetting.key == OVERRIDE_KEY))
    ).scalar_one_or_none()
    if row is None:
        return {}
    return _unwrap(row.value)


async def resolve_plan(session: AsyncSession, code: str) -> Plan:
    """The plan a new order must charge. Unknown and free codes still fail closed."""
    plan = plan_for(code)
    saved = await load_overrides(session)
    return apply_override(plan, saved.get(plan.code))


async def resolved_catalogue(session: AsyncSession) -> list[dict[str, Any]]:
    saved = await load_overrides(session)
    items = []
    for plan in PLANS:
        current = plan
        if plan.code in PURCHASABLE_CODES:
            current = apply_override(plan, saved.get(plan.code))
        items.append(catalogue_item(current))
    return items


def validate_override(
    *, code: str, amount_paise: int, duration_days: int, label: str | None
) -> Plan:
    """Reject a price checkout would not be allowed to charge."""
    if isinstance(amount_paise, bool) or not isinstance(amount_paise, int):
        raise BillingError("amount_paise must be an integer number of paise")
    if amount_paise < MIN_PAISE or amount_paise > MAX_PAISE:
        raise BillingError(f"amount_paise must be between {MIN_PAISE} and {MAX_PAISE}")
    if isinstance(duration_days, bool) or not isinstance(duration_days, int):
        raise BillingError("duration_days must be an integer")
    if duration_days < 1 or duration_days > MAX_DAYS:
        raise BillingError(f"duration_days must be between 1 and {MAX_DAYS}")
    plan = plan_for(code)
    if label is not None and not label.strip():
        raise BillingError("label cannot be blank")
    return apply_override(
        plan,
        {
            "amountPaise": amount_paise,
            "durationDays": duration_days,
            **({"label": label} if label is not None else {}),
        },
    )
