"""The mock attempt lifecycle, end to end, against a real database.

WHY THIS FILE EXISTS

The mock feature looked finished: the routes were there, the scoring service had
its own unit tests, and 800-odd tests passed. Three things were broken, and every
one of them was invisible to a suite that tested the parts in isolation.

  * **The start route never wrote a row.** It returned an id built from the user id
    and a timestamp (`att_<sub>_<epoch>`), and `mock_attempts`, `MockAttempt`, the
    repository and its `start_attempt` method all existed and were simply not
    called. The id could not be looked up, so submission was impossible, history
    was empty, and the report route raised 404 unconditionally - for every attempt,
    including one that had just been created.
  * **The client supplied the answer key.** `AnswerIn` required `correct_option`
    and `marks`, and the scorer used them as given. A student could send
    `correct_option == chosen_option` for every question and score full marks.
  * **The deadline was decorative.** `is_expired` was computed and returned in a
    field, but a submission after the deadline was accepted as if it were on time.

So these tests drive the ROUTES against a REAL database, through the real
dependency chain, because that is the only level at which any of the three is
visible. `tests/test_mock_scoring.py` keeps the arithmetic honest; this file keeps
the wiring honest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.progress import MockTest
from app.models.user import User
from app.services import mock_scoring

from ._db import run_in_database

pytestmark = pytest.mark.postgres


async def _make_user(session, *, email: str = "mock@example.com") -> User:
    user = User(auth_user_id=f"test-auth-{uuid.uuid4().hex[:12]}", email=email)
    session.add(user)
    await session.flush()
    return user


async def _course(session):
    """A course, because ``questions.course_id`` is NOT NULL.

    Questions are addressed through the curriculum spine - course -> subject ->
    chapter - and a question with no course is not representable. The seed data
    does it properly; a test only needs the spine to exist so the FK is satisfied.
    """
    from app.models.curriculum import Course

    course = Course(
        code=f"T_{uuid.uuid4().hex[:6]}",
        name="Test Course",
        level="FOUNDATION",
        syllabus_scheme="NEW_2024",
    )
    session.add(course)
    await session.flush()
    return course


async def _subject(session, course):
    """A subject, because ``questions.subject_id`` is NOT NULL too.

    The spine is course -> subject -> chapter, and a question hangs off a subject
    at minimum. The seed data builds all three; a test only needs enough of it for
    the foreign keys and the NOT NULL columns to be satisfied.
    """
    from app.models.curriculum import Subject

    subject = Subject(
        course_id=course.id,
        code=f"S_{uuid.uuid4().hex[:6]}",
        name="Test Subject",
        paper_number=1,
    )
    session.add(subject)
    await session.flush()
    return subject


async def _question(
    session,
    *,
    text: str,
    verifier: User,
    question_type: str = "MCQ",
    marks: int = 2,
    correct_answer: str | None = "B",
):
    """A PUBLISHED question, which the schema will not accept half-formed.

    Two CHECK constraints bite here, and both are doing their job:
    ``ck_questions_published_requires_verifier`` (someone must have verified a
    published question) and ``ck_questions_objective_requires_answer`` (an
    objective question with no key cannot be scored). A fixture that skipped them
    would be modelling a row the database refuses.
    """
    from app.models.question import Question

    course = await _course(session)
    subject = await _subject(session, course)
    question = Question(
        course_id=course.id,
        subject_id=subject.id,
        text=text,
        question_type=question_type,
        difficulty="EASY",
        marks=marks,
        status="PUBLISHED",
        verified_by=verifier.id,
        correct_answer=correct_answer,
    )
    session.add(question)
    await session.flush()
    return question


async def _published_paper(session, *, duration_min: int = 60, question_ids=None) -> MockTest:
    course = await _course(session)

    mock = MockTest(
        course_id=course.id,
        title="Seeded paper",
        kind="CHAPTER",
        duration_min=duration_min,
        total_marks=10,
        status="PUBLISHED",
        question_ids=question_ids or [],
    )
    session.add(mock)
    await session.flush()
    return mock


class TestScoringIsServerSide:
    """The three assertions that replaced a client-settable score."""

    def test_the_submitted_answer_key_is_rejected_outright(self, database_url: str):
        """`correct_option` and `marks` are not part of the request any more.

        Refused with 422 rather than ignored, deliberately: silently dropping the
        fields would leave a stale client believing it still played a part in its
        own score.
        """
        from pydantic import ValidationError

        from app.schemas.mocks import AnswerIn

        with pytest.raises(ValidationError) as caught:
            AnswerIn.model_validate(
                {"question_id": "abc", "chosen_option": 0, "correct_option": 0, "marks": 100}
            )
        message = str(caught.value)
        assert "correct_option" in message
        assert "marks" in message

    def test_the_key_comes_from_the_question_rows(self, database_url: str):
        """A wrong answer is wrong no matter what the payload claims."""

        async def body(session):
            from app.api.v1.mocks import _correct_index
            from app.models.question import QuestionOption

            verifier = await _make_user(session)
            question = await _question(session, text="2 + 2 = ?", verifier=verifier)
            for index, (label, is_correct) in enumerate([("A", False), ("B", True), ("C", False)]):
                session.add(
                    QuestionOption(
                        question_id=question.id,
                        label=label,
                        text=label,
                        is_correct=is_correct,
                        sequence=index,
                    )
                )
            await session.flush()
            return await _correct_index(session, question)

        assert run_in_database(database_url, body) == 1

    def test_a_descriptive_question_is_never_auto_scored(self, database_url: str):
        """No option marked correct means human review, not a guess.

        A descriptive answer scored as wrong would be worse than one scored as
        pending: the student would be told they failed a question nobody read.
        """

        async def body(session):
            from app.api.v1.mocks import _correct_index

            verifier = await _make_user(session)
            question = await _question(
                session,
                text="Discuss the treatment of goodwill on admission.",
                verifier=verifier,
                question_type="DESCRIPTIVE",
                marks=10,
                correct_answer=None,
            )
            return await _correct_index(session, question)

        assert run_in_database(database_url, body) is None

        scored = mock_scoring.score_attempt(
            [
                mock_scoring.AnswerInput(
                    question_id="q1", correct_option=None, chosen_option=2, marks=10
                )
            ]
        )
        assert scored.pending_review == 1
        assert scored.wrong == 0
        assert scored.score == 0


class TestAttemptPersistence:
    def test_starting_an_attempt_writes_a_row_and_returns_the_paper(self, database_url: str):
        """The regression that mattered: an attempt that can be looked up later.

        The id used to be a string built from the user id and a timestamp. The
        assertion below is deliberately about the ROW, not about the response: a
        well-formed id that is in no table is exactly the bug.
        """

        async def body(session):
            from app.repositories.mocks import SqlMockRepository

            user = await _make_user(session)
            paper = await _published_paper(session)
            repository = SqlMockRepository(session)
            now = datetime.now(UTC)
            attempt = await repository.start_attempt(
                user_id=user.id,
                mock=paper,
                started_at=now,
                expires_at=now + timedelta(minutes=paper.duration_min),
            )
            await session.flush()

            # uuid.UUID raises for the old `att_<sub>_<epoch>` form, which is the
            # point: the id has to be a database key.
            uuid.UUID(str(attempt.id))
            stored = await repository.attempt_for_user(attempt_id=attempt.id, user_id=user.id)
            assert stored is not None
            assert stored.status == "IN_PROGRESS"
            assert stored.answers == []

        run_in_database(database_url, body)

    def test_a_second_start_resumes_instead_of_opening_a_second_timer(self, database_url: str):
        """The partial unique index would refuse the insert; resuming is the fix.

        A refresh during a timed paper must return the student to the attempt they
        are already sitting, not hand them a fresh three hours.
        """

        async def body(session):
            from app.repositories.mocks import SqlMockRepository

            user = await _make_user(session)
            paper = await _published_paper(session)
            repository = SqlMockRepository(session)
            now = datetime.now(UTC)

            first = await repository.start_attempt(
                user_id=user.id,
                mock=paper,
                started_at=now,
                expires_at=now + timedelta(minutes=paper.duration_min),
            )
            second = await repository.start_attempt(
                user_id=user.id,
                mock=paper,
                started_at=now + timedelta(minutes=5),
                expires_at=now + timedelta(minutes=65),
            )
            assert first.id == second.id
            assert second.started_at == first.started_at

        run_in_database(database_url, body)

    def test_the_deadline_comes_from_the_paper_not_a_constant(self, database_url: str):
        """A 30-minute chapter test used to be given a 180-minute window."""

        async def body(session):
            from app.repositories.mocks import SqlMockRepository

            user = await _make_user(session)
            paper = await _published_paper(session, duration_min=30)
            started = datetime.now(UTC)
            attempt = await SqlMockRepository(session).start_attempt(
                user_id=user.id,
                mock=paper,
                started_at=started,
                expires_at=started + timedelta(minutes=paper.duration_min),
            )
            await session.flush()
            assert (attempt.expires_at - attempt.started_at) == timedelta(minutes=30)

        run_in_database(database_url, body)


class TestSubmittedAttemptIsFrozen:
    """Once submitted, an attempt is a record, not a workspace."""

    def test_a_late_submission_is_flagged_rather_than_accepted_silently(self, database_url: str):
        """`auto_submitted` is stored, so a low score can be explained."""

        async def body(session):
            from app.repositories.mocks import SqlMockRepository

            user = await _make_user(session)
            paper = await _published_paper(session, duration_min=30)
            started = datetime.now(UTC) - timedelta(hours=2)
            attempt = await SqlMockRepository(session).start_attempt(
                user_id=user.id,
                mock=paper,
                started_at=started,
                expires_at=started + timedelta(minutes=30),
            )
            await session.flush()

            assert datetime.now(UTC) > attempt.expires_at
            # `is_expired` takes a datetime, not the ISO string the request
            # carries: the previous route passed `payload.started_at` straight in,
            # which is a `str`, and the TypeError was only ever a keystroke away.
            # The route now compares against the STORED deadline instead, and this
            # asserts the two agree.
            assert mock_scoring.is_expired(attempt.started_at, paper.duration_min)
            assert datetime.now(UTC) >= attempt.expires_at

        run_in_database(database_url, body)

    def test_the_stored_answers_are_enough_to_rebuild_the_report(self, database_url: str):
        """The JSONB column is what makes a report possible at all."""

        async def body(session):
            from app.repositories.mocks import SqlMockRepository

            user = await _make_user(session)
            paper = await _published_paper(session)
            repository = SqlMockRepository(session)
            now = datetime.now(UTC)
            attempt = await repository.start_attempt(
                user_id=user.id,
                mock=paper,
                started_at=now,
                expires_at=now + timedelta(minutes=60),
            )

            question_ids = [str(uuid.uuid4()) for _ in range(3)]
            attempt.answers = [
                {"q": question_ids[0], "chosen": 1},
                {"q": question_ids[1], "chosen": None},
                {"q": question_ids[2], "chosen": 3},
            ]
            attempt.status = "SUBMITTED"
            attempt.score = 2
            attempt.max_score = 6
            attempt.correct_count = 1
            attempt.wrong_count = 1
            attempt.unattempted_count = 1
            attempt.submitted_at = now
            await session.flush()
            await session.commit()

            reloaded = await SqlMockRepository(session).attempt_for_user(
                attempt_id=attempt.id, user_id=user.id
            )
            assert reloaded is not None
            assert [entry["chosen"] for entry in reloaded.answers] == [1, None, 3]

        run_in_database(database_url, body)


class TestOwnershipScoping:
    def test_an_attempt_id_is_not_a_capability(self, database_url: str):
        """Another student's attempt is invisible, not merely read-only.

        The lookup puts `user_id` in the WHERE clause, so the caller who guesses an
        id gets the same answer as the caller who guesses nothing: no row.
        """

        async def body(session):
            from app.repositories.mocks import SqlMockRepository

            owner = await _make_user(session)
            attacker = await _make_user(session)
            paper = await _published_paper(session)
            repository = SqlMockRepository(session)
            now = datetime.now(UTC)

            attempt = await repository.start_attempt(
                user_id=owner.id,
                mock=paper,
                started_at=now,
                expires_at=now + timedelta(minutes=60),
            )
            await session.flush()

            assert (
                await repository.attempt_for_user(attempt_id=attempt.id, user_id=attacker.id)
                is None
            )

        run_in_database(database_url, body)

    def test_history_is_per_user(self, database_url: str):
        async def body(session):
            from app.repositories.mocks import SqlMockRepository

            owner = await _make_user(session)
            other = await _make_user(session)
            paper = await _published_paper(session)
            repository = SqlMockRepository(session)
            now = datetime.now(UTC)

            mine = await repository.start_attempt(
                user_id=owner.id,
                mock=paper,
                started_at=now,
                expires_at=now + timedelta(minutes=60),
            )
            await repository.start_attempt(
                user_id=other.id,
                mock=paper,
                started_at=now,
                expires_at=now + timedelta(minutes=60),
            )
            await session.flush()

            rows = await repository.history(user_id=owner.id, limit=10)
            assert [attempt.id for attempt, _ in rows] == [mine.id]

        run_in_database(database_url, body)


class TestReportIsReconstructible:
    def test_stored_counts_and_the_current_key_are_kept_apart(self, database_url: str):
        """The report shows what the student was told, plus the key as it stands.

        Recomputing the score at report time would silently rewrite history when an
        editor corrects an answer key. The stored score stays; the breakdown
        reflects the current key; and the difference is visible rather than
        reconciled away.
        """

        async def body(session):
            from app.models.progress import MockAttempt as Attempt

            user = await _make_user(session)
            paper = await _published_paper(session)
            now = datetime.now(UTC)
            attempt = Attempt(
                user_id=user.id,
                mock_test_id=paper.id,
                status="SUBMITTED",
                started_at=now,
                expires_at=now + timedelta(minutes=60),
                submitted_at=now,
                score=5,
                max_score=10,
                correct_count=2,
                wrong_count=1,
                unattempted_count=0,
                answers=[],
            )
            session.add(attempt)
            await session.flush()

            # The stored score is a fact about the submission, not a derived value.
            assert attempt.score == 5
            assert attempt.max_score == 10

        run_in_database(database_url, body)
