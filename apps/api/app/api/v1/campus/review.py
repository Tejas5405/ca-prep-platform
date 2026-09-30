"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.question import LawNotice, Question, QuestionFlag
from app.models.user import User
from app.schemas.base import StrictRequest
from app.services.audit import AuditAction, record_audit

from ._shared import (
    _REVIEW_STATES,
    _uuid,
)

router = APIRouter(tags=["campus"])

"""The publish review queue."""


class ReviewStateIn(StrictRequest):
    review_state: str = Field(min_length=7, max_length=32)


@router.get("/admin/review-queue", summary="Questions waiting on a review state")
async def review_queue(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    rows = (
        await session.execute(
            select(Question.id, Question.review_state, Question.status, Question.text)
            .where(Question.review_state != "CURRENT")
            .order_by(Question.updated_at.desc())
            .limit(100)
        )
    ).all()
    return success(
        {
            "questions": [
                {
                    "id": str(row.id),
                    "reviewState": row.review_state,
                    "status": row.status,
                    "excerpt": (row.text or "")[:240],
                }
                for row in rows
            ],
            "answersChanged": False,
            "note": "A review state does not publish a question and does not change its answer.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/review-queue/run", summary="Mark questions that need another look")
async def reverify(
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    """Set review states from open flags and law notices. Do not touch answers."""
    flags = (
        await session.execute(
            select(QuestionFlag.question_id, QuestionFlag.reason).where(
                QuestionFlag.status == "OPEN"
            )
        )
    ).all()
    needs_update: set[uuid.UUID] = set()
    pending: set[uuid.UUID] = set()
    for question_id, reason in flags:
        if reason == "HISTORICAL":
            needs_update.add(question_id)
        else:
            pending.add(question_id)
    notices = (await session.execute(select(LawNotice.matched_question_ids))).scalars().all()
    for raw in notices:
        for item in raw or []:
            parsed = _uuid(str(item))
            if parsed is not None:
                needs_update.add(parsed)
    pending -= needs_update
    marked_update = 0
    marked_pending = 0
    if needs_update:
        result = await session.execute(
            update(Question)
            .where(Question.id.in_(needs_update), Question.review_state != "NEEDS_UPDATE")
            .values(review_state="NEEDS_UPDATE")
        )
        marked_update = result.rowcount or 0
    if pending:
        result = await session.execute(
            update(Question)
            .where(Question.id.in_(pending), Question.review_state == "CURRENT")
            .values(review_state="PENDING_ADMIN_REVIEW")
        )
        marked_pending = result.rowcount or 0
    await record_audit(
        session,
        AuditAction.QUESTION_REVIEWED,
        actor=actor,
        summary=f"Review states marked: {marked_update} need update, {marked_pending} pending",
        target_type="question",
        target_id="reverify",
        changes={
            "needsUpdate": marked_update,
            "pendingReview": marked_pending,
            "answersChanged": False,
        },
    )
    await session.commit()
    return success(
        {
            "needsUpdate": marked_update,
            "pendingReview": marked_pending,
            "answersChanged": False,
            "publishedAutomatically": False,
            "note": "Open historical flags and law-notice matches are NEEDS_UPDATE. Other open flags are PENDING_ADMIN_REVIEW. Answers were not rewritten.",
        },
        request_id=get_request_id(),
    )


@router.patch("/admin/questions/{question_id}/review-state", summary="Set a review state")
async def set_review_state(
    question_id: str,
    payload: ReviewStateIn,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    if payload.review_state not in _REVIEW_STATES:
        return problem(
            status=422,
            title="Unknown review state",
            detail="Use CURRENT, VERIFICATION_REQUIRED, NEEDS_UPDATE, or PENDING_ADMIN_REVIEW.",
            type_slug="campus",
        )
    parsed = _uuid(question_id)
    question = await session.get(Question, parsed) if parsed else None
    if question is None:
        return problem(
            status=404,
            title="Question not found",
            detail="That question does not exist.",
            type_slug="campus",
        )
    question.review_state = payload.review_state
    await record_audit(
        session,
        AuditAction.QUESTION_REVIEWED,
        actor=actor,
        summary=f"Review state set to {payload.review_state}",
        target_type="question",
        target_id=str(question.id),
        changes={"reviewState": payload.review_state, "answersChanged": False},
    )
    await session.commit()
    return success(
        {
            "id": str(question.id),
            "reviewState": question.review_state,
            "status": question.status,
            "answersChanged": False,
        },
        request_id=get_request_id(),
    )
