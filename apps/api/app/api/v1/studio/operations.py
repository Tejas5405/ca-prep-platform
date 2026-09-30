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

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import success
from app.core.permissions import Permission, require_permission
from app.models.user import User

from ._shared import (
    _flag,
)

router = APIRouter(tags=["admin"])

"""AI provider configuration and storage health."""


@router.get("/admin/ai", summary="Assistant configuration, without a secret")
async def ai_configuration(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_AI)),
) -> Any:
    settings = get_settings()
    configured = bool(settings.ai_provider_api_key)
    return success(
        {
            "enabled": await _flag(session, "features.ai_assistant", False),
            "providerConfigured": configured,
            "missingEnv": [] if configured else ["AI_PROVIDER_API_KEY"],
            "monthlyCeilingUsd": settings.ai_monthly_ceiling_usd,
            "freeQueriesPerDay": settings.ai_free_queries_per_day,
            "grounding": "document_filter",
            "groundingOptional": False,
            "generatesAnswers": configured,
            "model": settings.ai_provider_model if configured else None,
            "note": (
                "A suggestion is written only from excerpts the student may already read, "
                "and only when AI_PROVIDER_API_KEY is set. It is labelled as not a legal "
                "authority. Without excerpts, no answer is invented. Turning grounding off "
                "is not a setting."
                if configured
                else (
                    "The assistant quotes documents the student may already read. "
                    "AI_PROVIDER_API_KEY is unset, so no model is called."
                )
            ),
        },
        request_id=get_request_id(),
    )


@router.get("/admin/storage", summary="Whether storage can be listed")
async def storage_status(
    _actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    """No invented usage numbers.

    Listing objects needs the server secret. When it is absent this route says so
    and returns no counts, rather than a chart of zero that looks like an empty bucket.
    """
    settings = get_settings()
    missing = []
    if not settings.supabase_url:
        missing.append("SUPABASE_URL")
    if not settings.supabase_secret_key:
        missing.append("SUPABASE_SECRET_KEY")
    return success(
        {
            "configured": not missing,
            "missingEnv": missing,
            "objects": None,
            "note": (
                "Object counts are not reported until the secret key is set. "
                "A zero here would be indistinguishable from an empty bucket."
                if missing
                else (
                    "The credential is set. A bucket listing is not implemented in this "
                    "client, so no count is shown."
                )
            ),
        },
        request_id=get_request_id(),
    )
