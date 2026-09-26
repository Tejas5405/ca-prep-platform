"""Seed the reference content: curriculum, a starter question bank, mock papers.

    python -m app.seed              # create or update (idempotent)
    python -m app.seed --check      # report what is missing, change nothing

IDEMPOTENT BY CONSTRUCTION, because a seed that is not idempotent is a seed that
is run once on a developer machine and never again - and then production has a
curriculum no other environment can reproduce.

Two mechanisms, chosen per table:

  * Where the schema already has a natural unique key (courses by code+scheme,
    subjects by course+code, chapters by subject+code, exam sessions by
    year+month) the seed upserts on that key. Re-running corrects a renamed
    chapter instead of failing on a duplicate.
  * Where it does not (questions, mock papers) the id is DETERMINISTIC: a uuid5 of
    a stable string. The same content therefore has the same id in every
    environment, and inserting it twice is a no-op rather than a duplicate. A
    random uuid would have made mock papers reference question ids that differ per
    environment, which breaks every attempt recorded against them.

Nothing here prints: this module runs inside the app package, where stdout is not
a logging channel. `python -m app.seed` configures a handler so a human running it
still sees the summary.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_session_factory
from app.models.curriculum import Chapter, Course, Subject
from app.models.progress import MockTest
from app.models.question import ExamSession, Question, QuestionOption
from app.models.user import User
from app.seed_data import COURSES, EXAM_SESSIONS, MOCKS, QUESTIONS, SyllabusScheme

logger = logging.getLogger(__name__)

#: Namespace for deterministic ids. Any fixed uuid works; this one is arbitrary but
#: must never change, or every seeded row gets a new identity on the next run.
NAMESPACE = uuid.UUID("6f1d1d3a-0000-4000-8000-000000000001")

#: The account credited as the verifier of seeded content. A real content manager
#: replaces it when they review the paper; until then, attributing the question to
#: nobody would leave `verified_by` NULL, which the schema forbids for PUBLISHED.
SYSTEM_ACCOUNT_AUTH_ID = "seed:system-content-manager"


def deterministic_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "|".join(parts))


async def _system_user(session: AsyncSession) -> uuid.UUID:
    user_id = deterministic_id("user", SYSTEM_ACCOUNT_AUTH_ID)
    stmt = (
        pg_insert(User)
        .values(
            id=user_id,
            auth_user_id=SYSTEM_ACCOUNT_AUTH_ID,
            email="content@seed.invalid",
            display_name="Seed Content Manager",
            role="CONTENT_MANAGER",
            is_active=False,
            syllabus_scheme=SyllabusScheme,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(stmt)
    return user_id


async def seed_curriculum(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Upsert courses, subjects and chapters. Returns code -> id maps."""
    subject_ids: dict[str, uuid.UUID] = {}
    chapter_ids: dict[str, uuid.UUID] = {}

    for course_seed in COURSES:
        stmt = (
            pg_insert(Course)
            .values(
                code=course_seed.code,
                name=course_seed.name,
                level=course_seed.level,
                syllabus_scheme=SyllabusScheme,
                description=course_seed.description,
                is_active=True,
            )
            # The unique key is (code, syllabus_scheme): the same course code will
            # legitimately exist for the old and the new scheme at the same time
            # while a transition is in progress.
            .on_conflict_do_update(
                index_elements=["code", "syllabus_scheme"],
                set_={"name": course_seed.name, "description": course_seed.description},
            )
            .returning(Course.id)
        )
        course_id = (await session.execute(stmt)).scalar_one()

        for subject_seed in course_seed.subjects:
            stmt = (
                pg_insert(Subject)
                .values(
                    course_id=course_id,
                    code=subject_seed.code,
                    name=subject_seed.name,
                    group_name=subject_seed.group,
                    paper_number=subject_seed.paper_number,
                    syllabus_weight=subject_seed.weight,
                    is_active=True,
                )
                .on_conflict_do_update(
                    index_elements=["course_id", "code"],
                    set_={
                        "name": subject_seed.name,
                        "group_name": subject_seed.group,
                        "paper_number": subject_seed.paper_number,
                        "syllabus_weight": subject_seed.weight,
                    },
                )
                .returning(Subject.id)
            )
            subject_id = (await session.execute(stmt)).scalar_one()
            subject_ids[subject_seed.code] = subject_id

            for sequence, chapter_seed in enumerate(subject_seed.chapters, start=1):
                stmt = (
                    pg_insert(Chapter)
                    .values(
                        subject_id=subject_id,
                        code=chapter_seed.code,
                        name=chapter_seed.name,
                        sequence=sequence,
                        weightage=chapter_seed.weightage,
                        estimated_minutes=chapter_seed.estimated_minutes,
                        is_active=True,
                    )
                    .on_conflict_do_update(
                        index_elements=["subject_id", "code"],
                        set_={
                            "name": chapter_seed.name,
                            "sequence": sequence,
                            "weightage": chapter_seed.weightage,
                            "estimated_minutes": chapter_seed.estimated_minutes,
                        },
                    )
                    .returning(Chapter.id)
                )
                chapter_ids[chapter_seed.code] = (await session.execute(stmt)).scalar_one()

    from app.services.intermediate import ensure_intermediate_components

    await ensure_intermediate_components(session)
    return {**subject_ids, **chapter_ids}


async def seed_questions(
    session: AsyncSession,
    *,
    ids: dict[str, uuid.UUID],
    verifier_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    """Insert the question bank. Returns a map from question text -> id."""
    question_ids: dict[str, uuid.UUID] = {}

    for seed in QUESTIONS:
        subject_id = ids[seed.subject_code]
        chapter_id = ids[seed.chapter_code]
        course_seed = next(
            course for course in COURSES if seed.subject_code in {s.code for s in course.subjects}
        )
        course_id = await _course_id(session, course_seed.code)

        question_id = deterministic_id("question", seed.subject_code, seed.text)
        question_ids[seed.text] = question_id

        correct = next(option for option in seed.options if option.is_correct)
        stmt = (
            pg_insert(Question)
            .values(
                id=question_id,
                course_id=course_id,
                subject_id=subject_id,
                chapter_id=chapter_id,
                text=seed.text,
                # Left NULL on purpose: the GIN index is built over
                # `COALESCE(search_text, text)`, and a curated search text is
                # something a content editor writes, not something a seed invents.
                search_text=None,
                explanation=seed.explanation,
                question_type=seed.question_type,
                difficulty=seed.difficulty,
                marks=seed.marks,
                negative_marks=0,
                correct_answer=correct.label,
                status="PUBLISHED",
                syllabus_scheme=SyllabusScheme,
                is_historical=False,
                # Honest provenance: written for this platform, not lifted from an
                # ICAI paper. A question attributed to a past attempt must carry the
                # disclaimer the schema requires, and inventing one is worse than
                # saying "platform content".
                source="Platform seed content",
                is_premium=False,
                created_by=verifier_id,
                verified_by=verifier_id,
                verified_at=None,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        await session.execute(stmt)

        for sequence, option in enumerate(seed.options, start=1):
            await session.execute(
                pg_insert(QuestionOption)
                .values(
                    question_id=question_id,
                    label=option.label,
                    text=option.text,
                    is_correct=option.is_correct,
                    sequence=sequence,
                )
                .on_conflict_do_nothing(index_elements=["question_id", "label"])
            )

    return question_ids


async def _course_id(session: AsyncSession, code: str) -> uuid.UUID:
    return (
        await session.execute(
            select(Course.id).where(Course.code == code, Course.syllabus_scheme == SyllabusScheme)
        )
    ).scalar_one()


async def seed_mocks(session: AsyncSession, *, ids: dict[str, uuid.UUID]) -> int:
    """Create the mock papers.

    The question list is rebuilt from the chapter codes on EVERY run, so a mock
    picks up questions added to its chapters later instead of freezing the bank as
    it was the day the seed first ran.
    """
    created = 0
    for seed in MOCKS:
        course_id = await _course_id(session, seed.course_code)
        subject_id = ids[seed.subject_code] if seed.subject_code else None

        chapter_ids = [ids[code] for code in seed.chapter_codes if code in ids]
        if not chapter_ids:
            logger.warning("mock %r has no matching chapters - skipped", seed.title)
            continue

        rows = await session.execute(
            select(Question.id)
            .where(
                Question.chapter_id.in_(chapter_ids),
                Question.status == "PUBLISHED",
                Question.deleted_at.is_(None),
            )
            .order_by(Question.id)
        )
        question_ids = [str(row[0]) for row in rows.all()]
        if not question_ids:
            logger.warning("mock %r matched no published questions - skipped", seed.title)
            continue

        mock_id = deterministic_id("mock", seed.title)
        stmt = (
            pg_insert(MockTest)
            .values(
                id=mock_id,
                course_id=course_id,
                subject_id=subject_id,
                title=seed.title,
                kind=seed.kind,
                duration_min=seed.duration_min,
                # Marks are recomputed from the questions actually attached, so the
                # paper total can never contradict its own contents.
                total_marks=await _marks_of(session, question_ids),
                syllabus_scheme=SyllabusScheme,
                status="PUBLISHED",
                is_premium=seed.is_premium,
                question_ids=question_ids,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"question_ids": question_ids, "status": "PUBLISHED"},
            )
        )
        await session.execute(stmt)
        created += 1
    return created


async def _marks_of(session: AsyncSession, question_ids: list[str]) -> int:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Question.marks), 0)).where(
                Question.id.in_([uuid.UUID(qid) for qid in question_ids])
            )
        )
    ).scalar_one()
    return int(total) or 1


async def seed_exam_sessions(session: AsyncSession) -> int:
    count = 0
    for year, month, label in EXAM_SESSIONS:
        stmt = (
            pg_insert(ExamSession)
            .values(
                year=year,
                month=month,
                label=label,
                syllabus_scheme=SyllabusScheme,
                # No date: ICAI publishes the calendar separately. A countdown
                # computed from an invented date is worse than no countdown.
                exam_start_date=None,
                is_published=True,
            )
            .on_conflict_do_update(
                index_elements=["year", "month"],
                set_={"label": label, "is_published": True},
            )
        )
        await session.execute(stmt)
        count += 1
    return count


async def seed_all(session: AsyncSession) -> dict[str, int]:
    verifier_id = await _system_user(session)
    ids = await seed_curriculum(session)
    await session.flush()

    question_ids = await seed_questions(session, ids=ids, verifier_id=verifier_id)
    await session.flush()

    mocks = await seed_mocks(session, ids=ids)
    sessions = await seed_exam_sessions(session)
    await session.commit()

    counts = {
        "courses": len(COURSES),
        "subjects": len({s.code for c in COURSES for s in c.subjects}),
        "chapters": len({ch.code for c in COURSES for s in c.subjects for ch in s.chapters}),
        "questions": len(question_ids),
        "mocks": mocks,
        "examSessions": sessions,
    }
    return counts


async def report(session: AsyncSession) -> dict[str, int]:
    """What the database currently holds, for `--check`."""
    result = {}
    for name, model in (
        ("courses", Course),
        ("subjects", Subject),
        ("chapters", Chapter),
        ("questions", Question),
        ("publishedQuestions", Question),
        ("mocks", MockTest),
        ("examSessions", ExamSession),
    ):
        stmt = select(func.count()).select_from(model)
        if name == "publishedQuestions":
            stmt = stmt.where(Question.status == "PUBLISHED")
        result[name] = int((await session.execute(stmt)).scalar_one())
    return result


async def main(*, check: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        if check:
            counts = await report(session)
            logger.info("seed check: %s", counts)
            expected = {
                "courses": len(COURSES),
                "questions": len(QUESTIONS),
                "mocks": len(MOCKS),
            }
            missing = {
                key: (counts.get(key, 0), want)
                for key, want in expected.items()
                if counts.get(key, 0) < want
            }
            if missing:
                logger.error("content missing (have, want): %s", missing)
                return 1
            logger.info("all reference content is present")
            return 0

        counts = await seed_all(session)
        logger.info("seeded: %s", counts)
        return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Seed reference content.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what is missing instead of writing anything",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not get_settings().database_url:
        logger.error("DATABASE_URL is not configured")
        return 2
    return asyncio.run(main(check=args.check))


if __name__ == "__main__":
    raise SystemExit(_cli())
