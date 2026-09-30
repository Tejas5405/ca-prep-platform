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

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import success
from app.core.permissions import Permission, require_permission
from app.models.progress import MockTest
from app.models.question import LawNotice, Question, QuestionFlag
from app.models.user import User
from app.schemas.base import StrictRequest
from app.services.audit import AuditAction, record_audit

from ._shared import (
    _like_literal,
)

router = APIRouter(tags=["admin"])

"""Law and amendment notices."""


class LawNoticeIn(StrictRequest):
    citation: str = Field(min_length=12, max_length=200)
    summary: str = Field(min_length=8, max_length=2000)


@router.get("/admin/law-notices", summary="Recorded law notices")
async def list_law_notices(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    rows = (
        (await session.execute(select(LawNotice).order_by(LawNotice.created_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    return success(
        {
            "notices": [
                {
                    "id": str(row.id),
                    "citation": row.citation,
                    "summary": row.summary,
                    "matched": len(row.matched_question_ids or []),
                    "mocks": len(row.affected_mock_ids or []),
                    "answersChanged": False,
                    "createdAt": row.created_at,
                }
                for row in rows
            ],
            "note": (
                "A notice flags questions whose text contains the citation. "
                "It does not rewrite them."
            ),
        },
        request_id=get_request_id(),
    )


@router.post("/admin/law-notices", summary="Flag questions that cite a provision")
async def record_law_notice(
    payload: LawNoticeIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    """Match the citation the administrator typed. Do not invent a statute.

    Matching questions get an open HISTORICAL flag. Their answers are not
    changed. Mocks that contain those questions are listed for review and are
    not rewritten, so an attempt already sat still refers to the paper it sat.
    """
    citation = payload.citation.strip()
    pattern = f"%{_like_literal(citation)}%"
    questions = (
        (
            await session.execute(
                select(Question)
                .where(
                    Question.deleted_at.is_(None),
                    Question.status == "PUBLISHED",
                    or_(
                        Question.text.ilike(pattern, escape="\\"),
                        Question.explanation.ilike(pattern, escape="\\"),
                    ),
                )
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    flagged = 0
    for question in questions:
        try:
            async with session.begin_nested():
                session.add(
                    QuestionFlag(
                        id=uuid.uuid4(),
                        question_id=question.id,
                        reported_by=actor.id,
                        reason="HISTORICAL",
                        detail=(
                            f"Law notice: {citation}. Review required. The answer was not changed."
                        ),
                        status="OPEN",
                    )
                )
                await session.flush()
            flagged += 1
        except IntegrityError:
            continue
    matched_ids = [str(question.id) for question in questions]
    matched = set(matched_ids)
    mocks = (await session.execute(select(MockTest.id, MockTest.question_ids))).all()
    affected = []
    for mock_id, raw_ids in mocks:
        ids = {str(item) for item in (raw_ids or [])}
        if matched & ids:
            affected.append(str(mock_id))
    notice = LawNotice(
        id=uuid.uuid4(),
        citation=citation,
        summary=payload.summary.strip(),
        recorded_by=actor.id,
        matched_question_ids=matched_ids,
        affected_mock_ids=affected,
        answers_changed=False,
    )
    session.add(notice)
    await record_audit(
        session,
        AuditAction.LAW_NOTICE_RECORDED,
        actor=actor,
        summary=f"Recorded a law notice matching {len(matched_ids)} questions",
        target_type="law_notice",
        target_id=notice.id,
        changes={"citation": citation, "answersChanged": False, "matched": len(matched_ids)},
        request=request,
    )
    await session.commit()
    return success(
        {
            "id": str(notice.id),
            "matched": len(matched_ids),
            "flagged": flagged,
            "mocks": affected,
            "answersChanged": False,
            "capped": len(matched_ids) == 200,
        },
        request_id=get_request_id(),
    )
