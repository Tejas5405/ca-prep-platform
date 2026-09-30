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
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import Permission, require_permission
from app.models.curriculum import Subject
from app.models.question import Question, QuestionOption, QuestionVersion
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit
from app.services.question_history import option_rows, pin_unversioned_attempts, record_version

from ._shared import (
    _DIFFICULTIES,
    _OBJECTIVE,
    _QUESTION_STATUSES,
    _QUESTION_TYPES,
    OptionIn,
    _ilike_contains,
    _parse_uuid,
    _question_item,
)

router = APIRouter(tags=["admin"])

"""The question bank: read, create, edit, versions, revise."""


class QuestionRevise(StrictRequest):
    change_reason: str = Field(min_length=8, max_length=500)
    text: str | None = Field(default=None, min_length=1, max_length=20000)
    explanation: str | None = Field(default=None, max_length=20000)
    correct_answer: str | None = Field(default=None, max_length=4000)
    model_answer: str | None = Field(default=None, max_length=20000)
    disclaimer_text: str | None = Field(default=None, max_length=2000)
    finance_act_year: str | None = Field(default=None, max_length=20)
    is_historical: bool | None = None
    options: list[OptionIn] | None = None


class QuestionCreate(StrictRequest):
    course_id: UuidRef
    subject_id: UuidRef
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None
    text: str = Field(min_length=1, max_length=20000)
    question_type: str = Field(min_length=1, max_length=20)
    difficulty: str = "MEDIUM"
    marks: int = Field(default=1, ge=1, le=100)
    negative_marks: float = Field(default=0, ge=0, le=100)
    correct_answer: str | None = Field(default=None, max_length=4000)
    explanation: str | None = Field(default=None, max_length=20000)
    is_historical: bool = False
    finance_act_year: str | None = Field(default=None, max_length=20)
    disclaimer_text: str | None = Field(default=None, max_length=2000)
    year: int | None = Field(default=None, ge=1990, le=2100)
    is_premium: bool = False
    source: str | None = Field(default=None, max_length=200)
    options: list[OptionIn] | None = None


class QuestionPatch(StrictRequest):
    text: str | None = Field(default=None, min_length=1, max_length=20000)
    explanation: str | None = Field(default=None, max_length=20000)
    difficulty: str | None = None
    marks: int | None = Field(default=None, ge=1, le=100)
    negative_marks: float | None = Field(default=None, ge=0, le=100)
    correct_answer: str | None = Field(default=None, max_length=4000)
    model_answer: str | None = Field(default=None, max_length=20000)
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None
    year: int | None = Field(default=None, ge=1990, le=2100)
    source: str | None = Field(default=None, max_length=200)
    is_historical: bool | None = None
    finance_act_year: str | None = Field(default=None, max_length=20)
    disclaimer_text: str | None = Field(default=None, max_length=2000)
    is_premium: bool | None = None
    archive: bool | None = None
    options: list[OptionIn] | None = None


def _question_rules(
    *,
    question_type: str,
    correct_answer: str | None,
    is_historical: bool,
    disclaimer_text: str | None,
) -> str | None:
    if question_type not in _QUESTION_TYPES:
        return f"question_type must be one of {', '.join(sorted(_QUESTION_TYPES))}."
    if question_type in _OBJECTIVE and not (correct_answer and correct_answer.strip()):
        return "An objective question needs a correct_answer. The database rejects one without it."
    if is_historical and not (disclaimer_text and disclaimer_text.strip()):
        return (
            "A historical taxation question needs disclaimer_text, or a student sees "
            "stale law with no warning."
        )
    return None


def _option_problem(
    question_type: str,
    correct_answer: str | None,
    options: list[OptionIn] | None,
) -> str | None:
    """MCQ and MSQ are unanswerable without choices. Other types may omit them."""
    if question_type not in {"MCQ", "MSQ"}:
        return None
    if not options or len(options) < 2:
        return "An MCQ or MSQ needs at least two options, or a student has nothing to choose."
    labels = [option.label.strip().upper() for option in options]
    if any(not label.isalpha() for label in labels):
        return "Option labels must be letters, such as A or B."
    if len(labels) != len(set(labels)):
        return "Option labels must be unique."
    expected = {part.strip().upper() for part in (correct_answer or "").split(",") if part.strip()}
    if not expected or not expected <= set(labels):
        return "correct_answer must name one of the option labels."
    return None


async def _replace_options(
    session: AsyncSession,
    question_id: uuid.UUID,
    options: list[OptionIn],
    correct_answer: str | None,
) -> None:
    expected = {part.strip().upper() for part in (correct_answer or "").split(",") if part.strip()}
    await session.execute(delete(QuestionOption).where(QuestionOption.question_id == question_id))
    for index, option in enumerate(options):
        label = option.label.strip().upper()
        session.add(
            QuestionOption(
                id=uuid.uuid4(),
                question_id=question_id,
                label=label,
                text=option.text,
                is_correct=label in expected,
                sequence=index,
            )
        )
    await session.flush()


@router.get("/admin/questions", summary="Question bank")
async def list_questions(
    q: str | None = Query(default=None, max_length=200),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    course_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    is_historical: bool | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    """Drafts included. This is the editor's bank, not the student's."""
    if status_filter and status_filter not in _QUESTION_STATUSES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown status",
            detail=f"Status must be one of {', '.join(sorted(_QUESTION_STATUSES))}.",
            type_slug="questions",
        )
    conditions = [Question.deleted_at.is_(None)]
    if status_filter:
        conditions.append(Question.status == status_filter)
    if course_id:
        conditions.append(Question.course_id == course_id)
    if subject_id:
        conditions.append(Question.subject_id == subject_id)
    if is_historical is not None:
        conditions.append(Question.is_historical.is_(is_historical))
    if q and q.strip():
        conditions.append(Question.text.ilike(_ilike_contains(q.strip()), escape="\\"))

    base = select(Question).where(*conditions)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(Question.created_at.desc()).limit(limit).offset((page - 1) * limit)
            )
        )
        .scalars()
        .all()
    )
    return paginated(
        [_question_item(row, full=False) for row in rows],
        total=int(total),
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/admin/questions/{question_id}", summary="One question, including the key")
async def get_question(
    question_id: str,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    parsed = _parse_uuid(question_id, "question_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    row = await session.get(Question, parsed)
    if row is None or row.deleted_at is not None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No such question.",
            type_slug="questions",
        )
    return success(_question_item(row, full=True), request_id=get_request_id())


@router.post(
    "/admin/questions", status_code=status.HTTP_201_CREATED, summary="Create a draft question"
)
async def create_question(
    payload: QuestionCreate,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    """Always a draft. Publication is the existing verifier route, not this one."""
    if payload.difficulty not in _DIFFICULTIES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown difficulty",
            detail=f"Difficulty must be one of {', '.join(sorted(_DIFFICULTIES))}.",
            type_slug="questions",
        )
    reason = _question_rules(
        question_type=payload.question_type,
        correct_answer=payload.correct_answer,
        is_historical=payload.is_historical,
        disclaimer_text=payload.disclaimer_text,
    ) or _option_problem(payload.question_type, payload.correct_answer, payload.options)
    if reason:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Question is not valid",
            detail=reason,
            type_slug="questions",
        )
    subject = await session.get(Subject, payload.subject_id)
    if subject is None or subject.course_id != payload.course_id:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Subject does not belong to that course",
            detail="course_id and subject_id must name a real pair.",
            type_slug="questions",
        )
    row = Question(
        id=uuid.uuid4(),
        course_id=payload.course_id,
        subject_id=payload.subject_id,
        chapter_id=payload.chapter_id,
        topic_id=payload.topic_id,
        text=payload.text,
        search_text=payload.text,
        explanation=payload.explanation,
        question_type=payload.question_type,
        difficulty=payload.difficulty,
        marks=payload.marks,
        negative_marks=payload.negative_marks,
        correct_answer=payload.correct_answer,
        status="DRAFT",
        is_historical=payload.is_historical,
        finance_act_year=payload.finance_act_year,
        disclaimer_text=payload.disclaimer_text,
        year=payload.year,
        is_premium=payload.is_premium,
        source=payload.source,
        created_by=actor.id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Could not save the question",
            detail="The database rejected the row. Check the chapter, topic and answer.",
            type_slug="questions",
        )
    if payload.options:
        await _replace_options(session, row.id, payload.options, row.correct_answer)
    await record_version(session, row, actor_id=actor.id, reason="Created as a draft")
    await record_audit(
        session,
        AuditAction.QUESTION_CREATED,
        actor=actor,
        summary=f"Draft question {row.id}",
        target_type="question",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success(_question_item(row, full=True), request_id=get_request_id())


@router.patch("/admin/questions/{question_id}", summary="Edit a question that is not live")
async def update_question(
    question_id: str,
    payload: QuestionPatch,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    parsed = _parse_uuid(question_id, "question_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    row = await session.get(Question, parsed)
    if row is None or row.deleted_at is not None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No such question.",
            type_slug="questions",
        )
    values = payload.model_dump(exclude_unset=True)
    archive = values.pop("archive", None)
    new_options = values.pop("options", None)
    if row.status == "PUBLISHED" and values:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Published questions are not edited here",
            detail=(
                "A published question is corrected with revise, which keeps the old version. "
                "A patch here would change the key under attempts already scored."
            ),
            type_slug="questions",
        )
    if payload.difficulty is not None and payload.difficulty not in _DIFFICULTIES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown difficulty",
            detail=f"Difficulty must be one of {', '.join(sorted(_DIFFICULTIES))}.",
            type_slug="questions",
        )
    next_historical = (
        payload.is_historical if payload.is_historical is not None else row.is_historical
    )
    next_disclaimer = (
        payload.disclaimer_text if "disclaimer_text" in values else row.disclaimer_text
    )
    next_answer = payload.correct_answer if "correct_answer" in values else row.correct_answer
    option_models = (
        [OptionIn.model_validate(item) for item in new_options] if new_options is not None else None
    )
    reason = _question_rules(
        question_type=row.question_type,
        correct_answer=next_answer,
        is_historical=next_historical,
        disclaimer_text=next_disclaimer,
    ) or _option_problem(row.question_type, next_answer, option_models)
    if reason:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Question is not valid",
            detail=reason,
            type_slug="questions",
        )
    for key, value in values.items():
        setattr(row, key, value)
    if "text" in values:
        row.search_text = row.text
    if archive:
        row.status = "ARCHIVED"
    if option_models is not None:
        await _replace_options(session, row.id, option_models, next_answer)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Could not save the question",
            detail="The database rejected the change.",
            type_slug="questions",
        )
    await record_version(session, row, actor_id=actor.id, reason="Draft edited")
    await record_audit(
        session,
        AuditAction.QUESTION_UPDATED,
        actor=actor,
        summary=f"Updated question {row.id}",
        target_type="question",
        target_id=row.id,
        changes=dict.fromkeys(values, True),
        request=request,
    )
    await session.commit()
    return success(_question_item(row, full=True), request_id=get_request_id())


@router.get("/admin/questions/{question_id}/versions", summary="Earlier copies of a question")
async def question_versions(
    question_id: str,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    parsed = _parse_uuid(question_id, "question_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    rows = (
        (
            await session.execute(
                select(QuestionVersion)
                .where(QuestionVersion.question_id == parsed)
                .order_by(QuestionVersion.version_number.desc())
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "versions": [
                {
                    "version": row.version_number,
                    "reason": row.change_reason,
                    "changedBy": str(row.changed_by) if row.changed_by else None,
                    "createdAt": row.created_at,
                    "correctAnswer": (row.snapshot or {}).get("correctAnswer"),
                    "text": ((row.snapshot or {}).get("text") or "")[:280],
                    "status": (row.snapshot or {}).get("status"),
                }
                for row in rows
            ]
        },
        request_id=get_request_id(),
    )


@router.post("/admin/questions/{question_id}/revise", summary="Correct a published question")
async def revise_question(
    question_id: str,
    payload: QuestionRevise,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    """Keep the old key, then write the new one.

    Attempts that have no version yet are pinned to the snapshot taken before
    this edit. Their score is not recomputed. This route does not decide that a
    statute changed. The editor supplies the new text and says why.
    """
    parsed = _parse_uuid(question_id, "question_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    row = await session.get(Question, parsed)
    if row is None or row.deleted_at is not None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No such question.",
            type_slug="questions",
        )
    if row.status != "PUBLISHED":
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Only a published question is revised",
            detail=(
                "Edit a draft with the ordinary save. Revise is for a question students may "
                "already have sat."
            ),
            type_slug="questions",
        )
    previous = await record_version(
        session, row, actor_id=actor.id, reason=f"Before revision: {payload.change_reason}"
    )
    await pin_unversioned_attempts(session, row.id, previous)
    values = payload.model_dump(exclude_unset=True)
    values.pop("change_reason", None)
    new_options = values.pop("options", None)
    for key, value in values.items():
        setattr(row, key, value)
    if "text" in values:
        row.search_text = row.text
    row.verified_by = actor.id
    next_answer = row.correct_answer
    option_models = (
        [OptionIn.model_validate(item) for item in new_options] if new_options is not None else None
    )
    if option_models is None and row.question_type in {"MCQ", "MSQ"}:
        existing = await option_rows(session, row.id)
        option_models_for_check = [OptionIn(label=item.label, text=item.text) for item in existing]
    else:
        option_models_for_check = option_models
    reason = _question_rules(
        question_type=row.question_type,
        correct_answer=next_answer,
        is_historical=row.is_historical,
        disclaimer_text=row.disclaimer_text,
    ) or _option_problem(row.question_type, next_answer, option_models_for_check)
    if reason:
        await session.rollback()
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Revision is not valid",
            detail=reason,
            type_slug="questions",
        )
    if option_models is not None:
        await _replace_options(session, row.id, option_models, next_answer)
    await record_version(session, row, actor_id=actor.id, reason=payload.change_reason)
    await record_audit(
        session,
        AuditAction.QUESTION_REVISED,
        actor=actor,
        summary=f"Revised question {row.id}",
        target_type="question",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success(_question_item(row, full=True), request_id=get_request_id())
