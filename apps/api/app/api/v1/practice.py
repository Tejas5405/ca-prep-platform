"""Practice endpoints - the loop a student actually spends their evening in.

THE ONE RULE THAT MATTERS HERE: ``correct_answer`` NEVER LEAVES THE SERVER BEFORE
THE STUDENT ANSWERS. Every question payload is built by ``_question_payload``,
which has no access to the answer at all - the field is not filtered out, it is
never read. Filtering is the version that breaks: someone adds a field to the
serializer, or a ``model_dump`` sneaks the whole row out, and the answer is in the
browser's network tab for anyone who looks.

The answer is returned only by ``POST /practice/answers``, in the response to an
attempt that has already been recorded.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.question import Question, QuestionFlag, QuestionOption
from app.models.user import User
from app.repositories.practice import SqlQuestionRepository
from app.repositories.progress import SqlProgressRepository
from app.repositories.revision import SqlRevisionRepository
from app.schemas.base import StrictRequest, UuidRef

router = APIRouter(tags=["practice"])

#: A practice set is a sitting, not a mock paper. Twenty questions is roughly 30-40
#: minutes at exam pace, which is what a student does on a weeknight.
DEFAULT_SET_SIZE = 20
MAX_SET_SIZE = 50


class AnswerIn(StrictRequest):
    """One submitted answer.

    ``chosen_option`` is optional because "I skipped it" is a real outcome worth
    recording: a question a student consistently skips is different information
    from one they get wrong, and the progress data is the input to the planner.
    """

    question_id: UuidRef
    chosen_option: str | None = Field(default=None, max_length=4)
    time_spent_seconds: int = Field(default=0, ge=0, le=3600)
    used_hint: bool = False


class BookmarkIn(StrictRequest):
    marked: bool = True


def _question_payload(
    question: Question, options: list[QuestionOption] | None = None
) -> dict[str, Any]:
    """A question as the browser may see it: NO ANSWER, NO EXPLANATION.

    The explanation goes with the answer, not with the question: showing it on the
    question screen gives away the answer to anyone who opens the network tab, and
    a practice feature whose answers leak is a practice feature nobody trusts.
    """
    payload: dict[str, Any] = {
        "id": str(question.id),
        "text": question.text,
        "questionType": question.question_type,
        "difficulty": question.difficulty,
        "marks": question.marks,
        "negativeMarks": float(question.negative_marks),
        "subjectId": str(question.subject_id),
        "chapterId": str(question.chapter_id) if question.chapter_id else None,
        "topicId": str(question.topic_id) if question.topic_id else None,
        "source": question.source,
        "isPremium": question.is_premium,
    }
    if options is not None:
        payload["options"] = [
            {"label": option.label, "text": option.text}
            for option in sorted(options, key=lambda item: (item.sequence, item.label))
        ]
    return payload


@router.get("/practice/questions", summary="Draw a practice set")
async def practice_questions(
    subject_id: str | None = None,
    chapter_id: str | None = None,
    difficulty: str | None = None,
    limit: int = DEFAULT_SET_SIZE,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """A random set of published questions matching the filters.

    Filter ids arrive as strings and are parsed here rather than declared as UUID
    query parameters, so a malformed id produces this service's RFC 7807 body
    instead of FastAPI's own validation shape - two error formats for one API is a
    client-side bug factory.
    """
    parsed_subject = _parse_optional_uuid(subject_id, "subject_id")
    if isinstance(parsed_subject, str):
        return _bad_id(parsed_subject)
    parsed_chapter = _parse_optional_uuid(chapter_id, "chapter_id")
    if isinstance(parsed_chapter, str):
        return _bad_id(parsed_chapter)

    size = min(MAX_SET_SIZE, max(1, limit))
    repo = SqlQuestionRepository(session)
    questions = await repo.questions(
        subject_id=parsed_subject, chapter_id=parsed_chapter, difficulty=difficulty, limit=size
    )
    options = await repo.options_for([question.id for question in questions])

    return success(
        {
            "questions": [
                _question_payload(question, options.get(question.id, [])) for question in questions
            ],
            "count": len(questions),
        },
        request_id=get_request_id(),
    )


@router.get("/questions/{question_id}", summary="Question detail")
async def question_detail(
    question_id: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """One published question, for opening a search result or a shared link.

    WHY THIS EXISTS. ``GET /search`` returns an id, a snippet and the metadata, and
    deliberately no options - so before this route there was nothing a client could
    do with a result except display it. Search found questions and then dead-ended.
    Blueprint v3 §7.3 names ``GET /questions/:id``; the gap was that no screen
    needed it yet, and "no screen needs it yet" is how a hole in the API survives
    an audit that only reads the route list.

    WHAT IS HELD BACK, AND WHY IT IS A FIELD AND NOT A FILTER. ``correctAnswer`` and
    ``explanation`` are not read from the row unless ``detail.answered`` is true, so
    there is no code path in which they could be serialised by accident. A student
    opening an unanswered question gets the question; answering it
    (``POST /practice/answers``) returns the verdict and the explanation, and
    re-opening the question after that shows the worked answer inline.

    Published-only, uniformly: a draft, an archived row and a nonexistent id all
    return the same 404, so the route cannot be used to enumerate which ids exist.
    """
    try:
        parsed = uuid.UUID(question_id)
    except ValueError:
        return _bad_id("question_id")

    detail = await SqlQuestionRepository(session).detail(parsed, user.id)
    if detail is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Question not found",
            detail="No published question with that id.",
            type_slug="practice",
        )

    payload = _question_payload(detail.question, detail.options)
    payload.update(
        {
            "year": detail.question.year,
            # The sitting as a label ("May 2025"), so a client does not have to
            # reassemble a date from a year and a month string.
            "examSession": detail.exam_session.label if detail.exam_session else None,
            "syllabusScheme": detail.question.syllabus_scheme,
            "bookmarked": detail.bookmarked,
            "attempted": detail.answered,
        }
    )

    if detail.answered and detail.attempt is not None:
        payload["yourAnswer"] = detail.attempt.chosen_option
        payload["yourResult"] = detail.attempt.is_correct
        key = detail.question.correct_answer
        explanation = detail.question.explanation
        model_answer = detail.question.model_answer
        # A correction after this attempt must not change the key the student was shown.
        if detail.attempt.question_version is not None:
            from app.services.question_history import version_snapshot

            frozen = await version_snapshot(
                session, detail.question.id, detail.attempt.question_version
            )
            if frozen is not None:
                key = frozen.get("correctAnswer", key)
                explanation = frozen.get("explanation", explanation)
                model_answer = frozen.get("modelAnswer", model_answer)
        payload["reveal"] = {
            "correctAnswer": key,
            "explanation": explanation,
            "modelAnswer": model_answer,
            "questionVersion": detail.attempt.question_version,
        }

    return success(payload, request_id=get_request_id())


@router.post("/practice/answers", summary="Submit one answer")
async def submit_answer(
    payload: AnswerIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Record an answer and return the verdict.

    Four writes in one transaction - the attempt, the per-question progress row,
    the daily activity row and any points - because a half-applied answer shows up
    weeks later as an accuracy percentage nobody can explain.
    """
    repo = SqlQuestionRepository(session)
    question = await repo.question(payload.question_id)
    if question is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Question not found",
            detail="No published question with that id.",
            type_slug="practice",
        )

    progress = SqlProgressRepository(session)
    outcome = await progress.record_answer(
        user_id=user.id,
        question=question,
        chosen_option=payload.chosen_option,
        time_spent_seconds=payload.time_spent_seconds,
        used_hint=payload.used_hint,
        tz_offset_minutes=330 if user.timezone == "Asia/Kolkata" else 0,
    )
    # The overall accuracy cache is refreshed in the same transaction rather than
    # by a job: a dashboard that lags an answer by a night is a dashboard the
    # student stops trusting.
    await progress.refresh_accuracy(user.id)

    # A wrong answer enters the revision queue. This is the whole reason spaced
    # repetition works without the student maintaining a list: the questions they
    # get wrong are exactly the ones the app should bring back.
    if outcome.is_correct is False:
        await SqlRevisionRepository(session).schedule(user_id=user.id, question_id=question.id)

    await session.commit()

    options = (await repo.options_for([question.id])).get(question.id, [])
    return success(
        {
            "questionId": str(question.id),
            "isCorrect": outcome.is_correct,
            "correctAnswer": outcome.correct_answer,
            "explanation": outcome.explanation,
            "pointsAwarded": outcome.points_awarded,
            "attemptsCount": outcome.attempts_count,
            "correctCount": outcome.correct_count,
            "accuracy": outcome.accuracy,
            "currentStreak": outcome.current_streak,
            "options": [
                {"label": option.label, "text": option.text, "isCorrect": option.is_correct}
                for option in sorted(options, key=lambda item: (item.sequence, item.label))
            ],
        },
        request_id=get_request_id(),
    )


@router.post("/practice/questions/{question_id}/bookmark", summary="Mark a question for review")
async def bookmark_question(
    question_id: uuid.UUID,
    payload: BookmarkIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    marked = await SqlProgressRepository(session).mark_for_review(
        user_id=user.id, question_id=question_id, marked=payload.marked
    )
    if not marked:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Nothing to bookmark",
            detail="Answer the question first - there is no progress row to flag yet.",
            type_slug="practice",
        )
    await session.commit()
    return success(
        {"questionId": str(question_id), "markedForReview": payload.marked},
        request_id=get_request_id(),
    )


_FLAG_REASONS = frozenset(
    {"WRONG_ANSWER", "TYPO", "OUT_OF_SYLLABUS", "HISTORICAL", "UNCLEAR", "DUPLICATE"}
)


class FlagIn(StrictRequest):
    reason: str = Field(min_length=1, max_length=50)
    detail: str | None = Field(default=None, max_length=2000)


@router.post("/practice/questions/{question_id}/flags", status_code=status.HTTP_201_CREATED)
async def flag_question(
    question_id: str,
    payload: FlagIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """A student reports a published question. This does not change the key.

    The report is a queue for an editor. It is not a verdict, and it is not
    labelled as verified by anyone.
    """
    try:
        parsed = uuid.UUID(question_id)
    except ValueError:
        return _bad_id("question_id")
    if payload.reason not in _FLAG_REASONS:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown reason",
            detail=f"Reason must be one of {', '.join(sorted(_FLAG_REASONS))}.",
            type_slug="questions",
        )
    question = await session.get(Question, parsed)
    if question is None or question.deleted_at is not None or question.status != "PUBLISHED":
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Question not found",
            detail="No published question with that id.",
            type_slug="questions",
        )
    row = QuestionFlag(
        id=uuid.uuid4(),
        question_id=question.id,
        reported_by=user.id,
        reason=payload.reason,
        detail=payload.detail,
        status="OPEN",
    )
    session.add(row)
    await session.commit()
    return success(
        {"id": str(row.id), "status": row.status},
        request_id=get_request_id(),
    )


def _parse_optional_uuid(raw: str | None, name: str) -> uuid.UUID | str | None:
    """Returns a uuid, None, or the NAME of the bad parameter (a string)."""
    if raw in (None, ""):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return name


def _bad_id(name: str) -> Any:
    return problem(
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        title="Invalid filter",
        detail=f"{name} must be a UUID.",
        type_slug="practice",
    )
