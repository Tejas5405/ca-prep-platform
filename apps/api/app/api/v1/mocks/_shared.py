"""Mock exam endpoints - blueprint v3 §7.3 and §18.1."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.progress import MockAttempt
from app.models.question import Question, QuestionOption

router = APIRouter(tags=["mocks"])

"""Helpers used by more than one sub-module. Unchanged; only relocated."""


logger = logging.getLogger(__name__)


async def _options_by_question(
    session: AsyncSession, question_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[QuestionOption]]:
    """Options for a set of questions, in presentation order.

    A `sequence` column exists so that the order is stable across renders; the
    label is a fallback for rows seeded before it, because an exam paper whose
    options shuffle between the question screen and the report is a bug students
    notice immediately.
    """
    if not question_ids:
        return {}
    rows = await session.execute(
        select(QuestionOption)
        .where(QuestionOption.question_id.in_(question_ids))
        .order_by(QuestionOption.sequence, QuestionOption.label)
    )
    grouped: dict[uuid.UUID, list[QuestionOption]] = {}
    for option in rows.scalars().all():
        grouped.setdefault(option.question_id, []).append(option)
    return grouped


def _paper_questions(
    questions: list[Question], options: dict[uuid.UUID, list[QuestionOption]]
) -> list[dict[str, Any]]:
    """A paper as the CLIENT may see it: no answer key, no explanation.

    The distinction matters even though the same data is revealed after
    submission: a paper sent with `isCorrect` on each option is a paper whose
    answers are in the browser's memory, and a student who opens devtools has the
    key to a timed exam. After submitting, the correct answer is in the response on
    purpose - that is the teaching moment.
    """
    return [
        {
            "id": str(question.id),
            "text": question.text,
            "questionType": question.question_type,
            "difficulty": question.difficulty,
            "marks": question.marks,
            "negativeMarks": float(question.negative_marks or 0),
            "subjectId": str(question.subject_id) if question.subject_id else None,
            "chapterId": str(question.chapter_id) if question.chapter_id else None,
            "options": [
                {"label": option.label, "text": option.text}
                for option in options.get(question.id, [])
            ],
        }
        for question in questions
    ]


def _attempt_payload(attempt: MockAttempt, mock: Any) -> dict[str, Any]:
    return {
        "attemptId": str(attempt.id),
        "mockTestId": str(mock.id),
        "title": mock.title,
        "kind": mock.kind,
        "status": attempt.status,
        "startedAt": attempt.started_at.isoformat(),
        "expiresAt": attempt.expires_at.isoformat(),
        "durationMin": mock.duration_min,
        "totalMarks": mock.total_marks,
        "autoSubmitted": attempt.auto_submitted,
        "score": attempt.score,
        "maxScore": attempt.max_score,
    }
