"""The remaining console writes: questions, papers, syllabus, and the honest gaps.

WHAT THIS FILE IS FOR

Six sidebar rows were grey because a screen behind them would have been a lie.
This file is the part that can be true:

  * a question bank an editor can list and correct, including historical taxation;
  * a mock paper that can be composed, not only published;
  * a syllabus that can be extended, not only seeded;
  * the plan catalogue checkout actually charges, including a saved price;
  * the assistant's real configuration, including the fact that it does not invent;
  * storage, which reports the missing credential instead of a made-up usage chart.

WHAT IT WILL NOT DO

It will not publish a question. That route already exists and records a verifier.
A patch that set ``status=PUBLISHED`` would either violate the database constraint
or skip the person on the record. Archive is the only status change offered here.

A saved price is what ``POST /payments/order`` reads. The tier and the
entitlement list stay in code, so a form cannot invent a feature.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.engagement import PlatformSetting
from app.models.user import User
from app.schemas.base import StrictRequest
from app.services import billing
from app.services.audit import AuditAction, record_audit
from app.services.plan_overrides import (
    OVERRIDE_KEY,
    BillingError,
    resolved_catalogue,
    validate_override,
)

router = APIRouter(tags=["admin"])

"""Plan catalogue and price overrides."""


@router.get("/admin/plans", summary="The catalogue checkout charges")
async def list_plans(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_PLANS)),
) -> Any:
    """The prices ``POST /payments/order`` will charge.

    Saving a different number here changes the next order. It does not change an
    order that was already created, and it does not change what a tier is allowed
    to do. Premium Plus defaults to ₹1,299 until an administrator saves another
    amount.
    """
    plans = []
    for item in await resolved_catalogue(session):
        tier = billing.Tier(item["tier"])
        plans.append(
            {
                **item,
                "entitlements": sorted(billing.TIER_ENTITLEMENTS[tier]),
            }
        )
    return success(
        {
            "editable": True,
            "reason": (
                "Checkout reads this catalogue. A price saved here is the price the "
                "next order charges. An order already created keeps its own amount. "
                "Premium Plus defaults to ₹1,299. Entitlements are not edited here."
            ),
            "plans": plans,
        },
        request_id=get_request_id(),
    )


class PlanPriceIn(StrictRequest):
    code: str = Field(min_length=1, max_length=40)
    amount_paise: int = Field(ge=100, le=10_000_000)
    duration_days: int = Field(ge=1, le=3650)
    label: str | None = Field(default=None, max_length=80)


@router.put("/admin/plans", summary="Save a price checkout will charge")
async def save_plan_price(
    payload: PlanPriceIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_PLANS)),
) -> Any:
    try:
        updated = validate_override(
            code=payload.code,
            amount_paise=payload.amount_paise,
            duration_days=payload.duration_days,
            label=payload.label,
        )
    except BillingError as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Price not accepted",
            detail=str(exc),
            type_slug="plans",
        )
    row = (
        await session.execute(select(PlatformSetting).where(PlatformSetting.key == OVERRIDE_KEY))
    ).scalar_one_or_none()
    current = row.value if row is not None and isinstance(row.value, dict) else {}
    if set(current) == {"value"} and isinstance(current.get("value"), dict):
        current = current["value"]
    saved = dict(current)
    saved[updated.code] = {
        "amountPaise": updated.amount_paise,
        "durationDays": updated.duration_days,
        "label": updated.label,
    }
    if row is None:
        row = PlatformSetting(
            id=uuid.uuid4(),
            key=OVERRIDE_KEY,
            value=saved,
            description="Prices checkout charges. Entitlements stay in code.",
            is_public=False,
            updated_by=actor.id,
        )
        session.add(row)
    else:
        row.value = saved
        row.updated_by = actor.id
    await record_audit(
        session,
        AuditAction.PLAN_UPDATED,
        actor=actor,
        summary=f"Set {updated.code} to {updated.amount_paise} paise",
        target_type="plan",
        target_id=updated.code,
        changes={"amountPaise": updated.amount_paise, "durationDays": updated.duration_days},
        request=request,
    )
    await session.commit()
    return success(
        {
            "code": updated.code,
            "amountPaise": updated.amount_paise,
            "amountRupees": updated.amount_rupees,
            "durationDays": updated.duration_days,
            "label": updated.label,
        },
        request_id=get_request_id(),
    )
