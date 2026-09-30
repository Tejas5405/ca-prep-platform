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
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.curriculum import Course
from app.models.progress import MockTest
from app.models.question import Question
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit

from ._shared import (
    _MOCK_KINDS,
    _SCHEMES,
    _parse_uuid,
)

router = APIRouter(tags=["admin"])

"""Mock papers owned by the admin."""


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
            select(func.count())
            .select_from(Question)
            .where(Question.id.in_(ids), Question.deleted_at.is_(None))
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
    rows = (
        (await session.execute(select(MockTest).order_by(MockTest.created_at.desc())))
        .scalars()
        .all()
    )
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
