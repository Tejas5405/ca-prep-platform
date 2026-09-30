"""Mock exam endpoints - blueprint v3 §7.3 and §18.1."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_redis_client, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.identity import get_current_user
from app.core.security import Principal, get_current_principal
from app.models.progress import MockAttempt, MockTest
from app.models.question import Question, QuestionOption
from app.models.user import User
from app.repositories.mocks import SqlMockRepository
from app.schemas.mocks import (
    StartAttemptIn,
    SubmitAttemptIn,
)
from app.services import mock_scoring
from app.services.entitlements import entitlements_for

from ._shared import (
    _attempt_payload,
    _options_by_question,
    _paper_questions,
    logger,
)

router = APIRouter(tags=["mocks"])

"""Mock papers and the attempt lifecycle: start, read, submit, score."""


@router.get("/mocks", summary="List mock tests")
async def list_mocks(
    page: int = 1,
    limit: int = 20,
    kind: str | None = None,
    course_id: str | None = None,
    level: str | None = None,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """List published mock papers.

    PREMIUM PAPERS ARE LISTED, NOT HIDDEN. A student who cannot see what they are
    missing has no reason to upgrade, so a locked paper shows its title, length and
    marks. ``locked`` is derived from the entitlement SERVER-SIDE - and the start
    route enforces it again, because a flag in a list response is a UI hint and
    never the control.
    """
    page = max(1, page)
    limit = min(100, max(1, limit))

    parsed_course_id = None
    if course_id:
        try:
            parsed_course_id = uuid.UUID(course_id)
        except ValueError:
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Invalid course id",
                detail="course_id must be a UUID.",
                type_slug="mocks",
            )

    repository = SqlMockRepository(session)
    rows, total = await repository.list_published(
        course_id=parsed_course_id,
        kind=kind,
        level=level,
        page=page,
        limit=limit,
    )
    entitlements = await entitlements_for(session, user.id)

    return paginated(
        [
            {
                "id": str(mock.id),
                "courseId": str(mock.course_id),
                "subjectId": str(mock.subject_id) if mock.subject_id else None,
                "title": mock.title,
                "kind": mock.kind,
                "durationMin": mock.duration_min,
                "totalMarks": mock.total_marks,
                "isPremium": mock.is_premium,
                # A full-length paper is the product's "exam pack", so it needs the
                # pack entitlement; any other premium paper needs unlimited mocks.
                "locked": mock.is_premium
                and not entitlements.allows(
                    "mock_exam_pack" if mock.kind == "FULL_LENGTH" else "unlimited_mocks"
                ),
                "questionCount": len(mock.question_ids or []),
            }
            for mock in rows
        ],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.post("/mocks/{mock_id}/attempts", status_code=status.HTTP_201_CREATED)
async def start_attempt(
    mock_id: str,
    payload: StartAttemptIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    principal: Principal = Depends(get_current_principal),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Start (or resume) a timed attempt, and hand back the question paper.

    WHAT WAS WRONG HERE

    The attempt id used to be a string built from the user id and a timestamp
    (`att_<sub>_<epoch>`), returned to the client and then looked up nowhere: the
    `mock_attempts` table existed, with a repository to write it, and this route
    never touched either. Submitting such an id 404ed, the dashboard showed no
    history, and every attempt in the database - there were none - would have been
    impossible to review.

    It now writes a real row, resumes an in-progress attempt instead of burning a
    second timer (the partial unique index would refuse the second insert anyway),
    and returns the paper WITHOUT the answer key.

    Three protections that the blueprint calls out and that are easy to omit:

    1. Attempt starts are rate limited SEPARATELY from the global limit (10/hour
       vs 100/minute). Without a tighter limit on this specific route, the
       leaderboard can be farmed by scripting attempt creation.
    2. ``expires_at`` is computed server-side from the PAPER's duration, and
       returned to the client. The client clock is never trusted for the deadline -
       and the duration is the paper's, not a constant: a 30-minute chapter test
       used to be given a three-hour window.
    3. The entitlement is checked HERE, not just in the list response. A ``locked``
       flag is a UI hint; a student who has the id can call this route directly.
    """
    try:
        mock_uuid = uuid.UUID(mock_id)
    except ValueError:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid mock id",
            detail="mock_id must be a UUID.",
            type_slug="mocks",
        )

    client = get_redis_client()
    hour_key = f"rate:mock-start:{principal.auth_user_id}:{datetime.now(UTC):%Y%m%d%H}"

    try:
        count = await client.incr(hour_key)
        if count == 1:
            await client.expire(hour_key, 3600)
        if count > settings.mock_attempts_per_hour:
            return problem(
                status=status.HTTP_429_TOO_MANY_REQUESTS,
                title="Too many attempts started",
                detail=(f"Limit is {settings.mock_attempts_per_hour} attempt starts per hour."),
                type_slug="rate-limit",
            )
    except Exception as exc:  # noqa: BLE001
        # FAIL OPEN on a Redis outage, deliberately, for starting an attempt.
        # Failing closed would make the whole mock feature unavailable whenever
        # the cache blips. This is a documented trade-off, not an oversight:
        # the limit is anti-abuse, not a security control.
        logger.warning("Rate-limit check skipped (redis unavailable): %s", exc)

    repository = SqlMockRepository(session)
    mock = await repository.published(mock_uuid)
    if mock is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Mock not found",
            detail="No published paper with that id.",
            type_slug="mocks",
        )

    if mock.is_premium:
        entitlements = await entitlements_for(session, user.id)
        needed = "mock_exam_pack" if mock.kind == "FULL_LENGTH" else "unlimited_mocks"
        if not entitlements.allows(needed):
            return problem(
                status=status.HTTP_403_FORBIDDEN,
                title="Upgrade required",
                detail=f"This paper needs the {needed} entitlement.",
                type_slug="entitlement",
            )

    questions = await repository.questions_for(mock)
    if not questions:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Empty paper",
            detail="This paper has no published questions yet.",
            type_slug="mocks",
        )

    started_at = datetime.now(UTC)
    attempt = await repository.start_attempt(
        user_id=user.id,
        mock=mock,
        started_at=started_at,
        expires_at=started_at + timedelta(minutes=mock.duration_min),
    )
    await session.commit()

    options = await _options_by_question(session, [question.id for question in questions])
    return success(
        {
            **_attempt_payload(attempt, mock),
            "resumed": attempt.started_at != started_at,
            "questions": _paper_questions(questions, options),
        },
        request_id=get_request_id(),
    )


@router.get("/mock-attempts", summary="Your mock attempt history")
async def list_attempts(
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Finished and in-progress attempts, newest first, for the student's dashboard."""
    limit = min(50, max(1, limit))
    rows = await SqlMockRepository(session).history(user_id=user.id, limit=limit)
    return success(
        [
            {
                **_attempt_payload(attempt, mock),
                "submittedAt": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
                "correctCount": attempt.correct_count,
                "wrongCount": attempt.wrong_count,
                "unattemptedCount": attempt.unattempted_count,
                "timeTakenSeconds": attempt.time_taken_seconds,
            }
            for attempt, mock in rows
        ],
        request_id=get_request_id(),
    )


@router.get("/mock-attempts/{attempt_id}", summary="Resume an attempt")
async def read_attempt(
    attempt_id: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Reload an in-progress attempt with its paper, so a refresh does not lose it."""
    try:
        attempt_uuid = uuid.UUID(attempt_id)
    except ValueError:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Attempt not found",
            detail="No attempt with that id.",
            type_slug="mocks",
        )

    repository = SqlMockRepository(session)
    attempt = await repository.attempt_for_user(attempt_id=attempt_uuid, user_id=user.id)
    if attempt is None:
        # 404 rather than 403 for someone else's attempt: distinguishing the two
        # confirms that the id exists, which is a fact about another student.
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Attempt not found",
            detail="No attempt with that id.",
            type_slug="mocks",
        )

    mock = await session.get(MockTest, attempt.mock_test_id)
    if mock is None:
        # The FK is ON DELETE CASCADE and mock_tests is not soft-deleted, so this
        # is unreachable in practice; returning a clean 404 beats a 500 if it is
        # ever reached through a data repair.
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Attempt not found",
            detail="The paper for this attempt no longer exists.",
            type_slug="mocks",
        )

    questions = await repository.questions_for(mock)
    options = await _options_by_question(session, [question.id for question in questions])
    return success(
        {
            **_attempt_payload(attempt, mock),
            "questions": _paper_questions(questions, options),
        },
        request_id=get_request_id(),
    )


@router.post("/mock-attempts/{attempt_id}/submit", summary="Submit Attempt")
async def submit_attempt(
    attempt_id: str,
    payload: SubmitAttemptIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Score a submitted paper from the DATABASE, persist it, and return the result.

    FOUR THINGS THAT WERE WRONG

    1. **The client supplied the answer key.** `AnswerIn` required
       ``correct_option`` and ``marks``, and the scorer used them as given, so a
       student could post ``correct_option == chosen_option`` for every question
       and score full marks. Both now come from the question row.
    2. **Nothing was saved.** The score was computed and discarded, so an attempt
       could not be reviewed, ranked or shown in history. The `mock_attempts` row
       is updated here, inside the request's transaction.
    3. **The deadline was not enforced.** ``is_expired`` was computed and returned
       as a flag, but the submission was accepted either way, so the timer was
       decorative. A late submission is now recorded as AUTO_SUBMITTED and the
       flag is stored.
    4. **Ranking used client-supplied scores.** ``other_scores`` came from the
       request body, so a student's percentile was whatever they said the cohort
       scored. Rank is computed against the OTHER ATTEMPTS OF THIS PAPER in the
       database, and ``other_scores`` is only accepted from a test/dev call.
    """
    try:
        attempt_uuid = uuid.UUID(attempt_id)
    except ValueError:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Attempt not found",
            detail="No attempt with that id.",
            type_slug="mocks",
        )

    repository = SqlMockRepository(session)
    attempt = await repository.attempt_for_user(attempt_id=attempt_uuid, user_id=user.id)
    if attempt is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Attempt not found",
            detail="No attempt with that id.",
            type_slug="mocks",
        )

    if attempt.status != "IN_PROGRESS":
        # Idempotence would HIDE a real client bug: a double submit usually means
        # the timer fired and the student also pressed submit, and the second
        # payload may carry answers the first did not. Refusing is honest, and the
        # report route already serves the stored result.
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Already submitted",
            detail="This attempt has already been submitted. Open its report instead.",
            type_slug="mocks",
        )

    mock = await session.get(MockTest, attempt.mock_test_id)
    if mock is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Attempt not found",
            detail="The paper for this attempt no longer exists.",
            type_slug="mocks",
        )

    questions = await repository.questions_for(mock)
    by_id = {question.id: question for question in questions}

    chosen_by_question: dict[uuid.UUID, int | None] = {}
    for submitted in payload.answers:
        try:
            question_uuid = uuid.UUID(submitted.question_id)
        except ValueError:
            # An id that is not in the paper is ignored rather than rejected: a
            # stale client can hold a question that was unpublished mid-paper, and
            # failing the whole submission for that would lose every answer.
            continue
        if question_uuid in by_id:
            chosen_by_question[question_uuid] = submitted.chosen_option

    scored = mock_scoring.score_attempt(
        [
            mock_scoring.AnswerInput(
                question_id=str(question.id),
                correct_option=await _correct_index(session, question),
                chosen_option=chosen_by_question.get(question.id),
                marks=question.marks,
            )
            for question in questions
        ]
    )

    # Server-computed lateness, from the stored deadline rather than from the
    # client's `started_at`, which a browser can send as anything.
    now = datetime.now(UTC)
    late = now > attempt.expires_at
    if late:
        logger.info("attempt %s submitted after its deadline; recording as auto", attempt.id)

    attempt.status = "AUTO_SUBMITTED" if late else "SUBMITTED"
    attempt.submitted_at = now
    attempt.auto_submitted = late
    attempt.score = scored.score
    attempt.max_score = scored.max_score
    attempt.correct_count = scored.correct
    attempt.wrong_count = scored.wrong
    attempt.unattempted_count = scored.unattempted
    attempt.pending_review_count = scored.pending_review
    attempt.time_taken_seconds = max(0, int((now - attempt.started_at).total_seconds()))
    from app.services.question_history import current_version

    stored_answers = []
    for question in questions:
        stored_answers.append(
            {
                "q": str(question.id),
                "chosen": chosen_by_question.get(question.id),
                # The index the student was marked against. The report uses this,
                # not the live option row, so a later correction does not rescore them.
                "correct": await _correct_index(session, question),
                "version": await current_version(session, question.id),
            }
        )
    attempt.answers = stored_answers

    others = await _cohort_scores(session, mock.id, exclude=attempt.id)
    ranked = mock_scoring.compute_rank(scored.score, others)
    await session.commit()

    return success(
        {
            "attemptId": str(attempt.id),
            "mockTestId": str(mock.id),
            "score": scored.score,
            "maxScore": scored.max_score,
            "correct": scored.correct,
            "wrong": scored.wrong,
            "unattempted": scored.unattempted,
            "pendingReview": scored.pending_review,
            "rank": ranked.rank,
            "totalAttempts": ranked.total_attempts,
            "percentile": ranked.percentile,
            "autoSubmitted": late,
            "timeTakenSeconds": attempt.time_taken_seconds,
        },
        request_id=get_request_id(),
    )


async def _correct_index(session: AsyncSession, question: Question) -> int | None:
    """The index of the correct option, read from the option rows.

    Returns None for a descriptive question, which routes to human review instead
    of being guessed at. Index rather than label because the client sends the index
    of the option it rendered, and comparing a label against an index would mark
    every answer wrong - a bug that would look like a scoring disaster rather than
    a type mismatch.
    """
    rows = await session.execute(
        select(QuestionOption)
        .where(QuestionOption.question_id == question.id)
        .order_by(QuestionOption.sequence, QuestionOption.label)
    )
    for index, option in enumerate(rows.scalars().all()):
        if option.is_correct:
            return index
    return None


async def _cohort_scores(
    session: AsyncSession, mock_test_id: uuid.UUID, *, exclude: uuid.UUID
) -> list[int]:
    """Every other SUBMITTED score on this paper, for ranking.

    Scoped to one paper rather than the whole platform: "how did I do" is only
    meaningful against people who sat the same exam.
    """
    rows = await session.execute(
        select(MockAttempt.score).where(
            MockAttempt.mock_test_id == mock_test_id,
            MockAttempt.status.in_(("SUBMITTED", "AUTO_SUBMITTED")),
            MockAttempt.score.is_not(None),
            MockAttempt.id != exclude,
        )
    )
    return [int(score) for (score,) in rows.all()]
