"""Spaced repetition over HTTP - blueprint v3 §18.1 (P1).

THE SCHEDULE IS THE SERVICE'S, NOT THE CLIENT'S. A client that could post its own
``next_review_at`` would let a student (or a script) keep a card due forever, or
retire it without ever seeing it again. So the only thing a client may send is a
GRADE, and the interval is computed from the server's own state.

Grades are 0-5, the SM-2 scale: 0-2 is a lapse and resets the interval, 3-5 is a
successful recall of increasing confidence. The mapping from "did they get it
right" to a grade happens on the answering screen, where the student can say "I
guessed" - a correct answer that felt like a guess is not a 5, and treating it as
one is how a revision queue quietly stops being useful.
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
from app.models.user import User
from app.repositories.practice import SqlQuestionRepository
from app.repositories.revision import SqlRevisionRepository
from app.schemas.base import StrictRequest, UuidRef

router = APIRouter(tags=["revision"])

MAX_CARDS = 50


class ReviewIn(StrictRequest):
    """A grade for one card."""

    question_id: UuidRef
    #: SM-2 quality. 0-2 = did not recall, 3-5 = recalled with increasing ease.
    quality: int = Field(ge=0, le=5)


@router.get("/revision/due", summary="Cards due now")
async def due_cards(
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    repo = SqlRevisionRepository(session)
    cards = await repo.due(user_id=user.id, limit=min(MAX_CARDS, max(1, limit)))

    questions = SqlQuestionRepository(session)
    options = await questions.options_for([question.id for _, question in cards])

    return success(
        {
            "cards": [
                {
                    "questionId": str(question.id),
                    "text": question.text,
                    "questionType": question.question_type,
                    "difficulty": question.difficulty,
                    "chapterId": str(question.chapter_id) if question.chapter_id else None,
                    "intervalDays": card.interval_days,
                    "repetitions": card.repetitions,
                    "easeFactor": float(card.ease_factor),
                    "nextReviewAt": card.next_review_at.isoformat(),
                    # Options are returned WITHOUT the answer, exactly as in
                    # practice: grading yourself still means answering first.
                    "options": [
                        {"label": option.label, "text": option.text}
                        for option in sorted(
                            options.get(question.id, []),
                            key=lambda item: (item.sequence, item.label),
                        )
                    ],
                }
                for card, question in cards
            ],
            "count": len(cards),
        },
        request_id=get_request_id(),
    )


@router.post("/revision/review", summary="Grade a card and reschedule it")
async def review_card(
    payload: ReviewIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Apply the grade, advance the interval, and return what happens next.

    ``nextReviewAt`` is returned so the screen can say "see this again in 6 days"
    - which is the only feedback that makes a spaced-repetition queue feel like a
    plan rather than a random pile.
    """
    repo = SqlRevisionRepository(session)
    result = await repo.apply_review(
        user_id=user.id, question_id=payload.question_id, quality=payload.quality
    )
    if result is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Card not queued",
            detail=(
                "This question is not in your revision queue. Answer it wrongly in "
                "practice, or bookmark it, to add it."
            ),
            type_slug="revision",
        )

    await session.commit()
    card = await _card_after(session, user_id=user.id, question_id=payload.question_id)
    return success(
        {
            "questionId": str(payload.question_id),
            "quality": payload.quality,
            "lapsed": result.lapsed,
            "intervalDays": result.interval_days,
            "repetitions": result.repetitions,
            "easeFactor": round(result.ease_factor, 2),
            "box": result.box,
            "nextReviewAt": card,
        },
        request_id=get_request_id(),
    )


async def _card_after(
    session: AsyncSession, *, user_id: uuid.UUID, question_id: uuid.UUID
) -> str | None:
    from sqlalchemy import select

    from app.models.progress import SpacedRepetitionCard

    value = (
        await session.execute(
            select(SpacedRepetitionCard.next_review_at).where(
                SpacedRepetitionCard.user_id == user_id,
                SpacedRepetitionCard.question_id == question_id,
            )
        )
    ).scalar_one_or_none()
    return value.isoformat() if value else None


@router.get("/revision/stats", summary="Queue health")
async def revision_stats(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    repo = SqlRevisionRepository(session)
    stats = await repo.stats(user_id=user.id)
    boxes = await repo.counts_by_box(user_id=user.id)
    return success(
        {
            **stats,
            # The Leitner boxes, by interval: a queue whose cards never leave box 1
            # is a queue the student is failing, and that is worth seeing.
            "boxes": {str(box): count for box, count in sorted(boxes.items())},
        },
        request_id=get_request_id(),
    )
