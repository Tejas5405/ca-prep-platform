"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.core.permissions import Permission, require_permission
from app.models.campus import (
    Experiment,
    ExperimentAssignment,
    MarketplaceListing,
)
from app.models.user import User
from app.schemas.base import StrictRequest

router = APIRouter(tags=["campus"])

"""Experiment variants and the peer marketplace."""


class ExperimentIn(StrictRequest):
    key: str = Field(min_length=2, max_length=60, pattern="^[a-z0-9_]+$")
    description: str = Field(min_length=2, max_length=500)


class ExperimentActiveIn(StrictRequest):
    active: bool


class ListingIn(StrictRequest):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=2, max_length=1000)
    plan_code: str | None = Field(default=None, max_length=40)
    price_paise: int | None = Field(default=None, ge=0, le=10_000_000)


@router.get("/campus/experiments/{key}", summary="Your variant, if an experiment is active")
async def experiment_variant(
    key: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    experiment = (
        await session.execute(select(Experiment).where(Experiment.key == key))
    ).scalar_one_or_none()
    if experiment is None or not experiment.active:
        return success(
            {
                "key": key,
                "active": False,
                "variant": None,
                "note": "No active experiment. Nothing was assigned.",
            },
            request_id=get_request_id(),
        )
    existing = (
        await session.execute(
            select(ExperimentAssignment).where(
                ExperimentAssignment.experiment_id == experiment.id,
                ExperimentAssignment.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    variants = [str(item) for item in (experiment.variants or ["A", "B"]) if str(item)]
    if not variants:
        variants = ["A", "B"]
    if existing is None:
        digest = hashlib.sha256(f"{experiment.key}:{user.id}".encode()).hexdigest()
        variant = variants[int(digest[:8], 16) % len(variants)]
        existing = ExperimentAssignment(
            id=uuid.uuid4(), experiment_id=experiment.id, user_id=user.id, variant=variant
        )
        session.add(existing)
        await session.commit()
    return success(
        {
            "key": experiment.key,
            "active": True,
            "variant": existing.variant,
            "note": "A stored assignment. It does not measure a conversion.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/experiments", summary="Create an inactive experiment")
async def create_experiment(
    payload: ExperimentIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    row = Experiment(
        id=uuid.uuid4(),
        key=payload.key,
        description=payload.description.strip(),
        active=False,
        variants=["A", "B"],
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=409,
            title="Key exists",
            detail="That experiment key is already used.",
            type_slug="campus",
        )
    return success(
        {"id": str(row.id), "key": row.key, "active": False}, request_id=get_request_id()
    )


@router.patch("/admin/experiments/{key}", summary="Turn an experiment on or off")
async def set_experiment(
    key: str,
    payload: ExperimentActiveIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    row = (
        await session.execute(select(Experiment).where(Experiment.key == key))
    ).scalar_one_or_none()
    if row is None:
        return problem(
            status=404,
            title="Experiment not found",
            detail="That key does not exist.",
            type_slug="campus",
        )
    row.active = payload.active
    await session.commit()
    return success({"key": row.key, "active": row.active}, request_id=get_request_id())


@router.get("/campus/marketplace", summary="Listings that do not take payment")
async def list_marketplace(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(MarketplaceListing)
                .where(MarketplaceListing.active.is_(True))
                .order_by(MarketplaceListing.title)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "listings": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "description": row.description,
                    "planCode": row.plan_code,
                    "pricePaise": row.price_paise,
                    "takesPayment": False,
                }
                for row in rows
            ],
            "note": "A listing does not create an order. Paid plans are bought on the upgrade page.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/marketplace", summary="Add a listing that cannot charge")
async def create_listing(
    payload: ListingIn,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_PLANS)),
) -> Any:
    row = MarketplaceListing(
        id=uuid.uuid4(),
        title=payload.title.strip(),
        description=payload.description.strip(),
        plan_code=payload.plan_code,
        price_paise=payload.price_paise,
        active=True,
        takes_payment=False,
        created_by=actor.id,
    )
    session.add(row)
    await session.commit()
    return success(
        {"id": str(row.id), "takesPayment": False, "orderCreated": False},
        request_id=get_request_id(),
    )
