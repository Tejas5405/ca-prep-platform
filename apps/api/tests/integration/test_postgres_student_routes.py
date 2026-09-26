"""The student-facing routes, over HTTP, against a live PostgreSQL.

WHAT THIS FILE IS FOR

The unit suite for these routes runs against a hand-written session double. That
proves the route's logic and nothing about its SQL: a double cannot fail on a
column that does not exist, a join that fans out rows, a CHECK the write violates,
or a partial unique index. Every route here goes through the real repository, real
SQL, a real commit, and the real constraints - seeded with the real seeder, which
is itself only exercised for real in this file.

WHAT IS FAKED: NOTHING. Only the signed-in identity and the settings are injected,
because those are how the request arrives, not what it does.

THE LEAK ASSERTIONS READ THE RAW BODY

``assert "correctAnswer" not in response.text`` rather than walking the parsed
payload. A parsed walk only checks the keys this test knows to look for; the raw
string check also catches a nested object, a debug field, or an ORM dump that
smuggles the answer out under a name nobody thought to list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

from app.api.v1 import search as search_route
from app.core.config import Settings
from app.core.dependencies import get_db
from app.core.identity import get_current_user
from app.main import app
from app.models.curriculum import Chapter, Course, Subject
from app.models.doubt import Doubt, DoubtReply
from app.models.progress import (
    DailyActivity,
    PointsLedger,
    PracticeAttempt,
    SpacedRepetitionCard,
    UserQuestionProgress,
)
from app.models.question import Question, QuestionOption
from app.models.user import Subscription, User
from app.seed import seed_all

from ._db import observe, run_in_database

pytestmark = pytest.mark.postgres

#: Fragments that must never appear in a response the student has not answered yet.
ANSWER_LEAKS = ("correctAnswer", "explanation", "isCorrect", "is_correct", "correct_answer")


# --------------------------------------------------------------------------- setup


def student_settings() -> Settings:
    """Real settings, but never from a developer's ``.env``."""
    return Settings(_env_file=None)


class Student:
    """An HTTP client for one signed-in user, sharing the test's own session.

    ``ASGITransport`` rather than ``TestClient``: TestClient runs the app on its own
    thread and loop, and the session this test asserts through belongs to this
    loop. Two identities are injected - the session and the user - and both are the
    ONLY things the request cannot supply for itself.
    """

    def __init__(self, session: Any, user: User, *, settings: Settings | None = None) -> None:
        self._session = session
        self._user = user
        self._settings = settings or student_settings()

    async def __aenter__(self) -> httpx.AsyncClient:
        app.dependency_overrides[get_db] = lambda: self._session
        app.dependency_overrides[get_current_user] = lambda: self._user
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        return self._client

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()
        # Cleared unconditionally: the override registry is global to the app, so a
        # leaked override would hand another test's session to the next request.
        app.dependency_overrides.clear()


async def make_student(session: Any, name: str = "student", *, role: str = "STUDENT") -> User:
    user = User(
        auth_user_id=f"test-auth-{name}-{uuid.uuid4().hex[:8]}",
        email=f"{name}@example.com",
        display_name=name.title(),
        role=role,
    )
    session.add(user)
    await session.commit()
    return user


class Content:
    """Ids from the seeded syllabus, so tests never hard-code a uuid."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.courses: dict[str, uuid.UUID] = {}
        self.subjects: dict[str, uuid.UUID] = {}
        self.chapters: dict[str, uuid.UUID] = {}

    @classmethod
    async def seeded(cls, session: Any) -> Content:
        await seed_all(session)
        self = cls(session)
        self.courses = {
            row.code: row.id for row in (await session.execute(select(Course))).scalars().all()
        }
        self.subjects = {
            row.code: row.id for row in (await session.execute(select(Subject))).scalars().all()
        }
        self.chapters = {
            row.code: row.id for row in (await session.execute(select(Chapter))).scalars().all()
        }
        return self

    def course(self, code: str) -> str:
        return str(self.courses[code])

    def subject(self, code: str) -> str:
        return str(self.subjects[code])

    def chapter(self, code: str) -> str:
        return str(self.chapters[code])

    async def questions_of(self, subject_code: str) -> list[Question]:
        rows = await self.session.execute(
            select(Question)
            .where(
                Question.subject_id == self.subjects[subject_code],
                Question.status == "PUBLISHED",
            )
            .order_by(Question.text)
        )
        return list(rows.scalars().all())

    async def question(self, subject_code: str) -> Question:
        questions = await self.questions_of(subject_code)
        assert questions, f"no seeded questions for {subject_code}"
        return questions[0]

    async def options(self, question: Question) -> list[QuestionOption]:
        rows = await self.session.execute(
            select(QuestionOption)
            .where(QuestionOption.question_id == question.id)
            .order_by(QuestionOption.label)
        )
        return list(rows.scalars().all())

    async def correct_label(self, question: Question) -> str:
        for option in await self.options(question):
            if option.is_correct:
                return option.label
        raise AssertionError("seeded question has no correct option")


async def count(session: Any, model: Any, *conditions: Any) -> int:
    stmt = select(func.count()).select_from(model).where(*conditions)
    return int((await session.execute(stmt)).scalar_one())


# ----------------------------------------------------------------- curriculum


def test_the_seeded_syllabus_is_served(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            courses = (await client.get("/api/v1/curriculum/courses")).json()["data"]["courses"]
            assert [c["code"] for c in courses] == [
                "CA_FOUNDATION",
                "CA_INTERMEDIATE",
                "CA_FINAL",
            ]
            assert {c["level"] for c in courses} == {"FOUNDATION", "INTERMEDIATE", "FINAL"}
            assert all(c["syllabusScheme"] == "NEW_2024" for c in courses)

            subjects = (
                await client.get(
                    "/api/v1/curriculum/subjects", params={"course_id": content.course("CA_FINAL")}
                )
            ).json()["data"]["subjects"]
            assert len(subjects) == 6
            assert all(s["chapterCount"] >= 1 for s in subjects)
            assert all(s["paperNumber"] >= 1 for s in subjects)

            chapters = (
                await client.get(
                    "/api/v1/curriculum/chapters", params={"subject_id": content.subject("FND_ACC")}
                )
            ).json()["data"]["chapters"]
            assert chapters, "FND_ACC has no chapters"
            assert [c["sequence"] for c in chapters] == sorted(c["sequence"] for c in chapters)
            assert all(1 <= c["weightage"] <= 10 for c in chapters)
            assert all(c["estimatedMinutes"] > 0 for c in chapters)

            topics = (
                await client.get(f"/api/v1/curriculum/chapters/{chapters[0]['id']}/topics")
            ).json()["data"]["topics"]
            assert topics == []  # the seed carries chapters, not topics

    run_in_database(database_url, body)


def test_a_malformed_course_id_is_a_problem_document_not_a_traceback(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.get(
                "/api/v1/curriculum/subjects", params={"course_id": "not-a-uuid"}
            )
            assert response.status_code == 422
            assert response.headers["content-type"].startswith("application/problem+json")
            assert response.json()["detail"] == "course_id must be a UUID."

    run_in_database(database_url, body)


def test_chapters_of_an_unknown_subject_is_a_404(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            missing = await client.get(
                "/api/v1/curriculum/chapters", params={"subject_id": str(uuid.uuid4())}
            )
            assert missing.status_code == 404
            assert missing.json()["title"] == "Subject not found"

    run_in_database(database_url, body)


# ------------------------------------------------------------------- practice


def test_a_practice_set_never_carries_the_answer(database_url: str) -> None:
    """The single most important assertion in this file."""

    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.get(
                "/api/v1/practice/questions",
                params={"subject_id": content.subject("FND_ACC"), "limit": 5},
            )
            assert response.status_code == 200
            for leak in ANSWER_LEAKS:
                assert leak not in response.text, f"{leak} reached the browser"
            questions = response.json()["data"]["questions"]
            assert questions
            assert all(len(q["options"]) >= 2 for q in questions)
            assert all(
                set(option) == {"label", "text"} for q in questions for option in q["options"]
            )

    run_in_database(database_url, body)


def test_answering_records_every_side_effect_in_one_transaction(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")
        label = await content.correct_label(question)

        async with Student(session, user) as client:
            response = await client.post(
                "/api/v1/practice/answers",
                json={
                    "question_id": str(question.id),
                    "chosen_option": label,
                    "time_spent_seconds": 45,
                },
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["isCorrect"] is True
            assert data["correctAnswer"] == label
            assert data["explanation"]
            assert data["pointsAwarded"] > 0
            assert data["attemptsCount"] == 1
            assert data["currentStreak"] == 1

        # Read back on a SECOND connection: a row visible only to the writing
        # session is a row that was never committed.
        async with observe(database_url) as check:
            assert await count(check, PracticeAttempt, PracticeAttempt.user_id == user.id) == 1
            progress = (
                await check.execute(
                    select(UserQuestionProgress).where(
                        UserQuestionProgress.user_id == user.id,
                        UserQuestionProgress.question_id == question.id,
                    )
                )
            ).scalar_one()
            assert progress.attempts_count == 1
            assert progress.correct_count == 1
            assert progress.last_attempted_at is not None
            assert await count(check, DailyActivity, DailyActivity.user_id == user.id) == 1
            assert await count(check, PointsLedger, PointsLedger.user_id == user.id) >= 1
            # A correct answer is NOT queued for revision - only mistakes and
            # bookmarks are, and a queue that fills with things you already know is
            # a queue nobody opens twice.
            assert (
                await count(
                    check,
                    SpacedRepetitionCard,
                    SpacedRepetitionCard.user_id == user.id,
                    SpacedRepetitionCard.question_id == question.id,
                )
                == 0
            )

    run_in_database(database_url, body)


def test_a_wrong_answer_enters_the_revision_queue(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")
        correct = await content.correct_label(question)
        wrong = next(
            option.label for option in await content.options(question) if option.label != correct
        )

        async with Student(session, user) as client:
            response = await client.post(
                "/api/v1/practice/answers",
                json={"question_id": str(question.id), "chosen_option": wrong},
            )
            assert response.json()["data"]["isCorrect"] is False

        async with observe(database_url) as check:
            card = (
                await check.execute(
                    select(SpacedRepetitionCard).where(
                        SpacedRepetitionCard.user_id == user.id,
                        SpacedRepetitionCard.question_id == question.id,
                    )
                )
            ).scalar_one()
            assert card.repetitions == 0
            assert card.interval_days == 0
            assert card.next_review_at <= datetime.now(UTC) + timedelta(minutes=1)

    run_in_database(database_url, body)


def test_accuracy_is_a_fraction_of_graded_attempts(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        questions = await content.questions_of("FND_ACC")
        assert len(questions) >= 2
        first, second = questions[0], questions[1]

        async with Student(session, user) as client:
            right = await client.post(
                "/api/v1/practice/answers",
                json={
                    "question_id": str(first.id),
                    "chosen_option": await content.correct_label(first),
                },
            )
            wrong = await client.post(
                "/api/v1/practice/answers",
                json={"question_id": str(second.id), "chosen_option": None},
            )
            # A skip is recorded and graded as WRONG, but `chosenOption: null` is a
            # legitimate answer, not a validation error.
            assert wrong.status_code == 200
            assert wrong.json()["data"]["isCorrect"] is False
            # The accuracy in an ANSWER response is that question's own record -
            # 1 of 1 for the one answered correctly, 0 of 1 (or 0 of 2 after the
            # retry below) for the other. Overall accuracy is a different number
            # and is asserted through /progress/overview, where it belongs.
            assert right.json()["data"]["accuracy"] == 1.0
            assert wrong.json()["data"]["accuracy"] == 0.0
            assert wrong.json()["data"]["isCorrect"] is False

        # A second attempt on the same question is what makes per-question
        # accuracy interesting, and it must not double-count anywhere else.
        async with Student(session, user) as client:
            retry = await client.post(
                "/api/v1/practice/answers",
                json={
                    "question_id": str(second.id),
                    "chosen_option": await content.correct_label(second),
                },
            )
            assert retry.json()["data"]["accuracy"] == 0.5
            assert retry.json()["data"]["attemptsCount"] == 2
            assert retry.json()["data"]["correctCount"] == 1
            overview = (await client.get("/api/v1/progress/overview")).json()["data"]
            assert overview["totals"]["attempts"] == 3
            assert overview["totals"]["correct"] == 2
            assert overview["totals"]["accuracy"] == round(2 / 3, 3) or (
                abs(overview["totals"]["accuracy"] - 2 / 3) < 1e-9
            )

    run_in_database(database_url, body)


def test_answering_an_unknown_question_is_a_404(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.post(
                "/api/v1/practice/answers",
                json={"question_id": str(uuid.uuid4()), "chosen_option": "A"},
            )
            assert response.status_code == 404
            assert response.json()["title"] == "Question not found"

    run_in_database(database_url, body)


def test_bookmarking_before_answering_is_refused_then_allowed(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")

        async with Student(session, user) as client:
            too_early = await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": True}
            )
            assert too_early.status_code == 409

            await client.post(
                "/api/v1/practice/answers",
                json={
                    "question_id": str(question.id),
                    "chosen_option": await content.correct_label(question),
                },
            )
            marked = await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": True}
            )
            assert marked.status_code == 200
            assert marked.json()["data"]["markedForReview"] is True

        async with observe(database_url) as check:
            progress = (
                await check.execute(
                    select(UserQuestionProgress).where(
                        UserQuestionProgress.user_id == user.id,
                        UserQuestionProgress.question_id == question.id,
                    )
                )
            ).scalar_one()
            assert progress.is_marked_for_review is True

    run_in_database(database_url, body)


def test_a_client_cannot_invent_fields_on_an_answer(database_url: str) -> None:
    """Strict inbound validation: ``isCorrect`` in the body must be a 422, not a nudge.

    The schema forbids extras, so a client that tries to tell the server it was
    right is rejected at the boundary rather than silently ignored - a difference
    that shows up immediately in a client's tests instead of in the accuracy
    numbers months later.
    """

    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")
        async with Student(session, user) as client:
            response = await client.post(
                "/api/v1/practice/answers",
                json={
                    "question_id": str(question.id),
                    "chosen_option": "A",
                    "is_correct": True,
                },
            )
            assert response.status_code == 422
        assert await count(session, PracticeAttempt) == 0

    run_in_database(database_url, body)


# ------------------------------------------------------------------- progress


def test_the_overview_reflects_what_was_actually_answered(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        questions = await content.questions_of("FND_ACC")
        right, wrong = questions[0], questions[1]

        async with Student(session, user) as client:
            await client.post(
                "/api/v1/practice/answers",
                json={
                    "question_id": str(right.id),
                    "chosen_option": await content.correct_label(right),
                },
            )
            await client.post(
                "/api/v1/practice/answers",
                json={"question_id": str(wrong.id), "chosen_option": None},
            )

            payload = (await client.get("/api/v1/progress/overview")).json()["data"]
            assert payload["totals"]["attempts"] == 2
            assert payload["totals"]["correct"] == 1
            assert payload["totals"]["accuracy"] == 0.5

            section = next(
                item
                for item in payload["bySubject"]
                if item["subjectId"] == content.subject("FND_ACC")
            )
            assert section["attempted"] == 2
            assert section["correct"] == 1

            # ONE ATTEMPT PER CHAPTER IS NOT A VERDICT. The threshold is 5
            # attempts before a chapter may be called weak or strong, so these are
            # empty here on purpose - telling a student a chapter is weak after a
            # single question is the kind of confident noise that erodes trust in
            # every other number on the dashboard.
            assert payload["focusAreas"] == []
            assert payload["strongAreas"] == []
            assert payload["profile"]["totalPoints"] > 0
            assert payload["profile"]["currentStreak"] == 1
            assert len(payload["recentActivity"]) == 1
            assert payload["recentActivity"][0]["questionsAttempted"] == 2
            assert payload["revision"]["due"] == 1

    run_in_database(database_url, body)


def test_a_chapter_is_only_judged_once_there_is_enough_evidence(database_url: str) -> None:
    """The focus/strong split, which crashed the first time it saw real data.

    ``focus_and_strong`` built ``ChapterPerformance`` with arguments it does not
    declare and omitted ``accuracy``, the field the verdict is computed from, so
    ``/progress/overview`` raised TypeError for any student with chapter stats. The
    unit suite could not catch it: the route had no test that reached this branch,
    which needs five attempts on one chapter.
    """

    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        questions = await content.questions_of("FND_ACC")
        assert questions, "FND_ACC has no seeded questions"
        weak, strong = questions[0], questions[1]
        correct_weak = await content.correct_label(weak)
        wrong_label = next(
            option.label for option in await content.options(weak) if option.label != correct_weak
        )

        async with Student(session, user) as client:
            # Five attempts on the weak chapter, all wrong.
            for _ in range(5):
                await client.post(
                    "/api/v1/practice/answers",
                    json={"question_id": str(weak.id), "chosen_option": wrong_label},
                )
            # Five on the strong chapter, all right.
            for _ in range(5):
                await client.post(
                    "/api/v1/practice/answers",
                    json={
                        "question_id": str(strong.id),
                        "chosen_option": await content.correct_label(strong),
                    },
                )

            payload = (await client.get("/api/v1/progress/overview")).json()["data"]
            focus_ids = {area["chapterId"] for area in payload["focusAreas"]}
            strong_ids = {area["chapterId"] for area in payload["strongAreas"]}

            assert str(weak.chapter_id) in focus_ids, payload["focusAreas"]
            assert str(strong.chapter_id) in strong_ids, payload["strongAreas"]
            # The two verdicts are mutually exclusive: a chapter cannot be both
            # something to revise and something already mastered.
            assert focus_ids.isdisjoint(strong_ids)

            focus = next(a for a in payload["focusAreas"] if a["chapterId"] == str(weak.chapter_id))
            assert focus["chapterName"]
            assert focus["subjectName"]
            assert focus["attempted"] == 5
            assert focus["accuracy"] == 0.0

    run_in_database(database_url, body)


def test_a_students_overview_is_empty_rather_than_absent_before_any_work(
    database_url: str,
) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.get("/api/v1/progress/overview")
            assert response.status_code == 200
            payload = response.json()["data"]
            # A brand-new account must render, not crash: the dashboard is the first
            # screen after sign-in and `None` there is a blank page.
            assert payload["totals"] == {
                "attempts": 0,
                "correct": 0,
                "pendingReview": 0,
                "accuracy": None,
            }
            assert payload["bySubject"] == []
            assert payload["focusAreas"] == []
            assert payload["strongAreas"] == []
            assert payload["recentActivity"] == []
            assert payload["profile"]["currentStreak"] == 0

    run_in_database(database_url, body)


# ------------------------------------------------------------------- revision


def test_the_due_queue_shows_a_missed_question_without_its_answer(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")

        async with Student(session, user) as client:
            await client.post(
                "/api/v1/practice/answers",
                json={"question_id": str(question.id), "chosen_option": None},
            )
            response = await client.get("/api/v1/revision/due")
            assert response.status_code == 200
            for leak in ANSWER_LEAKS:
                assert leak not in response.text
            cards = response.json()["data"]["cards"]
            assert [card["questionId"] for card in cards] == [str(question.id)]
            assert cards[0]["repetitions"] == 0
            assert cards[0]["options"]

    run_in_database(database_url, body)


def test_grading_a_card_advances_it_out_of_the_queue(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")

        async with Student(session, user) as client:
            await client.post(
                "/api/v1/practice/answers",
                json={"question_id": str(question.id), "chosen_option": None},
            )
            graded = await client.post(
                "/api/v1/revision/review",
                json={"question_id": str(question.id), "quality": 5},
            )
            assert graded.status_code == 200
            result = graded.json()["data"]
            assert result["lapsed"] is False
            assert result["intervalDays"] >= 1
            assert result["repetitions"] == 1
            assert result["nextReviewAt"]

            # Graded out of today's queue, so the queue is now empty. The interval
            # the session computed must be the one the database holds.
            assert (await client.get("/api/v1/revision/due")).json()["data"]["cards"] == []
            stats = (await client.get("/api/v1/revision/stats")).json()["data"]
            assert stats["boxes"] == {str(result["box"]): 1}

        async with observe(database_url) as check:
            card = (
                await check.execute(
                    select(SpacedRepetitionCard).where(
                        SpacedRepetitionCard.user_id == user.id,
                        SpacedRepetitionCard.question_id == question.id,
                    )
                )
            ).scalar_one()
            assert card.repetitions == 1
            assert card.next_review_at > datetime.now(UTC)

    run_in_database(database_url, body)


def test_a_lapse_sends_the_card_back_to_the_start(database_url: str) -> None:
    """Remembering nothing must not be recorded as progress.

    SM-2's lapse rule is the difference between a revision queue and a to-do list:
    a card graded 1 after a grade of 5 goes back to a one-day interval.
    """

    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")

        async with Student(session, user) as client:
            await client.post(
                "/api/v1/practice/answers",
                json={"question_id": str(question.id), "chosen_option": None},
            )
            good = (
                await client.post(
                    "/api/v1/revision/review",
                    json={"question_id": str(question.id), "quality": 5},
                )
            ).json()["data"]
            lapse = (
                await client.post(
                    "/api/v1/revision/review",
                    json={"question_id": str(question.id), "quality": 1},
                )
            ).json()["data"]

            # A lapse RESETS: SM-2 sends a failed card back to repetitions 0. The
            # interval cannot go below the 1-day floor it is already at, so the
            # assertions are about the reset and the eased-down factor, not about
            # the interval shrinking.
            assert lapse["lapsed"] is True
            assert lapse["repetitions"] == 0
            assert lapse["intervalDays"] == 1
            assert good["repetitions"] == 1
            assert lapse["easeFactor"] < good["easeFactor"]

    run_in_database(database_url, body)


def test_grading_a_question_that_was_never_missed_is_a_404(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        question = await content.question("FND_ACC")
        async with Student(session, user) as client:
            response = await client.post(
                "/api/v1/revision/review",
                json={"question_id": str(question.id), "quality": 4},
            )
            assert response.status_code == 404
            assert "not in your revision queue" in response.json()["detail"]

    run_in_database(database_url, body)


def test_a_grade_outside_the_scale_is_rejected(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.post(
                "/api/v1/revision/review",
                json={"question_id": str(uuid.uuid4()), "quality": 6},
            )
            assert response.status_code == 422

    run_in_database(database_url, body)


# --------------------------------------------------------------------- doubts


def test_a_doubt_can_be_asked_read_and_answered(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)

        async with Student(session, user) as client:
            created = await client.post(
                "/api/v1/doubts",
                json={
                    "title": "Why is consideration needed for a valid contract?",
                    "body": "Section 25 lists exceptions but I do not follow the logic.",
                    "subject_id": content.subject("FND_LAW"),
                    "chapter_id": content.chapter("FND_LAW_01"),
                },
            )
            assert created.status_code == 201
            doubt = created.json()["data"]
            assert doubt["status"] == "OPEN"
            assert doubt["replyCount"] == 0

            listed = (await client.get("/api/v1/doubts")).json()
            assert listed["meta"]["total"] == 1
            assert listed["data"][0]["id"] == doubt["id"]

            reply = await client.post(
                f"/api/v1/doubts/{doubt['id']}/replies",
                json={"body": "Consideration is the price of the promise."},
            )
            assert reply.status_code == 201
            assert reply.json()["data"]["isStaffAnswer"] is False
            assert reply.json()["data"]["author"]["displayName"] == user.display_name

            thread = (await client.get(f"/api/v1/doubts/{doubt['id']}")).json()["data"]
            assert thread["replyCount"] == 1
            assert len(thread["replies"]) == 1

        async with observe(database_url) as check:
            replies = await count(check, DoubtReply, DoubtReply.doubt_id == uuid.UUID(doubt["id"]))
            assert replies == 1

    run_in_database(database_url, body)


def test_a_student_cannot_claim_to_be_staff_in_a_reply(database_url: str) -> None:
    """The role is read from the row, and the field is not accepted from the body."""

    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)

        async with Student(session, user) as client:
            doubt_id = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "Is section 25 an exception list?", "body": "Explain please."},
                )
            ).json()["data"]["id"]

            for claim in ({"is_staff_answer": True}, {"isStaffAnswer": True}):
                response = await client.post(
                    f"/api/v1/doubts/{doubt_id}/replies",
                    json={"body": "It is an exception list.", **claim},
                )
                assert response.status_code == 422, claim

            honest = await client.post(
                f"/api/v1/doubts/{doubt_id}/replies", json={"body": "It is an exception list."}
            )
            assert honest.json()["data"]["isStaffAnswer"] is False

    run_in_database(database_url, body)


def test_a_student_cannot_read_another_students_doubt(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        asker = await make_student(session, "asker")
        nosy = await make_student(session, "nosy")

        async with Student(session, asker) as client:
            doubt_id = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "How do I value closing stock?", "body": "Cost or NRV?"},
                )
            ).json()["data"]["id"]

        async with Student(session, nosy) as client:
            response = await client.get(f"/api/v1/doubts/{doubt_id}")
            # 404, not 403: a 403 would confirm the id exists.
            assert response.status_code == 404
            assert (await client.get("/api/v1/doubts")).json()["meta"]["total"] == 0

    run_in_database(database_url, body)


def test_only_staff_can_resolve_and_staff_see_the_queue(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        student = await make_student(session, "student")
        moderator = await make_student(session, "mod", role="MODERATOR")

        async with Student(session, student) as client:
            doubt_id = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "Is negative marking per question?", "body": "Or per paper?"},
                )
            ).json()["data"]["id"]

            refused = await client.patch(
                f"/api/v1/doubts/{doubt_id}",
                json={"status": "RESOLVED", "resolution_note": "self-serve"},
            )
            assert refused.status_code == 403

        async with observe(database_url) as check:
            still_open = (
                await check.execute(select(Doubt).where(Doubt.id == uuid.UUID(doubt_id)))
            ).scalar_one()
            assert still_open.status == "OPEN"
            assert still_open.resolved_at is None

        async with Student(session, moderator) as client:
            # Staff see the whole queue, including doubts they did not ask.
            assert (await client.get("/api/v1/doubts")).json()["meta"]["total"] == 1
            answered = await client.post(
                f"/api/v1/doubts/{doubt_id}/replies",
                json={"body": "Per the marks printed on the paper."},
            )
            assert answered.json()["data"]["isStaffAnswer"] is True

            resolved = await client.patch(
                f"/api/v1/doubts/{doubt_id}",
                json={"status": "RESOLVED", "resolution_note": "Answered the marking rule."},
            )
            assert resolved.status_code == 200
            assert resolved.json()["data"]["status"] == "RESOLVED"

        async with observe(database_url) as check:
            row = (
                await check.execute(select(Doubt).where(Doubt.id == uuid.UUID(doubt_id)))
            ).scalar_one()
            assert row.status == "RESOLVED"
            assert row.resolved_at is not None
            assert row.resolved_by == moderator.id
            assert row.resolution_note == "Answered the marking rule."

    run_in_database(database_url, body)


def test_accepting_a_reply_resolves_the_thread_and_marks_only_that_reply(
    database_url: str,
) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        student = await make_student(session, "student")
        other = await make_student(session, "helper", role="EDITOR")

        async with Student(session, student) as client:
            doubt_id = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "Which chapter carries the most marks?", "body": "Roughly?"},
                )
            ).json()["data"]["id"]

        async with Student(session, other) as client:
            first = (
                await client.post(
                    f"/api/v1/doubts/{doubt_id}/replies", json={"body": "Look at the weightage."}
                )
            ).json()["data"]["id"]
            second = (
                await client.post(
                    f"/api/v1/doubts/{doubt_id}/replies", json={"body": "Roughly a fifth."}
                )
            ).json()["data"]["id"]

        async with Student(session, student) as client:
            accepted = await client.post(f"/api/v1/doubts/{doubt_id}/replies/{second}/accept")
            assert accepted.status_code == 200
            thread = accepted.json()["data"]
            assert thread["status"] == "RESOLVED"
            assert thread["acceptedReplyId"] == second
            flags = {reply["id"]: reply["isAccepted"] for reply in thread["replies"]}
            assert flags == {first: False, second: True}

        async with observe(database_url) as check:
            reply = (
                await check.execute(select(DoubtReply).where(DoubtReply.id == uuid.UUID(second)))
            ).scalar_one()
            assert reply.is_accepted is True

    run_in_database(database_url, body)


def test_a_reply_from_another_thread_cannot_be_accepted(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        student = await make_student(session, "student")

        async with Student(session, student) as client:
            first = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "Doubt about depreciation", "body": "Straight line?"},
                )
            ).json()["data"]["id"]
            second = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "Doubt about inventory", "body": "FIFO versus weighted?"},
                )
            ).json()["data"]["id"]
            foreign = (
                await client.post(
                    f"/api/v1/doubts/{second}/replies", json={"body": "Use the earliest cost."}
                )
            ).json()["data"]["id"]

            response = await client.post(f"/api/v1/doubts/{first}/replies/{foreign}/accept")
            assert response.status_code == 404

    run_in_database(database_url, body)


def test_a_closed_thread_takes_no_new_replies(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        student = await make_student(session, "student")

        async with Student(session, student) as client:
            doubt_id = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "Where do I get past papers?", "body": "Any source?"},
                )
            ).json()["data"]["id"]
            closed = await client.patch(f"/api/v1/doubts/{doubt_id}", json={"status": "CLOSED"})
            assert closed.status_code == 200

            response = await client.post(
                f"/api/v1/doubts/{doubt_id}/replies", json={"body": "Anyone?"}
            )
            assert response.status_code == 409

    run_in_database(database_url, body)


def test_an_unknown_doubt_status_is_refused(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        moderator = await make_student(session, "mod", role="MODERATOR")

        async with Student(session, moderator) as client:
            doubt_id = (
                await client.post(
                    "/api/v1/doubts",
                    json={"title": "A doubt that needs a status", "body": "Testing statuses."},
                )
            ).json()["data"]["id"]
            response = await client.patch(f"/api/v1/doubts/{doubt_id}", json={"status": "ARCHIVED"})
            assert response.status_code == 422
            assert "status must be one of" in response.json()["detail"]

    run_in_database(database_url, body)


def test_a_one_word_title_is_refused_by_the_schema_not_the_database(
    database_url: str,
) -> None:
    """The CHECK exists in the database; the API must refuse it before the INSERT.

    Both layers are required. The schema turns it into a useful 422; the constraint
    is what holds when something other than the API writes to the table.
    """

    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.post("/api/v1/doubts", json={"title": "help", "body": "how?"})
            assert response.status_code == 422
        assert await count(session, Doubt) == 0

    run_in_database(database_url, body)


# ------------------------------------------------------------------------- me


def test_me_reports_a_free_account_and_its_entitlements(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            payload = (await client.get("/api/v1/me")).json()["data"]
            assert payload["email"] == user.email
            assert payload["role"] == "STUDENT"
            assert payload["isStaff"] is False
            assert payload["entitlements"]["tier"] == "FREE"
            assert payload["entitlements"]["isPremium"] is False
            assert set(payload["entitlements"]["entitlements"]) == {
                "past_papers",
                "planner_single_group",
                "spaced_repetition",
            }
            assert payload["profile"]["currentLevel"] == 1

    run_in_database(database_url, body)


def test_me_reflects_a_paid_subscription_and_stops_the_moment_it_expires(
    database_url: str,
) -> None:
    """The expiry is read at request time, so no cron decides whether a student is premium."""

    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        subscription = Subscription(
            user_id=user.id,
            tier="PREMIUM",
            status="ACTIVE",
            started_at=datetime.now(UTC) - timedelta(days=1),
            expires_at=datetime.now(UTC) + timedelta(days=364),
        )
        session.add(subscription)
        await session.commit()

        async with Student(session, user) as client:
            paid = (await client.get("/api/v1/me")).json()["data"]["entitlements"]
            assert paid["tier"] == "PREMIUM"
            assert paid["isPremium"] is True
            assert "unlimited_mocks" in paid["entitlements"]
            assert paid["expiresAt"].startswith(subscription.expires_at.date().isoformat())

            # Move the clock past the expiry by moving the expiry, and change nothing
            # else: no job runs, no row is rewritten, and the access is gone.
            subscription.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

            lapsed = (await client.get("/api/v1/me")).json()["data"]["entitlements"]
            assert lapsed["tier"] == "FREE"
            assert lapsed["isPremium"] is False
            assert "unlimited_mocks" not in lapsed["entitlements"]

    run_in_database(database_url, body)


def test_the_profile_can_be_updated_and_is_persisted(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        exam_date = (datetime.now(UTC) + timedelta(days=200)).date()

        async with Student(session, user) as client:
            response = await client.patch(
                "/api/v1/me",
                json={
                    "target_level": "final",
                    "target_exam_date": exam_date.isoformat(),
                    "daily_goal_minutes": 180,
                    "timezone": "Asia/Kolkata",
                    "city": "Pune",
                    "college": "Example College",
                    "attempt_number": 2,
                },
            )
            assert response.status_code == 200
            payload = response.json()["data"]
            assert payload["targetLevel"] == "FINAL"  # normalised, not stored raw
            assert payload["targetExamDate"] == exam_date.isoformat()
            assert payload["dailyGoalMinutes"] == 180
            assert payload["profile"]["city"] == "Pune"
            assert payload["profile"]["attemptNumber"] == 2

        async with observe(database_url) as check:
            stored = (await check.execute(select(User).where(User.id == user.id))).scalar_one()
            assert stored.target_level == "FINAL"
            assert stored.target_exam_date == exam_date
            assert stored.daily_goal_minutes == 180

    run_in_database(database_url, body)


def test_a_student_cannot_promote_themselves(database_url: str) -> None:
    """The role is not in the update schema, and the schema forbids extras."""

    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.patch("/api/v1/me", json={"role": "ADMIN"})
            assert response.status_code == 422
            assert (await client.get("/api/v1/me")).json()["data"]["role"] == "STUDENT"

        async with observe(database_url) as check:
            stored = (await check.execute(select(User).where(User.id == user.id))).scalar_one()
            assert stored.role == "STUDENT"

    run_in_database(database_url, body)


def test_an_impossible_daily_goal_is_refused(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            too_low = await client.patch("/api/v1/me", json={"daily_goal_minutes": 5})
            too_high = await client.patch("/api/v1/me", json={"daily_goal_minutes": 5000})
            assert too_low.status_code == 422
            assert too_high.status_code == 422

    run_in_database(database_url, body)


def test_an_unknown_target_level_is_refused(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.patch("/api/v1/me", json={"target_level": "DOCTORATE"})
            assert response.status_code == 422
            assert "targetLevel must be one of" in response.json()["detail"]

    run_in_database(database_url, body)


# --------------------------------------------------------------------- search


def test_search_finds_seeded_content_with_a_snippet(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.get("/api/v1/search", params={"q": "depreciation"})
            assert response.status_code == 200
            payload = response.json()["data"]
            assert payload["query"] == "depreciation"
            assert payload["count"] >= 1
            first = payload["results"][0]
            assert first["rank"] > 0
            assert first["subjectName"]
            assert "<b>" in first["snippet"], "ts_headline must mark the matched terms"

    run_in_database(database_url, body)


def test_the_search_expression_matches_the_gin_index(database_url: str) -> None:
    """The route's predicate must be byte-for-byte the index's expression.

    WHY THIS IS NOT AN EXPLAIN ASSERTION AT THE ROUTE LEVEL

    It was one, and it failed - not because the index is unusable, but because the
    planner is right to ignore it here. The seeded bank holds a few dozen rows, so
    with sequential scans disabled PostgreSQL still prefers ``idx_questions_status``
    and applies the tsvector predicate as a filter:

        Index Scan using idx_questions_status on questions
          Filter: (... to_tsvector('english', COALESCE(search_text, text)) @@ ...)

    Asserting the plan would therefore pass or fail by luck of the row count. What
    actually matters - and what a future edit can break - is that the expression the
    route builds is the expression the index covers. A mismatch returns correct rows
    and silently stops using the index, which is the regression this guards.

    The plan SHAPE is asserted in tests/test_integration_db.py, on a bare query
    where the GIN index is the only candidate, so the two tests prove the two
    halves: the index is used when the planner can choose it, and the route is
    written so that it can.
    """

    async def body(session: Any) -> None:
        await Content.seeded(session)

        indexdef = (
            await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = 'idx_questions_fts'"
                )
            )
        ).scalar_one()
        assert "coalesce(search_text, text)" in indexdef.lower(), indexdef
        assert "to_tsvector('english'" in indexdef.lower(), indexdef

        statement = search_route.build_search_statement("depreciation", limit=20)
        sql = str(
            statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        # Both halves of the expression, in the route's own SQL. Compared in lower
        # case because SQLAlchemy renders func.coalesce as `coalesce`.
        rendered = sql.lower()
        assert "to_tsvector('english'" in rendered, sql
        assert "coalesce(questions.search_text, questions.text)" in rendered, sql
        # And no bare `to_tsvector(..., questions.text)`: that is the specific
        # wrong form this test exists to reject, and it is the one a developer
        # writes by hand when "simplifying" the expression.
        assert "to_tsvector('english', questions.text)" not in rendered, sql

        # The index is reachable: with sequential scans off the query still runs
        # and returns the expected row, so the predicate is not merely present but
        # executable against this schema.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in (await session.execute(text(f"EXPLAIN {sql}"))).all())
        assert "Seq Scan on questions" not in plan, plan

    run_in_database(database_url, body)


def test_search_never_returns_an_unpublished_question(database_url: str) -> None:
    async def body(session: Any) -> None:
        content = await Content.seeded(session)
        user = await make_student(session)
        source = await content.question("FND_ACC")
        draft = Question(
            course_id=source.course_id,
            subject_id=source.subject_id,
            chapter_id=source.chapter_id,
            text="Zygomorphic ledgerposting is prohibited for a going concern.",
            question_type="MCQ",
            difficulty="MEDIUM",
            marks=2,
            # ck_questions_objective_requires_answer fires regardless of status: a
            # draN MCQ still needs its answer key, because the key is what the
            # reviewer is checking.
            correct_answer="A",
            status="DRAFT",
            source="integration test",
        )
        session.add(draft)
        await session.commit()

        async with Student(session, user) as client:
            published = (await client.get("/api/v1/search", params={"q": "zygomorphic"})).json()
            assert published["data"]["count"] == 0

            # The same words are findable the moment the question is published, so
            # the previous assertion is about status and not about the query text.
            draft.status = "PUBLISHED"
            draft.verified_by = source.verified_by
            await session.commit()
            found = (await client.get("/api/v1/search", params={"q": "zygomorphic"})).json()
            assert found["data"]["count"] == 1

    run_in_database(database_url, body)


def test_a_one_character_search_is_refused(database_url: str) -> None:
    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            response = await client.get("/api/v1/search", params={"q": "d"})
            assert response.status_code == 422
            assert response.json()["title"] == "Query too short"

    run_in_database(database_url, body)


def test_a_quoted_search_phrase_does_not_break_the_query(database_url: str) -> None:
    """``websearch_to_tsquery`` rather than ``to_tsquery``: a stray quote in the
    box is a person typing, not a syntax error."""

    async def body(session: Any) -> None:
        await Content.seeded(session)
        user = await make_student(session)
        async with Student(session, user) as client:
            for query in ('"cost of goods sold"', "depreciation OR inventory", "stock -closing"):
                response = await client.get("/api/v1/search", params={"q": query})
                assert response.status_code == 200, query
                assert response.json()["data"]["query"] == query

    run_in_database(database_url, body)
