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

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.question import Question, QuestionFlag
from app.models.user import User
from app.services.audit import AuditAction, record_audit

from ._shared import (
    _FLAG_STATUSES,
    FlagResolve,
    _parse_uuid,
)

router = APIRouter(tags=["admin"])

"""The user flag queue and its resolution."""


@router.get("/admin/question-flags", summary="Reports that a question may be wrong")
async def list_flags(
    status_filter: str | None = Query(default="OPEN", alias="status", max_length=20),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    conditions = []
    if status_filter:
        if status_filter not in _FLAG_STATUSES:
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Unknown status",
                detail=f"Status must be one of {', '.join(sorted(_FLAG_STATUSES))}.",
                type_slug="questions",
            )
        conditions.append(QuestionFlag.status == status_filter)
    rows = (
        await session.execute(
            select(QuestionFlag, Question.text)
            .join(Question, Question.id == QuestionFlag.question_id)
            .where(*conditions)
            .order_by(QuestionFlag.created_at.desc())
            .limit(100)
        )
    ).all()
    return success(
        {
            "flags": [
                {
                    "id": str(flag.id),
                    "questionId": str(flag.question_id),
                    "questionText": (text or "")[:180],
                    "reason": flag.reason,
                    "detail": flag.detail,
                    "status": flag.status,
                    "resolutionNote": flag.resolution_note,
                    "createdAt": flag.created_at,
                }
                for flag, text in rows
            ],
            "note": (
                "Resolving a report does not change the question. A correction is a revision, "
                "which keeps the old version."
            ),
        },
        request_id=get_request_id(),
    )


@router.patch("/admin/question-flags/{flag_id}", summary="Triage a question report")
async def resolve_flag(
    flag_id: str,
    payload: FlagResolve,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    parsed = _parse_uuid(flag_id, "flag_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    if payload.status not in _FLAG_STATUSES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown status",
            detail=f"Status must be one of {', '.join(sorted(_FLAG_STATUSES))}.",
            type_slug="questions",
        )
    row = await session.get(QuestionFlag, parsed)
    if row is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No such report.",
            type_slug="questions",
        )
    row.status = payload.status
    row.resolution_note = payload.resolution_note
    row.resolved_by = actor.id
    await record_audit(
        session,
        AuditAction.QUESTION_FLAGGED,
        actor=actor,
        summary=f"Report {row.id} marked {row.status}",
        target_type="question_flag",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success(
        {"id": str(row.id), "status": row.status},
        request_id=get_request_id(),
    )
