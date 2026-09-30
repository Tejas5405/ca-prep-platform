"""Mock exam endpoints - blueprint v3 §7.3 and §18.1."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.progress import MockTest
from app.models.user import User
from app.repositories.mocks import SqlMockRepository

from ._shared import (
    _options_by_question,
)

router = APIRouter(tags=["mocks"])

"""The post-attempt report."""


@router.get("/mock-attempts/{attempt_id}/report", summary="Attempt Report")
async def attempt_report(
    attempt_id: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """The finished paper with the answer key, question by question.

    This route used to raise 404 unconditionally, with a comment claiming the
    attempt was scoped to the principal - which it was, and which made the 404
    correct, because no attempt was ever persisted to scope to. It now reads the
    stored attempt and its stored answers, and joins them to the paper.

    AUTHORIZATION. The attempt is looked up by ``attempt_for_user``, which puts
    ``user_id`` in the WHERE clause. An attempt id is not a capability, and a
    student who guesses another's id gets the same 404 as a student who guesses
    nothing at all.
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

    if attempt.status == "IN_PROGRESS":
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Attempt still in progress",
            detail="Submit the paper before opening its report.",
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
    options = await _options_by_question(session, [question.id for question in questions])

    stored_by_question: dict[str, dict[str, Any]] = {
        str(entry.get("q")): entry for entry in (attempt.answers or []) if isinstance(entry, dict)
    }

    breakdown = []
    for question in questions:
        ordered = options.get(question.id, [])
        stored = stored_by_question.get(str(question.id), {})
        # Prefer the index stored at submit. Falling back to the live key is only
        # for papers sat before that index was stored.
        if "correct" in stored:
            correct_index = stored.get("correct")
        else:
            correct_index = next(
                (index for index, option in enumerate(ordered) if option.is_correct), None
            )
        chosen = stored.get("chosen")
        if correct_index is None:
            outcome = "PENDING_REVIEW"
        elif chosen is None:
            outcome = "UNATTEMPTED"
        elif chosen == correct_index:
            outcome = "CORRECT"
        else:
            outcome = "WRONG"

        breakdown.append(
            {
                "questionId": str(question.id),
                "text": question.text,
                "marks": question.marks,
                "difficulty": question.difficulty,
                "chosenOption": chosen,
                "correctOption": correct_index,
                "outcome": outcome,
                "explanation": question.explanation,
                "options": [
                    {
                        "label": option.label,
                        "text": option.text,
                        "isCorrect": option.is_correct,
                    }
                    for option in ordered
                ],
            }
        )

    # The score columns are the mark given at submit. The breakdown uses the option
    # index stored on the attempt when one was stored, so a later correction of the
    # live key does not change what this student was marked against. Papers sat
    # before that index was stored still fall back to the live key.
    return success(
        {
            "attemptId": str(attempt.id),
            "mockTestId": str(mock.id),
            "title": mock.title,
            "kind": mock.kind,
            "status": attempt.status,
            "submittedAt": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
            "autoSubmitted": attempt.auto_submitted,
            "timeTakenSeconds": attempt.time_taken_seconds,
            "score": attempt.score,
            "maxScore": attempt.max_score,
            "correct": attempt.correct_count,
            "wrong": attempt.wrong_count,
            "unattempted": attempt.unattempted_count,
            "pendingReview": attempt.pending_review_count,
            "questions": breakdown,
        },
        request_id=get_request_id(),
    )
