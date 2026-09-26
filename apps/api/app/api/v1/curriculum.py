"""Curriculum endpoints - the syllabus a student picks from.

WHY THESE ARE AUTHENTICATED. The syllabus is not secret, but it is also not
marketing: it exists to populate a signed-in student's pickers. Leaving it public
would put the platform's editorial structure (which chapters it considers worth
weighting, how long it thinks each takes) into every crawler's index, and would
mean an unauthenticated endpoint that can be hammered for free.

NOTHING HERE IS PAGINATED. A course has a handful of subjects and a subject has
tens of chapters; page/size parameters on a list that is bounded by a syllabus
would be protocol for its own sake. The one list that could grow - questions - is
paginated in the practice router, where it belongs.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.core.permissions import Permission, permissions_for
from app.models.curriculum import Chapter, Course, Subject, SubjectComponent, Topic
from app.models.user import User
from app.repositories.curriculum import SqlCurriculumRepository

router = APIRouter(tags=["curriculum"])


def _course(course: Course) -> dict[str, Any]:
    return {
        "id": str(course.id),
        "code": course.code,
        "name": course.name,
        "level": course.level,
        "syllabusScheme": course.syllabus_scheme,
        "description": course.description,
    }


def _subject(subject: Subject, chapter_count: int) -> dict[str, Any]:
    return {
        "id": str(subject.id),
        "courseId": str(subject.course_id),
        "code": subject.code,
        "name": subject.name,
        "groupName": subject.group_name,
        "paperNumber": subject.paper_number,
        "syllabusWeight": subject.syllabus_weight,
        "chapterCount": chapter_count,
        "type": "subject",
        "parentSubjectCode": None,
        "parentSubjectId": None,
    }


def _component(component: SubjectComponent, subject: Subject, chapter_count: int) -> dict[str, Any]:
    return {
        "id": str(component.id),
        "courseId": str(subject.course_id),
        "code": component.code,
        "name": component.display_name,
        "groupName": subject.group_name,
        "paperNumber": subject.paper_number,
        "syllabusWeight": subject.syllabus_weight,
        "chapterCount": chapter_count,
        "type": "subject_component",
        "parentSubjectCode": subject.code,
        "parentSubjectId": str(subject.id),
    }


def _staff(user: User) -> bool:
    granted = permissions_for(user.role)
    return bool(
        granted
        & {Permission.MANAGE_QUESTIONS, Permission.MANAGE_CURRICULUM, Permission.VIEW_CONTENT}
    )


def _chapter(chapter: Chapter, topic_count: int) -> dict[str, Any]:
    return {
        "id": str(chapter.id),
        "subjectId": str(chapter.subject_id),
        "code": chapter.code,
        "name": chapter.name,
        "sequence": chapter.sequence,
        "weightage": chapter.weightage,
        "estimatedMinutes": chapter.estimated_minutes,
        "topicCount": topic_count,
    }


def _topic(topic: Topic) -> dict[str, Any]:
    return {
        "id": str(topic.id),
        "chapterId": str(topic.chapter_id),
        "code": topic.code,
        "name": topic.name,
        "sequence": topic.sequence,
    }


@router.get("/curriculum/courses", summary="List courses")
async def list_courses(
    level: str | None = None,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Foundation, Intermediate and Final, in exam order.

    ``user`` is taken as a dependency and otherwise unused: the dependency is what
    provisions the account row on a first request, so the very first screen after
    sign-in cannot be the one that fails.
    """
    courses = await SqlCurriculumRepository(session).courses(level=level)
    return success(
        {"courses": [_course(course) for course in courses]},
        request_id=get_request_id(),
    )


@router.get("/curriculum/subjects", summary="List subjects, optionally by course")
async def list_subjects(
    course_id: str | None = None,
    level: str | None = None,
    include_parents: bool = False,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    parsed_course_id = None
    if course_id is not None:
        try:
            parsed_course_id = uuid.UUID(course_id)
        except ValueError:
            # 422 rather than 500, and naming the parameter: a client that sends a
            # slug where an id belongs should be able to fix it from the message.
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Invalid course id",
                detail="course_id must be a UUID.",
                type_slug="curriculum",
            )

    repo = SqlCurriculumRepository(session)
    rows = await repo.subjects(course_id=parsed_course_id, level=level)
    # A student cannot opt back into the combined paper by adding a query
    # parameter. Staff can, because questions are still filed under the parent.
    show_parents = include_parents and _staff(user)
    components = await repo.components_for([subject.id for subject, _, _ in rows])
    counts = await repo.component_chapter_counts(
        [component.id for kids in components.values() for component in kids]
    )
    listed: list[dict[str, Any]] = []
    for subject, count, course_level in rows:
        kids = components.get(subject.id, [])
        split = course_level == "INTERMEDIATE" and bool(kids)
        if split and not show_parents:
            for component in kids:
                listed.append(_component(component, subject, counts.get(component.id, 0)))
            continue
        listed.append(_subject(subject, count))
        if split and show_parents:
            for component in kids:
                listed.append(_component(component, subject, counts.get(component.id, 0)))
    return success({"subjects": listed}, request_id=get_request_id())


@router.get("/curriculum/chapters", summary="List chapters of a subject")
async def list_chapters(
    subject_id: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    try:
        subject_id = uuid.UUID(subject_id)
    except ValueError:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid subject id",
            detail="subject_id must be a UUID.",
            type_slug="curriculum",
        )

    repo = SqlCurriculumRepository(session)
    if await repo.subject(subject_id) is not None:
        rows = await repo.chapters(subject_id)
    else:
        # A student picker sends a split id, not a paper id. The chapters of that
        # split are the ones mapped to it. An unknown id is still a 404.
        component = await repo.component(subject_id)
        if component is None:
            return problem(
                status=status.HTTP_404_NOT_FOUND,
                title="Subject not found",
                detail="No such subject in the active syllabus.",
                type_slug="curriculum",
            )
        rows = await repo.chapters_for_component(component.id)
    return success(
        {"chapters": [_chapter(chapter, count) for chapter, count in rows]},
        request_id=get_request_id(),
    )


@router.get("/curriculum/chapters/{chapter_id}/topics", summary="List topics of a chapter")
async def list_topics(
    chapter_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    topics = await SqlCurriculumRepository(session).topics(chapter_id)
    return success(
        {"topics": [_topic(topic) for topic in topics]},
        request_id=get_request_id(),
    )
