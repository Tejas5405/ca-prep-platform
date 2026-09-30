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
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.curriculum import Chapter, Course, Subject, Topic
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit

from ._shared import (
    _GROUPS,
    _LEVELS,
    _SCHEMES,
    _parse_uuid,
)

router = APIRouter(tags=["admin"])

"""Courses, subjects, chapters and topics - the whole admin tree."""


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
    """The student list hides inactive rows.

    This one does not, or a deactivated paper vanishes from the editor.
    """
    courses = (
        (await session.execute(select(Course).order_by(Course.level, Course.code))).scalars().all()
    )
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


@router.post(
    "/admin/curriculum/courses", status_code=status.HTTP_201_CREATED, summary="Add a course"
)
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
            detail=(
                "Level must be FOUNDATION, INTERMEDIATE or FINAL. Scheme must be OLD_2016, "
                "NEW_2024 or UNMAPPED."
            ),
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


@router.post(
    "/admin/curriculum/subjects", status_code=status.HTTP_201_CREATED, summary="Add a subject"
)
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
    return success(
        {"id": str(row.id), "code": row.code, "name": row.name},
        request_id=get_request_id(),
    )


@router.patch("/admin/curriculum/subjects/{subject_id}", summary="Rename or retire a subject")
async def update_subject(
    subject_id: str,
    payload: NamedPatch,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CURRICULUM)),
) -> Any:
    return await _rename(session, actor, request, Subject, subject_id, "subject", payload)


@router.post(
    "/admin/curriculum/chapters", status_code=status.HTTP_201_CREATED, summary="Add a chapter"
)
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
    return success(
        {"id": str(row.id), "code": row.code, "name": row.name},
        request_id=get_request_id(),
    )


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
    return success(
        {"id": str(row.id), "code": row.code, "name": row.name},
        request_id=get_request_id(),
    )


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
    return success(
        {"id": str(row.id), "name": row.name, "isActive": row.is_active},
        request_id=get_request_id(),
    )
