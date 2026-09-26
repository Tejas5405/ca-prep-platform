"""The publish step, against a live PostgreSQL, from DRAFT to a student-visible row.

WHY THIS FILE EXISTS

`POST /admin/mocks/{id}/publish` answered `200 {"status": "PUBLISHED"}` and wrote
nothing, and `POST /admin/questions/{id}/publish` — named in blueprint v3 §7.3 — did
not exist. Both are the SAME failure as the empty list pages: an endpoint that
describes a state change it did not perform is indistinguishable from a working one
until someone asks why no content is live. So the assertions here are not about
status codes; they are about the ROW, read back on a second connection after the
request finished.

  * the question really is PUBLISHED and really carries a verifier;
  * the verifier is the CALLER's row, not anything the request could name;
  * a question with no correct option cannot be published — shipping it would mark
    every student right and produce a report that calls them wrong;
  * a paper whose questions are not all published cannot go live, because it cannot
    be scored;
  * a second publish is a conflict rather than an overwrite, so the name attached
    to a question has exactly one answer;
  * a STUDENT cannot publish anything, and the check is the real role dependency.

The student-visible consequence is asserted too, because "published" that students
cannot see would be the same bug wearing a different hat: a published question
appears in `GET /api/v1/practice/questions` for a chapter, and a published paper
appears in `GET /api/v1/mocks`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.main import app
from app.models.curriculum import Chapter, Course, Subject
from app.models.progress import MockTest
from app.models.question import Question, QuestionOption
from app.models.user import User
from app.seed import seed_all

from ._db import observe, run_in_database

pytestmark = pytest.mark.postgres

PUBLISH_QUESTION = "/api/v1/admin/questions/{question_id}/publish"
PUBLISH_MOCK = "/api/v1/admin/mocks/{mock_id}/publish"


class Actor:
    """A signed-in caller, sharing this test's session.

    The principal is overridden rather than the user dependency, so `require_role`
    runs for real: a STUDENT test is refused by the same dependency that refuses a
    student in production, not by a stub that the test wrote to agree with it.
    """

    def __init__(self, session: Any, user: User) -> None:
        self._session = session
        self._user = user

    async def __aenter__(self) -> httpx.AsyncClient:
        app.dependency_overrides[get_db] = lambda: self._session
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            auth_user_id=self._user.auth_user_id,
            email=self._user.email,
            role=self._user.role,
            claims={"sub": self._user.auth_user_id},
        )

        # `get_current_user` resolves the row from the database through the app's own
        # session factory, which is not this database; the row is injected instead so
        # the verifier id is a real users.id with a real FK behind it.
        #
        # IT IS RE-READ ON EVERY REQUEST, ON PURPOSE.
        #
        # Production resolves this row once per request through that request's own
        # session. This harness shares one long-lived instance, and the difference is
        # observable: a route that rolls a transaction back (a 404, a 409) expires
        # every instance in the session, so the NEXT request touching `user.id`
        # triggers a lazy load. In an async context that is not a slow query, it is
        # `MissingGreenlet` and a 500. That is a harness artefact rather than a
        # product bug - but it looks exactly like a product bug, in whichever
        # endpoint was written most recently, which is the worst place to spend an
        # afternoon. Refreshing per request restores the production behaviour
        # without changing what is under test.
        async def _caller() -> User:
            await self._session.refresh(self._user)
            return self._user

        app.dependency_overrides[_current_user_dep()] = _caller
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        return self._client

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()
        app.dependency_overrides.clear()


def _current_user_dep():
    from app.core.identity import get_current_user

    return get_current_user


async def make_user(session: Any, role: str) -> User:
    user = User(
        auth_user_id=f"test-auth-{role.lower()}-{uuid.uuid4().hex[:8]}",
        email=f"{role.lower()}@example.com",
        display_name=role.title(),
        role=role,
    )
    session.add(user)
    await session.commit()
    return user


class Content:
    """A course/subject/chapter spine from the real seeder."""

    @classmethod
    async def seeded(cls, session: Any) -> Content:
        await seed_all(session)
        self = cls()
        self.course = (
            (await session.execute(select(Course).order_by(Course.code))).scalars().first()
        )
        self.subject = (
            (
                await session.execute(
                    select(Subject)
                    .where(Subject.course_id == self.course.id)
                    .order_by(Subject.code)
                )
            )
            .scalars()
            .first()
        )
        self.chapter = (
            (
                await session.execute(
                    select(Chapter)
                    .where(Chapter.subject_id == self.subject.id)
                    .order_by(Chapter.code)
                )
            )
            .scalars()
            .first()
        )
        return self


async def make_question(
    session: Any,
    content: Content,
    *,
    status: str = "DRAFT",
    question_type: str = "MCQ",
    options: int = 4,
    correct: int = 1,
    verifier: User | None = None,
) -> Question:
    """A question plus options, satisfying the NOT NULL spine and the CHECKs."""
    # `correct_answer` is NOT NULL for objective types by CHECK
    # (ck_questions_objective_requires_answer): an MCQ with no recorded key is not
    # representable, which is the database doing half of this module's job.
    objective = question_type in {"MCQ", "TRUE_FALSE", "NUMERICAL"}
    question = Question(
        course_id=content.course.id,
        subject_id=content.subject.id,
        chapter_id=content.chapter.id,
        text=f"Test question {uuid.uuid4().hex[:8]}?",
        question_type=question_type,
        difficulty="EASY",
        marks=2,
        status=status,
        source="integration test",
        correct_answer="A" if objective else None,
        # `ck_questions_published_requires_verifier`: a PUBLISHED row must name
        # whoever signed it off. A fixture that set the status without a verifier
        # would fail at INSERT - which is the constraint doing exactly what it says.
        verified_by=verifier.id if (status == "PUBLISHED" and verifier) else None,
        verified_at=datetime.now(UTC) if (status == "PUBLISHED" and verifier) else None,
    )
    session.add(question)
    await session.flush()
    for index in range(options):
        session.add(
            QuestionOption(
                question_id=question.id,
                label=chr(ord("A") + index),
                text=f"Option {index + 1}",
                is_correct=index < correct,
                sequence=index,
            )
        )
    await session.commit()
    return question


# ------------------------------------------------------------------- questions


def test_publishing_a_question_marks_it_and_records_the_verifier(database_url: str) -> None:
    """Read back on a second connection: the commit either happened or it did not."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        editor = await make_user(session, "CONTENT_MANAGER")
        question = await make_question(session, content)

        async with Actor(session, editor) as client:
            response = await client.post(PUBLISH_QUESTION.format(question_id=question.id))

        async with observe(database_url) as other:
            row = await other.get(Question, question.id)

        return {
            "status": response.status_code,
            "body": response.json(),
            "row_status": row.status,
            "verifier": str(row.verified_by) if row.verified_by else None,
            "expected_verifier": str(editor.id),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200, result["body"]
    assert result["body"]["data"]["status"] == "PUBLISHED"
    assert result["body"]["data"]["previousStatus"] == "DRAFT"
    assert result["row_status"] == "PUBLISHED"
    # THE VERIFIER IS A REAL ROW, and it is the caller's. An FK to a user nobody
    # created would fail here rather than silently recording an audit trail that
    # points at nothing.
    assert result["verifier"] == result["expected_verifier"]


def test_a_published_question_reaches_students(database_url: str) -> None:
    """The point of publishing. `PUBLISHED` in a column nobody reads is not shipping."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        question = await make_question(session, content, status="APPROVED")

        async with Actor(session, manager) as client:
            published = await client.post(PUBLISH_QUESTION.format(question_id=question.id))
            # The student-facing list, through the real route and the real query.
            listed = await client.get(
                "/api/v1/practice/questions", params={"chapter_id": str(content.chapter.id)}
            )
        return {
            "publish": published.status_code,
            "listed": listed.status_code,
            # The practice envelope nests the set under `data.questions`.
            "ids": [row["id"] for row in listed.json()["data"]["questions"]],
            "question_id": str(question.id),
        }

    result = run_in_database(database_url, body)
    assert result["publish"] == 200
    assert result["listed"] == 200
    assert result["question_id"] in result["ids"]


def test_an_unkeyed_question_cannot_be_published(database_url: str) -> None:
    """The failure that would reach students as a scoring bug.

    An MCQ with no correct option is publishable as far as the status column is
    concerned; the reason it must not be is that `_correct_index` finds nothing, the
    submission is recorded as PENDING_REVIEW, and every student who answers it is
    told they were wrong.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        question = await make_question(session, content, options=4, correct=0)
        # The constraint forbids a row like this through the ORM... except that it
        # keys on `correct_answer`, not on the option flags. Both are checked by the
        # publish path: the column for objective types, the flags for scorability.
        question.correct_answer = "A"
        await session.commit()

        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_QUESTION.format(question_id=question.id))

        async with observe(database_url) as other:
            row = await other.get(Question, question.id)
        return {
            "status": response.status_code,
            "detail": response.json()["detail"],
            "row_status": row.status,
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 409
    # The detail names the FIX, not just the refusal.
    assert "marked correct" in result["detail"]
    assert result["row_status"] == "DRAFT"


def test_a_single_answer_question_cannot_have_two_correct_options(database_url: str) -> None:
    """MCQ means one answer; MSQ is the multi-answer type. Publishing the difference
    by accident turns a 4-mark question into a trick question."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        question = await make_question(session, content, question_type="MCQ", correct=2)

        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_QUESTION.format(question_id=question.id))
        return {"status": response.status_code, "detail": response.json()["detail"]}

    result = run_in_database(database_url, body)
    assert result["status"] == 409
    assert "exactly one" in result["detail"]


def test_a_descriptive_question_may_publish_without_options(database_url: str) -> None:
    """Not every question has a key, and pretending otherwise blocks the papers that
    are marked by a human - which the report already models as PENDING_REVIEW."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        question = await make_question(
            session, content, question_type="DESCRIPTIVE", options=0, correct=0
        )

        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_QUESTION.format(question_id=question.id))
        return {"status": response.status_code, "body": response.json()}

    result = run_in_database(database_url, body)
    assert result["status"] == 200, result["body"]
    assert result["body"]["data"]["status"] == "PUBLISHED"


def test_publishing_twice_is_a_conflict_not_an_overwrite(database_url: str) -> None:
    """The name attached to a question must have exactly one answer.

    A silent second success would leave the first verifier's name replaced by
    whoever clicked later, which is the opposite of what a sign-off means.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        first = await make_user(session, "CONTENT_MANAGER")
        second = await make_user(session, "ADMIN")
        question = await make_question(session, content)

        async with Actor(session, first) as client:
            assert (
                await client.post(PUBLISH_QUESTION.format(question_id=question.id))
            ).status_code == 200
        async with Actor(session, second) as client:
            again = await client.post(PUBLISH_QUESTION.format(question_id=question.id))

        async with observe(database_url) as other:
            row = await other.get(Question, question.id)
        return {
            "status": again.status_code,
            "verifier": str(row.verified_by),
            "first": str(first.id),
            "second": str(second.id),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 409
    assert result["verifier"] == result["first"], "the original sign-off was overwritten"
    assert result["verifier"] != result["second"]


def test_a_student_cannot_publish(database_url: str) -> None:
    """The role gate is the REAL dependency, so this asserts the production refusal."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content)

        async with Actor(session, student) as client:
            response = await client.post(PUBLISH_QUESTION.format(question_id=question.id))

        async with observe(database_url) as other:
            row = await other.get(Question, question.id)
        return {"status": response.status_code, "row_status": row.status}

    result = run_in_database(database_url, body)
    assert result["status"] == 403
    assert result["row_status"] == "DRAFT"


def test_an_unknown_question_is_a_404(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        manager = await make_user(session, "CONTENT_MANAGER")
        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_QUESTION.format(question_id=uuid.uuid4()))
        return {"status": response.status_code, "body": response.json()}

    result = run_in_database(database_url, body)
    assert result["status"] == 404
    assert "not found" in result["body"]["title"].lower()


# ----------------------------------------------------------------------- mocks


async def make_mock(
    session: Any, content: Content, question_ids: list[Any], **kwargs: Any
) -> MockTest:
    mock = MockTest(
        course_id=content.course.id,
        title=f"Test paper {uuid.uuid4().hex[:6]}",
        kind="CHAPTER",
        duration_min=60,
        total_marks=100,
        question_ids=[str(value) for value in question_ids],
        status=kwargs.pop("status", "DRAFT"),
        **kwargs,
    )
    session.add(mock)
    await session.commit()
    return mock


def test_publishing_a_paper_requires_every_question_to_be_live(database_url: str) -> None:
    """A paper is only as good as its questions.

    Publishing one that references a DRAFT question gives a student a paper that
    cannot be scored: the question is absent from the paper's own breakdown, and the
    denominator counts something nobody can answer.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        live = await make_question(session, content, status="PUBLISHED", verifier=manager)
        draft = await make_question(session, content, status="DRAFT")
        mock = await make_mock(session, content, [live.id, draft.id])

        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_MOCK.format(mock_id=mock.id))

        async with observe(database_url) as other:
            row = await other.get(MockTest, mock.id)
        return {
            "status": response.status_code,
            "detail": response.json()["detail"],
            "mock_status": row.status,
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 409
    assert "not published" in result["detail"]
    assert result["mock_status"] == "DRAFT"


def test_publishing_a_paper_makes_it_visible_to_students(database_url: str) -> None:
    """The stub answered 200 for ids that did not exist. This asserts the row moved
    AND that the listing students read now includes it."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        question = await make_question(session, content, status="PUBLISHED", verifier=manager)
        mock = await make_mock(session, content, [question.id])

        async with Actor(session, manager) as client:
            published = await client.post(PUBLISH_MOCK.format(mock_id=mock.id))
            listed = await client.get("/api/v1/mocks")

        async with observe(database_url) as other:
            row = await other.get(MockTest, mock.id)
        return {
            "publish": published.status_code,
            "body": published.json()["data"],
            "mock_status": row.status,
            "listed_ids": [item["id"] for item in listed.json()["data"]],
            "mock_id": str(mock.id),
        }

    result = run_in_database(database_url, body)
    assert result["publish"] == 200, result["body"]
    assert result["mock_status"] == "PUBLISHED"
    assert result["body"]["questionCount"] == 1
    assert result["mock_id"] in result["listed_ids"]


def test_an_empty_paper_cannot_be_published(database_url: str) -> None:
    """Zero questions publishes a paper that scores 0 out of 0."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        mock = await make_mock(session, content, [])

        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_MOCK.format(mock_id=mock.id))
        return {"status": response.status_code, "detail": response.json()["detail"]}

    result = run_in_database(database_url, body)
    assert result["status"] == 409
    assert "no questions" in result["detail"]


def test_an_unknown_paper_is_a_404_rather_than_a_success(database_url: str) -> None:
    """The stub's most visible lie: a 200 for an id that does not exist."""

    async def body(session) -> dict[str, Any]:
        manager = await make_user(session, "CONTENT_MANAGER")
        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_MOCK.format(mock_id=uuid.uuid4()))
        return {"status": response.status_code}

    assert run_in_database(database_url, body)["status"] == 404


def test_a_malformed_paper_id_is_a_problem_document(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        manager = await make_user(session, "CONTENT_MANAGER")
        async with Actor(session, manager) as client:
            response = await client.post(PUBLISH_MOCK.format(mock_id="not-a-uuid"))
        return {"status": response.status_code, "body": response.json()}

    result = run_in_database(database_url, body)
    assert result["status"] == 400
    assert result["body"]["type"].endswith("/validation")
