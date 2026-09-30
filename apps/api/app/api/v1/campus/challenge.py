"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.engagement import AnalyticsEvent
from app.models.progress import Referral
from app.models.question import Question, QuestionOption
from app.models.user import User
from app.repositories.practice import grade
from app.repositories.progress import SqlProgressRepository
from app.schemas.base import StrictRequest
from app.services.gamification import PointsReason

from ._shared import (
    _CHALLENGE,
    _day_start,
)

router = APIRouter(tags=["campus"])

"""The daily challenge and referrals."""


class ChallengeIn(StrictRequest):
    chosen_option: str = Field(min_length=1, max_length=8)


class RedeemIn(StrictRequest):
    code: str = Field(min_length=4, max_length=16)


async def _todays_question(session: AsyncSession) -> Question | None:
    rows = (
        (
            await session.execute(
                select(Question)
                .where(
                    Question.status == "PUBLISHED",
                    Question.question_type == "MCQ",
                    Question.deleted_at.is_(None),
                )
                .order_by(Question.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    return rows[datetime.now(UTC).date().toordinal() % len(rows)]


@router.get("/campus/challenge", summary="Today's published question")
async def daily_challenge(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    question = await _todays_question(session)
    if question is None:
        return success(
            {
                "available": False,
                "question": None,
                "note": "No published MCQ is in the bank, so no challenge was invented.",
            },
            request_id=get_request_id(),
        )
    options = (
        await session.execute(
            select(QuestionOption.label, QuestionOption.text)
            .where(QuestionOption.question_id == question.id)
            .order_by(QuestionOption.label)
        )
    ).all()
    answered = (
        await session.execute(
            select(AnalyticsEvent.properties).where(
                AnalyticsEvent.user_id == user.id,
                AnalyticsEvent.name == _CHALLENGE,
                AnalyticsEvent.created_at >= _day_start(),
            )
        )
    ).scalar_one_or_none()
    return success(
        {
            "available": True,
            "question": {
                "id": str(question.id),
                "text": question.text,
                "options": [{"label": label, "text": text} for label, text in options],
            },
            "alreadyAnswered": answered is not None,
            "correctAnswerIncluded": False,
        },
        request_id=get_request_id(),
    )


@router.post("/campus/challenge", summary="Answer today's question once")
async def answer_challenge(
    payload: ChallengeIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    prior = (
        await session.execute(
            select(AnalyticsEvent).where(
                AnalyticsEvent.user_id == user.id,
                AnalyticsEvent.name == _CHALLENGE,
                AnalyticsEvent.created_at >= _day_start(),
            )
        )
    ).scalar_one_or_none()
    if prior is not None:
        props = prior.properties or {}
        return success(
            {
                "alreadyAnswered": True,
                "isCorrect": props.get("isCorrect"),
                "pointsAwarded": 0,
                "note": "Today's challenge is already answered. Points are not awarded twice.",
            },
            request_id=get_request_id(),
        )
    question = await _todays_question(session)
    if question is None:
        return problem(
            status=404,
            title="No challenge",
            detail="No published MCQ is in the bank.",
            type_slug="campus",
        )
    chosen = payload.chosen_option.strip().upper()
    correct = grade(question, chosen)
    points = 0
    if correct is True:
        points = await SqlProgressRepository(session).award(
            user_id=user.id,
            reason=PointsReason.DAILY_CHALLENGE_CORRECT,
            reference_id=datetime.now(UTC).date().isoformat(),
        )
    session.add(
        AnalyticsEvent(
            id=uuid.uuid4(),
            name=_CHALLENGE,
            user_id=user.id,
            role=getattr(user, "role", None),
            properties={"questionId": str(question.id), "isCorrect": correct, "chosen": chosen},
        )
    )
    await session.commit()
    return success(
        {
            "alreadyAnswered": False,
            "isCorrect": correct,
            "pointsAwarded": points,
            "correctAnswer": question.correct_answer if correct is not None else None,
            "note": "Marked against the published key. This is not a new ruling.",
        },
        request_id=get_request_id(),
    )


async def _ensure_code(session: AsyncSession, user: User) -> str:
    if user.referral_code:
        return user.referral_code
    for _ in range(5):
        code = secrets.token_hex(4).upper()
        user.referral_code = code
        try:
            await session.commit()
            return code
        except IntegrityError:
            await session.rollback()
            await session.refresh(user)
    raise RuntimeError("could not allocate a referral code")


@router.get("/campus/referrals", summary="Your referral code and counts")
async def referral_status(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    code = await _ensure_code(session, user)
    signups = (
        await session.execute(
            select(func.count()).select_from(Referral).where(Referral.referrer_user_id == user.id)
        )
    ).scalar_one()
    converted = (
        await session.execute(
            select(func.count())
            .select_from(Referral)
            .where(Referral.referrer_user_id == user.id, Referral.converted_at.is_not(None))
        )
    ).scalar_one()
    return success(
        {
            "code": code,
            "signups": int(signups),
            "conversions": int(converted),
            "premiumGranted": False,
            "note": "Sharing the code records a signup. It does not grant Premium.",
        },
        request_id=get_request_id(),
    )


@router.post("/campus/referrals/redeem", summary="Record a referral without granting Premium")
async def redeem_referral(
    payload: RedeemIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    code = payload.code.strip().upper()
    referrer = (
        await session.execute(select(User).where(User.referral_code == code))
    ).scalar_one_or_none()
    if referrer is None:
        return problem(
            status=404,
            title="Code not found",
            detail="That referral code does not exist.",
            type_slug="campus",
        )
    if referrer.id == user.id:
        return problem(
            status=422,
            title="Own code",
            detail="You cannot redeem your own code.",
            type_slug="campus",
        )
    existing = (
        await session.execute(select(Referral).where(Referral.referred_user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        return success(
            {"recorded": True, "alreadyRecorded": True, "premiumGranted": False},
            request_id=get_request_id(),
        )
    session.add(
        Referral(
            id=uuid.uuid4(),
            referrer_user_id=referrer.id,
            referred_user_id=user.id,
            code_used=code,
            signed_up_at=datetime.now(UTC),
            converted_at=None,
            reward_granted=False,
        )
    )
    await session.commit()
    return success(
        {
            "recorded": True,
            "alreadyRecorded": False,
            "premiumGranted": False,
            "note": "The relationship is saved. Premium is not granted until a paid conversion, and this route does not record one.",
        },
        request_id=get_request_id(),
    )
