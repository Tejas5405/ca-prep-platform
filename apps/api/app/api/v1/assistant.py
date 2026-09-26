"""Ask the library.

The search is the same page search the library uses, under the same document
filter. Staff do not get the staff bypass. Drafts and answer keys stay out.

A model is called only when three things are true: the feature flag is on, the
server has AI_PROVIDER_API_KEY, and at least one excerpt the student may read
was found. The reply is labelled a study suggestion. It is not a legal
authority. If the call fails, or there is nothing to ground it on, ``answer``
stays null and nothing is invented.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.engagement import AnalyticsEvent, PlatformSetting
from app.models.question import Question
from app.models.user import User
from app.repositories.content import SqlContentStore
from app.schemas.base import StrictRequest
from app.services.analytics import record_event
from app.services.entitlements import entitlements_for, resolve_viewer
from app.services.gemini import write_suggestion

router = APIRouter(tags=["assistant"])

_ASKED = "ai.library_asked"
_WROTE = "ai.suggestion_written"
_MARKS = re.compile(r"</?mark>")
_PREMIUM_DAILY = 40
#: A query cap, not a bill. This server does not receive a charge from Google.
_MONTHLY_GENERATIONS = 2000


class AskRequest(StrictRequest):
    query: str = Field(min_length=2, max_length=200)


async def _enabled(session: AsyncSession) -> bool:
    row = (
        await session.execute(
            select(PlatformSetting).where(PlatformSetting.key == "features.ai_assistant")
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    # Migration 0008 stored a bare JSON false. The settings screen stores {"value": bool}.
    # Both mean the same switch. A bare true must not be read as off.
    if isinstance(row.value, dict):
        return bool(row.value.get("value", False))
    if isinstance(row.value, bool):
        return row.value
    return False


async def _generation_capped(session: AsyncSession) -> bool:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (
        await session.execute(
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.name == _WROTE, AnalyticsEvent.created_at >= start)
        )
    ).scalar_one()
    return int(used) >= _MONTHLY_GENERATIONS


def _plain(value: str | None) -> str:
    return _MARKS.sub("", value or "")


@router.get("/assistant/status", summary="Whether the assistant is on")
async def assistant_status(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    settings = get_settings()
    return success(
        {
            "enabled": await _enabled(session),
            "generatesAnswers": bool(settings.ai_provider_api_key),
            "providerConfigured": bool(settings.ai_provider_api_key),
            "model": settings.ai_provider_model if settings.ai_provider_api_key else None,
            "note": (
                "When excerpts exist, a study suggestion is written from them and labelled "
                "as not a legal authority. Without excerpts, no answer is invented."
                if settings.ai_provider_api_key
                else (
                    "Quotations from documents you may read. "
                    "No model is called until AI_PROVIDER_API_KEY is set."
                )
            ),
        },
        request_id=get_request_id(),
    )


@router.post("/assistant/ask", summary="Quote documents the student may read")
async def ask(
    payload: AskRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    if not await _enabled(session):
        return problem(
            status=status.HTTP_403_FORBIDDEN,
            title="Assistant is off",
            detail="An owner turns features.ai_assistant on in platform settings. It is off.",
            type_slug="assistant",
        )

    snapshot = await entitlements_for(session, user.id)
    settings = get_settings()
    cap = _PREMIUM_DAILY if snapshot.is_premium else settings.ai_free_queries_per_day
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = (
        await session.execute(
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(
                AnalyticsEvent.user_id == user.id,
                AnalyticsEvent.name == _ASKED,
                AnalyticsEvent.created_at >= start,
            )
        )
    ).scalar_one()
    if int(used) >= cap:
        return problem(
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            title="Daily assistant limit reached",
            detail=(
                f"{cap} questions a day. The limit is counted on the server, not in the browser."
            ),
            type_slug="assistant",
        )

    viewer = await resolve_viewer(session, user)
    # Never the staff bypass. Drafts and unpublished files stay out.
    reader = replace(viewer, is_staff=False)
    hits = await SqlContentStore(session).search_pages(payload.query.strip(), reader, limit=5)
    quotations = [
        {
            "documentId": str(hit.document_id),
            "title": hit.title,
            "pageNumber": hit.page_number,
            "excerpt": _plain(hit.excerpt),
        }
        for hit in hits
    ]

    needle = payload.query.strip().replace("\\", "").replace("%", "").replace("_", "")
    question_conditions = [
        Question.status == "PUBLISHED",
        Question.deleted_at.is_(None),
        Question.text.ilike(f"%{needle}%"),
    ]
    if reader.tier == "FREE":
        question_conditions.append(Question.is_premium.is_(False))
    questions = (
        await session.execute(
            select(Question.id, Question.text).where(*question_conditions).limit(3)
        )
    ).all()
    question_hits = [
        {"questionId": str(row.id), "excerpt": (row.text or "")[:400]} for row in questions
    ]

    excerpts = [hit["excerpt"] for hit in quotations] + [hit["excerpt"] for hit in question_hits]
    answer = None
    generates = False
    if not excerpts:
        note = "Nothing you are allowed to read contains that. No answer was invented."
    elif not settings.ai_provider_api_key:
        note = (
            "These are quotations from documents you may already read. "
            "Nothing here was written by a model."
        )
    elif await _generation_capped(session):
        note = (
            "The monthly generation cap was reached. The quotations below are unchanged. "
            "No explanation was invented, and this is a query cap, not a bill from Google."
        )
    else:
        answer = await write_suggestion(
            api_key=settings.ai_provider_api_key,
            model=settings.ai_provider_model,
            query=payload.query.strip(),
            excerpts=excerpts,
        )
        if answer:
            generates = True
            note = (
                "Study suggestion grounded on the excerpts below. Not a legal authority, "
                "not an ICAI ruling, and not a substitute for the statute."
            )
        else:
            note = (
                "The model call failed. The quotations below are unchanged. "
                "No explanation was invented."
            )

    await record_event(
        session,
        _ASKED,
        user_id=user.id,
        role=getattr(user, "role", None),
        properties={"quotations": len(quotations), "questions": len(question_hits)},
    )
    if generates:
        await record_event(
            session,
            _WROTE,
            user_id=user.id,
            role=getattr(user, "role", None),
            properties={"quotations": len(quotations)},
        )
    await session.commit()

    return success(
        {
            "query": payload.query.strip(),
            "answer": answer,
            "answerKind": "study_suggestion" if answer else None,
            "notLegalAuthority": True,
            "generatesAnswers": generates,
            "note": note,
            "quotations": quotations,
            "questions": question_hits,
            "remainingToday": max(cap - int(used) - 1, 0),
        },
        request_id=get_request_id(),
    )
