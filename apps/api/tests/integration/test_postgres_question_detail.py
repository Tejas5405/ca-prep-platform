"""Opening one question, against a live PostgreSQL.

WHY THIS FILE EXISTS

`GET /search` returned an id, a snippet and the metadata, and nothing else — no
options — so a search result was a dead end: the app could find a question and then
had nothing to open it with. Blueprint v3 §7.3 names `GET /questions/:id`. The route
was missing not because it was hard but because no screen needed it yet, and the
audit that reads a route list cannot see that hole. So this file asserts the two
things that make the route correct rather than merely present:

  * THE ANSWER IS NOT IN THE PAYLOAD BEFORE THE STUDENT ANSWERS. Asserted against
    the raw response TEXT, not the parsed fields — a filter that forgets one key is
    exactly the bug, and the key that leaks is rarely the one under test.
  * IT IS THERE AFTERWARDS, read from the student's own attempt row.

and the boundary cases that decide whether the route is safe to expose: a DRAFT and
a soft-deleted row are 404s, identical to an id that never existed, so the endpoint
cannot be used to enumerate the question bank.

The fixtures (`Actor`, `Content`, `make_question`) are imported from the publishing
suite rather than copied: a second copy of "make a legal question row" would drift,
and the first copy is already constrained by two CHECKs that caught a bad fixture
once. A shared factory module is the tidier home for them and is the next refactor
if a third suite needs them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.models.progress import PracticeAttempt, UserQuestionProgress
from app.models.question import Question

from ._db import observe, run_in_database
from .test_postgres_publishing import Actor, Content, make_question, make_user

DETAIL = "/api/v1/questions/{question_id}"


async def make_attempt(
    session: Any,
    user: Any,
    question: Question,
    *,
    chosen: str | None = "A",
    is_correct: bool | None = True,
    bookmarked: bool = False,
) -> None:
    """The student's own history: one attempt, and optionally a bookmark.

    The bookmark lives on `user_question_progress` rather than a table of its own,
    so the flag the route reads is the same row the practice loop already writes.
    """
    session.add(
        PracticeAttempt(
            user_id=user.id,
            question_id=question.id,
            chosen_option=chosen,
            is_correct=is_correct,
            time_spent_seconds=42,
            used_hint=False,
        )
    )
    session.add(
        UserQuestionProgress(
            user_id=user.id,
            question_id=question.id,
            attempts_count=1,
            correct_count=1 if is_correct else 0,
            is_marked_for_review=bookmarked,
        )
    )
    await session.commit()


# ------------------------------------------------------------------ the payload


def test_a_student_can_open_a_published_question(database_url: str) -> None:
    """The dead end this route closes: search finds a question, and it opens."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)
        await make_attempt(session, student, question)

        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id=question.id))

        payload = response.json()["data"]
        return {
            "status": response.status_code,
            "id": payload["id"],
            "text": payload["text"],
            "labels": [option["label"] for option in payload["options"]],
            "has_text": all(option["text"] for option in payload["options"]),
            "subject_id": payload["subjectId"],
            "chapter_id": payload["chapterId"],
            "expected_id": str(question.id),
            "expected_subject": str(question.subject_id),
            "expected_chapter": str(question.chapter_id),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["id"] == result["expected_id"]
    # The placement is what makes the question addressable in the syllabus tree.
    assert result["subject_id"] == result["expected_subject"]
    assert result["chapter_id"] == result["expected_chapter"]
    # Four options, in the order a student reads them - sequence, then label.
    assert result["labels"] == ["A", "B", "C", "D"]
    assert result["has_text"]


def test_the_answer_is_not_in_the_payload_before_the_student_answers(database_url: str) -> None:
    """THE ASSERTION THAT MATTERS, made against the raw text.

    `_question_payload` never reads `correct_answer`, `explanation` or
    `option.is_correct`, so these fields cannot be present. Asserted on
    `response.text` rather than on the parsed body because a leak is a stray key
    somewhere in the JSON, and a field-by-field check only finds the keys someone
    thought to check.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id=question.id))

        payload = response.json()["data"]
        return {
            "status": response.status_code,
            "text": response.text,
            "attempted": payload.get("attempted"),
            "has_reveal": "reveal" in payload,
            "options": payload["options"],
            "correct_answer": question.correct_answer,
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["attempted"] is False
    assert result["has_reveal"] is False
    for forbidden in ("correctAnswer", "explanation", "modelAnswer", "isCorrect"):
        assert forbidden not in result["text"], f"{forbidden} leaked to an unanswered question"
    # And the key it WOULD leak really does exist on the row, so the assertion above
    # is testing a held-back answer rather than a fixture that never had one.
    assert result["correct_answer"] == "A"
    assert set(result["options"][0]) == {"label", "text"}


def test_the_worked_answer_appears_once_the_student_has_answered(database_url: str) -> None:
    """The other half of the rule: an answered question shows its solution inline.

    Same gate as `POST /practice/answers` — an attempt exists, so the explanation is
    owed to the student. Anything else (a time limit, a "show me" flag) is a second
    rule to get wrong.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)
        question.explanation = "Because section 10(13A) applies."
        await session.commit()
        await make_attempt(session, student, question, chosen="A", is_correct=True)

        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id=question.id))

        payload = response.json()["data"]
        return {
            "status": response.status_code,
            "attempted": payload.get("attempted"),
            "your_answer": payload.get("yourAnswer"),
            "your_result": payload.get("yourResult"),
            "reveal": payload.get("reveal"),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["attempted"] is True
    assert result["your_answer"] == "A"
    assert result["your_result"] is True
    assert result["reveal"]["correctAnswer"] == "A"
    assert "10(13A)" in result["reveal"]["explanation"]


def test_a_skipped_attempt_still_unlocks_the_answer(database_url: str) -> None:
    """A skipped question is an attempt.

    `chosen_option` is nullable because skipping is a real outcome, and the row
    exists — so the explanation is unlocked. Treating a skip as "never answered"
    would mark the question wrong on the progress side AND hide the solution, which
    is the worst of both.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)
        await make_attempt(session, student, question, chosen=None, is_correct=False)

        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id=question.id))

        payload = response.json()["data"]
        return {
            "status": response.status_code,
            "attempted": payload.get("attempted"),
            "your_answer": payload.get("yourAnswer"),
            "has_reveal": "reveal" in payload,
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["attempted"] is True
    assert result["your_answer"] is None
    assert result["has_reveal"] is True


def test_the_bookmark_flag_is_the_callers_own(database_url: str) -> None:
    """One student's bookmark must not appear on another student's screen.

    The flag is read from `user_question_progress` filtered by the caller's id. A
    query that filtered by question id alone would look correct on every screen in
    development, where there is exactly one account.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        other = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)
        await make_attempt(session, other, question, bookmarked=True)

        async with Actor(session, student) as client:
            mine = await client.get(DETAIL.format(question_id=question.id))

        await make_attempt(session, student, question, bookmarked=False)
        async with Actor(session, student) as client:
            after = await client.get(DETAIL.format(question_id=question.id))

        return {
            "status": mine.status_code,
            "other_students_bookmark": mine.json()["data"]["bookmarked"],
            "mine": after.json()["data"]["bookmarked"],
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    # The other student bookmarked it; this one has not.
    assert result["other_students_bookmark"] is False
    assert result["mine"] is False


# ------------------------------------------------------------------ the boundary


def test_a_draft_question_is_a_404(database_url: str) -> None:
    """Unpublished content is admin-side only, so it is not addressable here.

    A 403 would be worse than a 404: it confirms the id exists, which turns this
    route into a probe for the ingestion pipeline's output.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        draft = await make_question(session, content, status="DRAFT")

        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id=draft.id))

        payload = response.json()
        return {
            "status": response.status_code,
            "title": payload.get("title"),
            "type": payload.get("type"),
            "text_has_answer": "correctAnswer" in response.text,
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 404
    assert result["title"] == "Question not found"
    # RFC 7807, like every other failure in this API.
    assert result["type"].endswith("/practice")
    assert result["text_has_answer"] is False


def test_a_soft_deleted_question_is_a_404(database_url: str) -> None:
    """Deleting a question must take it out of the student's reach immediately.

    `questions` is soft-deleted, so `status` stays PUBLISHED and only `deleted_at`
    changes. Every read path filters on it (`_published()` in the repository); this
    is the assertion that the new route goes through that helper instead of a
    hand-written query that forgot the second predicate.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            before = await client.get(DETAIL.format(question_id=question.id))

        question.deleted_at = datetime.now(UTC)
        await session.commit()

        async with Actor(session, student) as client:
            after = await client.get(DETAIL.format(question_id=question.id))

        async with observe(database_url) as other:
            row = await other.get(Question, question.id)

        return {
            "before": before.status_code,
            "after": after.status_code,
            # Read back on another connection: the row is still there, still
            # PUBLISHED, and no longer served.
            "status_in_db": row.status,
            "deleted_in_db": row.deleted_at is not None,
        }

    result = run_in_database(database_url, body)
    assert result["before"] == 200
    assert result["after"] == 404
    assert result["status_in_db"] == "PUBLISHED"
    assert result["deleted_in_db"] is True


def test_a_malformed_id_is_a_422_in_this_apis_error_shape(database_url: str) -> None:
    """Two error formats for one API is a client-side bug factory.

    The id is parsed in the route rather than declared as a `uuid.UUID` path
    parameter, so a bad id produces this service's RFC 7807 body rather than
    FastAPI's own `detail: [{loc: ...}]` shape.
    """

    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id="not-a-uuid"))
        payload = response.json()
        return {
            "status": response.status_code,
            "title": payload.get("title"),
            "detail": payload.get("detail"),
            "has_fastapi_detail_list": isinstance(payload.get("detail"), list),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 422
    assert result["title"] == "Invalid filter"
    assert "UUID" in result["detail"]
    assert result["has_fastapi_detail_list"] is False


def test_an_unknown_id_is_a_404_not_a_500(database_url: str) -> None:
    """The boring case, asserted because it is the one that reaches production."""

    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id=uuid.uuid4()))
        return {"status": response.status_code, "title": response.json().get("title")}

    result = run_in_database(database_url, body)
    assert result["status"] == 404
    assert result["title"] == "Question not found"


def test_every_option_is_returned_even_for_a_numerical_question(database_url: str) -> None:
    """A protective assertion, not a feature.

    `options` is a list built from `options_for`, not from a join, so a question
    with no option rows returns `[]` rather than the route failing. NUMERICAL
    questions have a key and no choices, and a client that assumed `options[0]`
    exists would crash on them.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(
            session,
            content,
            status="PUBLISHED",
            question_type="NUMERICAL",
            options=0,
            # PUBLISHED without a verifier trips ck_questions_published_requires_verifier,
            # the same constraint the publishing suite learns the hard way.
            verifier=student,
        )
        async with Actor(session, student) as client:
            response = await client.get(DETAIL.format(question_id=question.id))
        payload = response.json()["data"]
        return {
            "status": response.status_code,
            "options": payload["options"],
            "question_type": payload["questionType"],
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["question_type"] == "NUMERICAL"
    assert result["options"] == []


def test_the_detail_route_is_in_the_openapi_document(database_url: str) -> None:
    """The contract the frontend generates its types from.

    `/practice/questions` and the mock routes declare `response_model=None`, so this
    one carries a schema and a client can rely on the document rather than on a
    hand-written interface. A route that exists but is undocumented is how the two
    sides drift.
    """

    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            document = await client.get("/openapi.json")
        schema = document.json()
        operation = schema["paths"].get("/api/v1/questions/{question_id}", {}).get("get")
        return {
            "status": document.status_code,
            "operation": operation,
            "schemas": schema["components"],
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["operation"] is not None
    assert result["operation"]["summary"] == "Question detail"


def test_the_progress_row_is_not_created_by_looking_at_a_question(database_url: str) -> None:
    """Opening a question must not fabricate history.

    `GET` that writes is how a dashboard ends up counting questions the student
    never attempted. The route reads progress; only `POST /practice/answers` creates
    it. Asserted by reading the table back on a second connection.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            for _ in range(3):
                await client.get(DETAIL.format(question_id=question.id))

        async with observe(database_url) as other:
            attempts = (
                (
                    await other.execute(
                        select(PracticeAttempt).where(
                            PracticeAttempt.user_id == student.id,
                            PracticeAttempt.question_id == question.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            progress = (
                (
                    await other.execute(
                        select(UserQuestionProgress).where(
                            UserQuestionProgress.user_id == student.id,
                            UserQuestionProgress.question_id == question.id,
                        )
                    )
                )
                .scalars()
                .all()
            )

        return {"attempts": len(attempts), "progress_rows": len(progress)}

    result = run_in_database(database_url, body)
    assert result["attempts"] == 0
    assert result["progress_rows"] == 0
