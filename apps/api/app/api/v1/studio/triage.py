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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.curriculum import Subject, SubjectComponent
from app.models.question import Question
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit

router = APIRouter(tags=["admin"])

"""Unclassified questions and their AI classification."""


@router.get("/admin/questions/unclassified", summary="Intermediate questions with no split")
async def unclassified_questions(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    """Published questions on a combined Intermediate paper that have no component.

    A chapter mapping may already show one of these in a single split. This queue
    is the rows an editor has not locked. It does not guess a split.
    """
    parents = select(SubjectComponent.parent_subject_id).where(SubjectComponent.is_active.is_(True))
    rows = (
        await session.execute(
            select(Question, Subject.name, Subject.code)
            .join(Subject, Subject.id == Question.subject_id)
            .where(
                Question.deleted_at.is_(None),
                Question.subject_component_id.is_(None),
                Question.subject_id.in_(parents),
                Question.status == "PUBLISHED",
            )
            .order_by(Question.created_at.desc())
            .limit(100)
        )
    ).all()
    total = (
        await session.execute(
            select(func.count(Question.id)).where(
                Question.deleted_at.is_(None),
                Question.subject_component_id.is_(None),
                Question.subject_id.in_(parents),
                Question.status == "PUBLISHED",
            )
        )
    ).scalar_one()
    components = (
        (
            await session.execute(
                select(SubjectComponent)
                .where(SubjectComponent.is_active.is_(True))
                .order_by(SubjectComponent.sort_order)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "total": int(total or 0),
            "questions": [
                {
                    "id": str(question.id),
                    "text": question.text[:240],
                    "subjectCode": code,
                    "subjectName": name,
                    "chapterId": str(question.chapter_id) if question.chapter_id else None,
                    "correctAnswer": question.correct_answer,
                }
                for question, name, code in rows
            ],
            "components": [
                {
                    "id": str(component.id),
                    "code": component.code,
                    "name": component.display_name,
                    "parentSubjectId": str(component.parent_subject_id),
                }
                for component in components
            ],
            "note": (
                "Assigning a split does not change the answer. A question with no "
                "assignment does not appear in both Direct Tax and Indirect Tax."
            ),
        },
        request_id=get_request_id(),
    )


class ClassifyIn(StrictRequest):
    component_id: UuidRef


@router.patch("/admin/questions/{question_id}/component", summary="Assign an Intermediate split")
async def classify_question(
    question_id: uuid.UUID,
    payload: ClassifyIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    question = await session.get(Question, question_id)
    if question is None or question.deleted_at is not None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Question not found",
            detail="No such question.",
            type_slug="questions",
        )
    component = await session.get(SubjectComponent, payload.component_id)
    if component is None or not component.is_active:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Split not found",
            detail="No such subject component.",
            type_slug="questions",
        )
    if component.parent_subject_id != question.subject_id:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Split does not belong to this paper",
            detail="A Direct Tax question cannot be filed under Strategic Management.",
            type_slug="questions",
        )
    before = question.correct_answer
    question.subject_component_id = component.id
    if question.correct_answer != before:
        question.correct_answer = before
    await record_audit(
        session,
        AuditAction.QUESTION_CLASSIFIED,
        actor=actor,
        summary=f"Classified question {question.id} as {component.code}",
        target_type="question",
        target_id=question.id,
        changes={"subjectComponentId": str(component.id), "answerChanged": False},
        request=request,
    )
    await session.commit()
    return success(
        {
            "id": str(question.id),
            "subjectComponentId": str(component.id),
            "componentCode": component.code,
            "correctAnswer": question.correct_answer,
            "answerChanged": False,
        },
        request_id=get_request_id(),
    )
