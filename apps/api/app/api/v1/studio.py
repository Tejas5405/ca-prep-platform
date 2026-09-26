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
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import Permission, require_permission
from app.models.curriculum import Chapter, Course, Subject, SubjectComponent, Topic
from app.models.engagement import PlatformSetting
from app.models.progress import MockTest
from app.models.question import LawNotice, Question, QuestionFlag, QuestionOption, QuestionVersion
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services import billing
from app.services.audit import AuditAction, record_audit
from app.services.plan_overrides import (
    OVERRIDE_KEY,
    BillingError,
    resolved_catalogue,
    validate_override,
)
from app.services.question_history import option_rows, pin_unversioned_attempts, record_version

router = APIRouter(tags=["admin"])

_LEVELS = frozenset({"FOUNDATION", "INTERMEDIATE", "FINAL"})
_SCHEMES = frozenset({"OLD_2016", "NEW_2024", "UNMAPPED"})
_GROUPS = frozenset({"GROUP_I", "GROUP_II"})
_QUESTION_TYPES = frozenset({"MCQ", "MSQ", "TRUE_FALSE", "NUMERICAL", "DESCRIPTIVE", "CASE_STUDY"})
_DIFFICULTIES = frozenset({"EASY", "MEDIUM", "HARD"})
_OBJECTIVE = frozenset({"MCQ", "TRUE_FALSE", "NUMERICAL"})
_MOCK_KINDS = frozenset({"CHAPTER", "SUBJECT", "FULL_LENGTH", "PREVIOUS_PAPER", "CUSTOM"})
_QUESTION_STATUSES = frozenset({"DRAFT", "IN_REVIEW", "APPROVED", "PUBLISHED", "ARCHIVED"})


def _bad_id(name: str) -> Any:
    return problem(
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        title="Invalid id",
        detail=f"{name} must be a UUID.",
        type_slug="validation",
    )


def _parse_uuid(value: str, name: str) -> uuid.UUID | Any:
    try:
        return uuid.UUID(value)
    except ValueError:
        return _bad_id(name)


def _ilike_contains(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _flag(session: AsyncSession, key: str, default: bool = False) -> bool:
    row = (
        await session.execute(select(PlatformSetting).where(PlatformSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        return default
    if isinstance(row.value, dict):
        return bool(row.value.get("value", default))
    if isinstance(row.value, bool):
        return row.value
    return default


def _question_item(row: Question, *, full: bool) -> dict[str, Any]:
    text = row.text if full else row.text[:280]
    return {
        "id": str(row.id),
        "text": text,
        "truncated": not full and len(row.text) > 280,
        "explanation": row.explanation if full else None,
        "questionType": row.question_type,
        "difficulty": row.difficulty,
        "marks": row.marks,
        "negativeMarks": float(row.negative_marks),
        "correctAnswer": row.correct_answer if full else None,
        "modelAnswer": row.model_answer if full else None,
        "status": row.status,
        "isHistorical": row.is_historical,
        "financeActYear": row.finance_act_year,
        "disclaimerText": row.disclaimer_text if full else None,
        "courseId": str(row.course_id),
        "subjectId": str(row.subject_id),
        "chapterId": str(row.chapter_id) if row.chapter_id else None,
        "topicId": str(row.topic_id) if row.topic_id else None,
        "year": row.year,
        "isPremium": row.is_premium,
        "source": row.source,
        "createdAt": row.created_at,
    }


class OptionIn(StrictRequest):
    label: str = Field(min_length=1, max_length=4)
    text: str = Field(min_length=1, max_length=4000)


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


class FlagResolve(StrictRequest):
    status: str
    resolution_note: str | None = Field(default=None, max_length=2000)


_FLAG_STATUSES = frozenset({"OPEN", "TRIAGED", "FIXED", "REJECTED"})


# ---------------------------------------------------------------- questions


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
        return "A historical taxation question needs disclaimer_text, or a student sees stale law with no warning."
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
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
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


@router.post("/admin/questions", status_code=status.HTTP_201_CREATED, summary="Create a draft question")
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
            detail="A published question is corrected with revise, which keeps the old version. A patch here would change the key under attempts already scored.",
            type_slug="questions",
        )
    if payload.difficulty is not None and payload.difficulty not in _DIFFICULTIES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown difficulty",
            detail=f"Difficulty must be one of {', '.join(sorted(_DIFFICULTIES))}.",
            type_slug="questions",
        )
    next_historical = payload.is_historical if payload.is_historical is not None else row.is_historical
    next_disclaimer = (
        payload.disclaimer_text if "disclaimer_text" in values else row.disclaimer_text
    )
    next_answer = payload.correct_answer if "correct_answer" in values else row.correct_answer
    option_models = [OptionIn.model_validate(item) for item in new_options] if new_options is not None else None
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
        changes={key: True for key in values},
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
        await session.execute(
            select(QuestionVersion)
            .where(QuestionVersion.question_id == parsed)
            .order_by(QuestionVersion.version_number.desc())
        )
    ).scalars().all()
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
            detail="Edit a draft with the ordinary save. Revise is for a question students may already have sat.",
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
        option_models_for_check = [
            OptionIn(label=item.label, text=item.text) for item in existing
        ]
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
            "note": "Resolving a report does not change the question. A correction is a revision, which keeps the old version.",
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


# -------------------------------------------------------------------- mocks


class MockCreate(StrictRequest):
    course_id: UuidRef
    subject_id: UuidRef | None = None
    title: str = Field(min_length=1, max_length=300)
    kind: str
    duration_min: int = Field(default=180, ge=1, le=600)
    total_marks: int = Field(default=100, ge=1, le=1000)
    question_ids: list[UuidRef] = Field(default_factory=list)
    is_premium: bool = False
    syllabus_scheme: str = "NEW_2024"


class MockPatch(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    duration_min: int | None = Field(default=None, ge=1, le=600)
    total_marks: int | None = Field(default=None, ge=1, le=1000)
    question_ids: list[UuidRef] | None = None
    is_premium: bool | None = None


def _mock_item(row: MockTest) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "courseId": str(row.course_id),
        "subjectId": str(row.subject_id) if row.subject_id else None,
        "title": row.title,
        "kind": row.kind,
        "durationMin": row.duration_min,
        "totalMarks": row.total_marks,
        "status": row.status,
        "isPremium": row.is_premium,
        "syllabusScheme": row.syllabus_scheme,
        "questionIds": [str(value) for value in (row.question_ids or [])],
        "questionCount": len(row.question_ids or []),
        "createdAt": row.created_at,
    }


async def _known_questions(session: AsyncSession, ids: list[uuid.UUID]) -> str | None:
    if not ids:
        return None
    found = (
        await session.execute(
            select(func.count()).select_from(Question).where(
                Question.id.in_(ids), Question.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    if int(found) != len(set(ids)):
        return "Every question id must already exist. Unknown ids are not stored."
    return None


@router.get("/admin/mocks", summary="Every mock paper, including drafts")
async def list_mocks(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_TESTS)),
) -> Any:
    rows = (await session.execute(select(MockTest).order_by(MockTest.created_at.desc()))).scalars().all()
    return success({"mocks": [_mock_item(row) for row in rows]}, request_id=get_request_id())


@router.post("/admin/mocks", status_code=status.HTTP_201_CREATED, summary="Create a draft paper")
async def create_mock(
    payload: MockCreate,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_TESTS)),
) -> Any:
    if payload.kind not in _MOCK_KINDS:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown paper kind",
            detail=f"Kind must be one of {', '.join(sorted(_MOCK_KINDS))}.",
            type_slug="tests",
        )
    if payload.syllabus_scheme not in _SCHEMES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown syllabus scheme",
            detail=f"Scheme must be one of {', '.join(sorted(_SCHEMES))}.",
            type_slug="tests",
        )
    missing = await _known_questions(session, payload.question_ids)
    if missing:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown question",
            detail=missing,
            type_slug="tests",
        )
    course = await session.get(Course, payload.course_id)
    if course is None:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown course",
            detail="course_id does not match a course.",
            type_slug="tests",
        )
    row = MockTest(
        id=uuid.uuid4(),
        course_id=payload.course_id,
        subject_id=payload.subject_id,
        title=payload.title,
        kind=payload.kind,
        duration_min=payload.duration_min,
        total_marks=payload.total_marks,
        syllabus_scheme=payload.syllabus_scheme,
        status="DRAFT",
        is_premium=payload.is_premium,
        question_ids=[str(value) for value in payload.question_ids],
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        AuditAction.MOCK_CREATED,
        actor=actor,
        summary=f"Draft paper {row.title}",
        target_type="mock_test",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success(_mock_item(row), request_id=get_request_id())


@router.patch("/admin/mocks/{mock_id}", summary="Edit a draft paper")
async def update_mock(
    mock_id: str,
    payload: MockPatch,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_TESTS)),
) -> Any:
    parsed = _parse_uuid(mock_id, "mock_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    row = await session.get(MockTest, parsed)
    if row is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No such paper.",
            type_slug="tests",
        )
    if row.status == "PUBLISHED":
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Published papers are not edited here",
            detail="Students may already be sitting this paper. Compose a new draft instead.",
            type_slug="tests",
        )
    values = payload.model_dump(exclude_unset=True)
    if "question_ids" in values:
        missing = await _known_questions(session, values["question_ids"])
        if missing:
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Unknown question",
                detail=missing,
                type_slug="tests",
            )
        row.question_ids = [str(value) for value in values.pop("question_ids")]
    for key, value in values.items():
        setattr(row, key, value)
    await record_audit(
        session,
        AuditAction.MOCK_UPDATED,
        actor=actor,
        summary=f"Updated paper {row.title}",
        target_type="mock_test",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success(_mock_item(row), request_id=get_request_id())


# ---------------------------------------------------------------- curriculum


class CourseWrite(StrictRequest):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    level: str
    syllabus_scheme: str = "NEW_2024"
    description: str | None = Field(default=None, max_length=2000)


class CoursePatch(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class SubjectWrite(StrictRequest):
    course_id: UuidRef
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    group_name: str | None = None
    paper_number: int | None = Field(default=None, ge=1, le=8)
    syllabus_weight: int = Field(default=100, ge=1, le=1000)


class NamedPatch(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    is_active: bool | None = None


class ChapterWrite(StrictRequest):
    subject_id: UuidRef
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=300)
    sequence: int = Field(default=0, ge=0, le=1000)
    weightage: int = Field(default=5, ge=1, le=10)
    estimated_minutes: int = Field(default=120, ge=1, le=2000)


class TopicWrite(StrictRequest):
    chapter_id: UuidRef
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=300)
    sequence: int = Field(default=0, ge=0, le=1000)


def _course_item(row: Course) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "level": row.level,
        "syllabusScheme": row.syllabus_scheme,
        "isActive": row.is_active,
        "description": row.description,
    }


@router.get("/admin/curriculum", summary="Syllabus, including inactive rows")
async def admin_curriculum(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    """The student list hides inactive rows. This one does not, or a deactivated paper vanishes from the editor."""
    courses = (await session.execute(select(Course).order_by(Course.level, Course.code))).scalars().all()
    subjects = (await session.execute(select(Subject).order_by(Subject.code))).scalars().all()
    chapters = (await session.execute(select(Chapter).order_by(Chapter.sequence))).scalars().all()
    topics = (await session.execute(select(Topic).order_by(Topic.sequence))).scalars().all()
    return success(
        {
            "courses": [_course_item(row) for row in courses],
            "subjects": [
                {
                    "id": str(row.id),
                    "courseId": str(row.course_id),
                    "code": row.code,
                    "name": row.name,
                    "groupName": row.group_name,
                    "paperNumber": row.paper_number,
                    "isActive": row.is_active,
                }
                for row in subjects
            ],
            "chapters": [
                {
                    "id": str(row.id),
                    "subjectId": str(row.subject_id),
                    "code": row.code,
                    "name": row.name,
                    "sequence": row.sequence,
                    "weightage": row.weightage,
                    "isActive": row.is_active,
                }
                for row in chapters
                if row.deleted_at is None
            ],
            "topics": [
                {
                    "id": str(row.id),
                    "chapterId": str(row.chapter_id),
                    "code": row.code,
                    "name": row.name,
                    "sequence": row.sequence,
                    "isActive": row.is_active,
                }
                for row in topics
            ],
        },
        request_id=get_request_id(),
    )


@router.post("/admin/curriculum/courses", status_code=status.HTTP_201_CREATED, summary="Add a course")
async def create_course(
    payload: CourseWrite,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    if payload.level not in _LEVELS or payload.syllabus_scheme not in _SCHEMES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown course value",
            detail="Level must be FOUNDATION, INTERMEDIATE or FINAL. Scheme must be OLD_2016, NEW_2024 or UNMAPPED.",
            type_slug="curriculum",
        )
    row = Course(
        id=uuid.uuid4(),
        code=payload.code,
        name=payload.name,
        level=payload.level,
        syllabus_scheme=payload.syllabus_scheme,
        description=payload.description,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Course already exists",
            detail="That code is already used in this syllabus scheme.",
            type_slug="curriculum",
        )
    await record_audit(
        session,
        AuditAction.CURRICULUM_WRITTEN,
        actor=actor,
        summary=f"Created course {row.code}",
        target_type="course",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success(_course_item(row), request_id=get_request_id())


@router.patch("/admin/curriculum/courses/{course_id}", summary="Rename or retire a course")
async def update_course(
    course_id: str,
    payload: CoursePatch,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    parsed = _parse_uuid(course_id, "course_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    row = await session.get(Course, parsed)
    if row is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No such course.",
            type_slug="curriculum",
        )
    # Deactivate, do not delete: questions reference the course with RESTRICT.
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(row, key, value)
    await record_audit(
        session,
        AuditAction.CURRICULUM_WRITTEN,
        actor=actor,
        summary=f"Updated course {row.code}",
        target_type="course",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success(_course_item(row), request_id=get_request_id())


@router.post("/admin/curriculum/subjects", status_code=status.HTTP_201_CREATED, summary="Add a subject")
async def create_subject(
    payload: SubjectWrite,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    if payload.group_name is not None and payload.group_name not in _GROUPS:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown group",
            detail="group_name must be GROUP_I, GROUP_II, or omitted.",
            type_slug="curriculum",
        )
    if await session.get(Course, payload.course_id) is None:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown course",
            detail="course_id does not match a course.",
            type_slug="curriculum",
        )
    row = Subject(
        id=uuid.uuid4(),
        course_id=payload.course_id,
        code=payload.code,
        name=payload.name,
        group_name=payload.group_name,
        paper_number=payload.paper_number,
        syllabus_weight=payload.syllabus_weight,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Subject already exists",
            detail="That code is already used in this course.",
            type_slug="curriculum",
        )
    await record_audit(
        session,
        AuditAction.CURRICULUM_WRITTEN,
        actor=actor,
        summary=f"Created subject {row.code}",
        target_type="subject",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success({"id": str(row.id), "code": row.code, "name": row.name}, request_id=get_request_id())


@router.patch("/admin/curriculum/subjects/{subject_id}", summary="Rename or retire a subject")
async def update_subject(
    subject_id: str,
    payload: NamedPatch,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    return await _rename(session, actor, request, Subject, subject_id, "subject", payload)


@router.post("/admin/curriculum/chapters", status_code=status.HTTP_201_CREATED, summary="Add a chapter")
async def create_chapter(
    payload: ChapterWrite,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    if await session.get(Subject, payload.subject_id) is None:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown subject",
            detail="subject_id does not match a subject.",
            type_slug="curriculum",
        )
    row = Chapter(
        id=uuid.uuid4(),
        subject_id=payload.subject_id,
        code=payload.code,
        name=payload.name,
        sequence=payload.sequence,
        weightage=payload.weightage,
        estimated_minutes=payload.estimated_minutes,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Chapter already exists",
            detail="That code is already used in this subject.",
            type_slug="curriculum",
        )
    await record_audit(
        session,
        AuditAction.CURRICULUM_WRITTEN,
        actor=actor,
        summary=f"Created chapter {row.code}",
        target_type="chapter",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success({"id": str(row.id), "code": row.code, "name": row.name}, request_id=get_request_id())


@router.patch("/admin/curriculum/chapters/{chapter_id}", summary="Rename or retire a chapter")
async def update_chapter(
    chapter_id: str,
    payload: NamedPatch,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    return await _rename(session, actor, request, Chapter, chapter_id, "chapter", payload)


@router.post("/admin/curriculum/topics", status_code=status.HTTP_201_CREATED, summary="Add a topic")
async def create_topic(
    payload: TopicWrite,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    if await session.get(Chapter, payload.chapter_id) is None:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown chapter",
            detail="chapter_id does not match a chapter.",
            type_slug="curriculum",
        )
    row = Topic(
        id=uuid.uuid4(),
        chapter_id=payload.chapter_id,
        code=payload.code,
        name=payload.name,
        sequence=payload.sequence,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Topic already exists",
            detail="That code is already used in this chapter.",
            type_slug="curriculum",
        )
    await record_audit(
        session,
        AuditAction.CURRICULUM_WRITTEN,
        actor=actor,
        summary=f"Created topic {row.code}",
        target_type="topic",
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success({"id": str(row.id), "code": row.code, "name": row.name}, request_id=get_request_id())


@router.patch("/admin/curriculum/topics/{topic_id}", summary="Rename or retire a topic")
async def update_topic(
    topic_id: str,
    payload: NamedPatch,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    return await _rename(session, actor, request, Topic, topic_id, "topic", payload)


async def _rename(
    session: AsyncSession,
    actor: User,
    request: Request,
    model: type,
    raw_id: str,
    label: str,
    payload: NamedPatch,
) -> Any:
    parsed = _parse_uuid(raw_id, f"{label}_id")
    if not isinstance(parsed, uuid.UUID):
        return parsed
    row = await session.get(model, parsed)
    if row is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Not found",
            detail=f"No such {label}.",
            type_slug="curriculum",
        )
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(row, key, value)
    await record_audit(
        session,
        AuditAction.CURRICULUM_WRITTEN,
        actor=actor,
        summary=f"Updated {label} {getattr(row, 'code', row.id)}",
        target_type=label,
        target_id=row.id,
        request=request,
    )
    await session.commit()
    return success({"id": str(row.id), "name": row.name, "isActive": row.is_active}, request_id=get_request_id())


# --------------------------------------------------------- plans, ai, storage


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
                else "The credential is set. A bucket listing is not implemented in this client, so no count is shown."
            ),
        },
        request_id=get_request_id(),
    )


def _like_literal(value: str) -> str:
    """Escape a citation so a typed percent sign is not a wildcard."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        await session.execute(
            select(SubjectComponent).where(SubjectComponent.is_active.is_(True)).order_by(
                SubjectComponent.sort_order
            )
        )
    ).scalars().all()
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


class LawNoticeIn(StrictRequest):
    citation: str = Field(min_length=12, max_length=200)
    summary: str = Field(min_length=8, max_length=2000)


@router.get("/admin/law-notices", summary="Recorded law notices")
async def list_law_notices(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
) -> Any:
    rows = (
        await session.execute(select(LawNotice).order_by(LawNotice.created_at.desc()).limit(50))
    ).scalars().all()
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
        await session.execute(
            select(Question).where(
                Question.deleted_at.is_(None),
                Question.status == "PUBLISHED",
                or_(
                    Question.text.ilike(pattern, escape="\\"),
                    Question.explanation.ilike(pattern, escape="\\"),
                ),
            ).limit(200)
        )
    ).scalars().all()
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
                            f"Law notice: {citation}. Review required. "
                            "The answer was not changed."
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
