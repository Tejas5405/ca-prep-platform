"""Append-only snapshots of a question.

A published row can be corrected. The score a student already received cannot.
``question_versions`` is the copy of the question as it was. An attempt stores the
version number it was marked against, so a later edit does not rewrite that mark.

This module does not decide that a law has changed. An editor types the new text.
Nothing here invents an amendment.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.progress import PracticeAttempt
from app.models.question import Question, QuestionOption, QuestionVersion


async def option_rows(session: AsyncSession, question_id: uuid.UUID) -> list[QuestionOption]:
    rows = (
        await session.execute(
            select(QuestionOption)
            .where(QuestionOption.question_id == question_id)
            .order_by(QuestionOption.sequence, QuestionOption.label)
        )
    ).scalars().all()
    return list(rows)


def snapshot_of(question: Question, options: list[QuestionOption]) -> dict[str, Any]:
    """The fields a later dispute needs. Not the whole ORM row."""
    return {
        "text": question.text,
        "explanation": question.explanation,
        "correctAnswer": question.correct_answer,
        "modelAnswer": question.model_answer,
        "questionType": question.question_type,
        "difficulty": question.difficulty,
        "marks": question.marks,
        "negativeMarks": float(question.negative_marks or 0),
        "status": question.status,
        "isHistorical": question.is_historical,
        "financeActYear": question.finance_act_year,
        "disclaimerText": question.disclaimer_text,
        "verifiedBy": str(question.verified_by) if question.verified_by else None,
        "options": [
            {"label": option.label, "text": option.text, "isCorrect": option.is_correct}
            for option in options
        ],
    }


async def current_version(session: AsyncSession, question_id: uuid.UUID) -> int | None:
    value = await session.scalar(
        select(func.max(QuestionVersion.version_number)).where(
            QuestionVersion.question_id == question_id
        )
    )
    return int(value) if value is not None else None


async def record_version(
    session: AsyncSession,
    question: Question,
    *,
    actor_id: uuid.UUID | None,
    reason: str,
) -> int:
    """Append one snapshot. Does not commit."""
    number = (await current_version(session, question.id) or 0) + 1
    options = await option_rows(session, question.id)
    session.add(
        QuestionVersion(
            id=uuid.uuid4(),
            question_id=question.id,
            version_number=number,
            snapshot=snapshot_of(question, options),
            changed_by=actor_id,
            change_reason=reason[:500],
        )
    )
    await session.flush()
    return number


async def pin_unversioned_attempts(
    session: AsyncSession, question_id: uuid.UUID, version_number: int
) -> None:
    """Attempts taken before versions existed keep the key they were marked on.

    Only rows with a null version are updated. A row that already names a version
    is left alone, which is how a second correction does not rewrite the first.
    """
    await session.execute(
        update(PracticeAttempt)
        .where(
            PracticeAttempt.question_id == question_id,
            PracticeAttempt.question_version.is_(None),
        )
        .values(question_version=version_number)
    )


async def version_snapshot(
    session: AsyncSession, question_id: uuid.UUID, version_number: int
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(QuestionVersion.snapshot).where(
                QuestionVersion.question_id == question_id,
                QuestionVersion.version_number == version_number,
            )
        )
    ).scalar_one_or_none()
    return row if isinstance(row, dict) else None
