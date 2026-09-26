"""Read-only curriculum queries.

The curriculum is REFERENCE DATA: it changes when ICAI changes a syllabus, not
when a student studies. So every query here is read-only, and every list is
ordered server-side by the column the client groups on - a picker that sorts a
syllabus itself will one day sort "Group I" after "Group II" because of a locale
difference.

Nothing here filters by user. There is no private curriculum, and inventing an
ownership check for public reference data is how a "no rows" bug gets shipped and
diagnosed as a permissions problem.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, and_, case, func, select

from app.models.curriculum import Chapter, Course, Subject, SubjectComponent, Topic


def _active_chapters() -> Any:
    """Soft-deleted chapters are excluded everywhere, so the predicate lives here."""
    return (Chapter.is_active.is_(True), Chapter.deleted_at.is_(None))


#: Foundation -> Intermediate -> Final is the order a student moves through them.
#: Written as a CASE because it is an ordering the alphabet does not know, and
#: `else_=99` so a level added to the CHECK constraint later cannot silently sort
#: into the middle of the list.
_LEVEL_ORDER = case(
    {"FOUNDATION": 1, "INTERMEDIATE": 2, "FINAL": 3},
    value=Course.level,
    else_=99,
)


class SqlCurriculumRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def courses(self, *, level: str | None = None) -> list[Course]:
        stmt: Select[Any] = select(Course).where(Course.is_active.is_(True))
        if level is not None:
            stmt = stmt.where(Course.level == level)
        # EXAM ORDER, NOT ALPHABETICAL ORDER. Sorting by the level column gives
        # FINAL, FOUNDATION, INTERMEDIATE - "FINAL" sorts before "INTERMEDIATE"
        # because of the letters, which would put the last course of a student's
        # career at the top of the picker. The CASE is the only place the order of
        # the three levels is written down.
        stmt = stmt.order_by(_LEVEL_ORDER, Course.name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def course(self, course_id: uuid.UUID) -> Course | None:
        return (
            await self._session.execute(select(Course).where(Course.id == course_id))
        ).scalar_one_or_none()

    async def subjects(
        self, *, course_id: uuid.UUID | None = None, level: str | None = None
    ) -> list[tuple[Subject, int, str]]:
        """Subjects with their active chapter count and the course level.

        The count comes back with the row because every client that lists subjects
        also shows "12 chapters" next to each one. A second round trip per row is
        the classic N+1 that only shows up once a real syllabus is loaded.

        The level comes back with the row so Intermediate can be split without a
        second query, and without guessing the level from the display name.
        """
        chapter_count = func.count(Chapter.id).label("chapter_count")
        stmt = (
            select(Subject, chapter_count, Course.level)
            # and_(*clauses), not a bare tuple: SQLAlchemy 2 rejects a tuple as an ON
            # clause, and this join is the one that decides whether soft-deleted
            # chapters are counted in a subject's chapter count.
            .join(Course, Course.id == Subject.course_id)
            .outerjoin(Chapter, and_(Chapter.subject_id == Subject.id, *_active_chapters()))
            .where(Subject.is_active.is_(True))
            .group_by(Subject.id, Course.level)
        )
        if course_id is not None:
            stmt = stmt.where(Subject.course_id == course_id)
        if level is not None:
            stmt = stmt.where(Course.level == level)
        # COALESCE rather than NULLS LAST: paper_number is null for subjects that
        # are not a numbered paper, and they belong at the end.
        stmt = stmt.order_by(func.coalesce(Subject.paper_number, 99), Subject.name)
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], int(row[1]), str(row[2])) for row in rows]

    async def components_for(
        self, subject_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[SubjectComponent]]:
        if not subject_ids:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(SubjectComponent)
                    .where(
                        SubjectComponent.parent_subject_id.in_(subject_ids),
                        SubjectComponent.is_active.is_(True),
                        SubjectComponent.is_filterable.is_(True),
                    )
                    .order_by(SubjectComponent.sort_order, SubjectComponent.display_name)
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[uuid.UUID, list[SubjectComponent]] = {}
        for row in rows:
            grouped.setdefault(row.parent_subject_id, []).append(row)
        return grouped

    async def component_chapter_counts(
        self, component_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not component_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Chapter.subject_component_id, func.count(Chapter.id))
                .where(Chapter.subject_component_id.in_(component_ids), *_active_chapters())
                .group_by(Chapter.subject_component_id)
            )
        ).all()
        return {row[0]: int(row[1]) for row in rows if row[0] is not None}

    async def component(self, component_id: uuid.UUID) -> SubjectComponent | None:
        return (
            await self._session.execute(
                select(SubjectComponent).where(
                    SubjectComponent.id == component_id,
                    SubjectComponent.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def chapters_for_component(self, component_id: uuid.UUID) -> list[tuple[Chapter, int]]:
        topic_count = func.count(Topic.id).label("topic_count")
        stmt = (
            select(Chapter, topic_count)
            .outerjoin(Topic, and_(Topic.chapter_id == Chapter.id, Topic.is_active.is_(True)))
            .where(Chapter.subject_component_id == component_id, *_active_chapters())
            .group_by(Chapter.id)
            .order_by(Chapter.sequence, Chapter.name)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], int(row[1])) for row in rows]

    async def subject(self, subject_id: uuid.UUID) -> Subject | None:
        return (
            await self._session.execute(select(Subject).where(Subject.id == subject_id))
        ).scalar_one_or_none()

    async def chapters(self, subject_id: uuid.UUID) -> list[tuple[Chapter, int]]:
        topic_count = func.count(Topic.id).label("topic_count")
        stmt = (
            select(Chapter, topic_count)
            .outerjoin(Topic, and_(Topic.chapter_id == Chapter.id, Topic.is_active.is_(True)))
            .where(Chapter.subject_id == subject_id, *_active_chapters())
            .group_by(Chapter.id)
            .order_by(Chapter.sequence, Chapter.name)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], int(row[1])) for row in rows]

    async def chapter(self, chapter_id: uuid.UUID) -> Chapter | None:
        return (
            await self._session.execute(
                select(Chapter).where(Chapter.id == chapter_id, *_active_chapters())
            )
        ).scalar_one_or_none()

    async def topics(self, chapter_id: uuid.UUID) -> list[Topic]:
        stmt = (
            select(Topic)
            .where(Topic.chapter_id == chapter_id, Topic.is_active.is_(True))
            .order_by(Topic.sequence, Topic.name)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def published_exam_sessions(self, limit: int = 12) -> list[Any]:
        """Upcoming published attempts, newest first.

        The landing page's countdown data comes from here rather than from a
        constant in the frontend: an exam calendar that is wrong on the day the
        exam moves is worse than no calendar.
        """
        from app.models.question import ExamSession

        stmt = (
            select(ExamSession)
            .where(ExamSession.is_published.is_(True))
            .order_by(ExamSession.year.desc(), ExamSession.month)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
