"""Spaced-repetition cards.

The scheduling maths lives in ``app.services.spaced_repetition`` as pure
functions; this module only persists the state they produce. Keeping the two apart
is why the scheduler has 24 tests and none of them need a database.

TWO CLOCKS, ON PURPOSE. ``next_review_at`` is computed from the student's local
day, not UTC, because "review this tomorrow" means tomorrow where they are. The
service takes a ``tz_offset_minutes`` for exactly this reason (IST = 330) and the
default here matches the platform's primary market.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.progress import SpacedRepetitionCard
from app.models.question import Question
from app.services import spaced_repetition as sr


class SqlRevisionRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def schedule(
        self,
        *,
        user_id: uuid.UUID,
        question_id: uuid.UUID,
        now: datetime | None = None,
        tz_offset_minutes: int = 330,
    ) -> SpacedRepetitionCard:
        """Add a question to the queue, or leave an existing card untouched.

        DUE NOW, NOT TOMORROW. A card is created because the student just got the
        question wrong or asked to come back to it, and the useful moment to see it
        again is the same sitting. Scheduling it for tomorrow morning - which
        ``next_review_at(moment, 1)`` did - means "review your mistakes" shows an
        empty queue to the student who wants to review their mistakes.

        The SM-2 interval takes over as soon as the card is GRADED: a card entering
        with ``repetitions=0`` and a quality-5 review comes back in a day, then six,
        and so on. So the schedule lives in one place (the review), and the queue
        holds exactly the cards that are outstanding.

        DO NOTHING rather than DO UPDATE: answering a question wrong must not reset
        a card that is already three successful reviews in. Destroying earned
        progress on a slip is how a spaced-repetition queue becomes a treadmill.
        """
        moment = now or datetime.now(UTC)
        initial = sr.initial_review_state()
        stmt = (
            pg_insert(SpacedRepetitionCard)
            .values(
                user_id=user_id,
                question_id=question_id,
                ease_factor=initial.ease_factor,
                interval_days=initial.interval_days,
                repetitions=initial.repetitions,
                # interval_days stays 0: the card is not on a schedule until it
                # is graded. `moment`, not a snapped 04:00, so it is due immediately.
                next_review_at=moment,
                created_at=moment,
                updated_at=moment,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "question_id"])
            .returning(SpacedRepetitionCard)
        )
        card = (await self._session.execute(stmt)).scalar_one_or_none()
        if card is not None:
            return card
        return (
            await self._session.execute(
                select(SpacedRepetitionCard).where(
                    SpacedRepetitionCard.user_id == user_id,
                    SpacedRepetitionCard.question_id == question_id,
                )
            )
        ).scalar_one()

    async def apply_review(
        self,
        *,
        user_id: uuid.UUID,
        question_id: uuid.UUID,
        quality: int,
        now: datetime | None = None,
        tz_offset_minutes: int = 330,
    ) -> sr.ReviewResult | None:
        """Grade a card and store the next interval. ``None`` if it is not queued."""
        moment = now or datetime.now(UTC)
        card = (
            await self._session.execute(
                select(SpacedRepetitionCard)
                .where(
                    SpacedRepetitionCard.user_id == user_id,
                    SpacedRepetitionCard.question_id == question_id,
                )
                # Locked: two grade taps in flight would otherwise both read the
                # old interval and one update would be lost.
                .with_for_update()
            )
        ).scalar_one_or_none()
        if card is None:
            return None

        result = sr.review(
            sr.ReviewState(
                ease_factor=float(card.ease_factor),
                interval_days=card.interval_days,
                repetitions=card.repetitions,
            ),
            quality,
        )
        card.ease_factor = round(result.ease_factor, 2)
        card.interval_days = result.interval_days
        card.repetitions = result.repetitions
        card.last_reviewed_at = moment
        card.next_review_at = sr.next_review_at(moment, result.interval_days, tz_offset_minutes)
        card.updated_at = moment
        await self._session.flush()
        return result

    async def due(
        self, *, user_id: uuid.UUID, limit: int = 20, now: datetime | None = None
    ) -> list[tuple[SpacedRepetitionCard, Question]]:
        moment = now or datetime.now(UTC)
        stmt = (
            select(SpacedRepetitionCard, Question)
            .join(Question, Question.id == SpacedRepetitionCard.question_id)
            .where(
                SpacedRepetitionCard.user_id == user_id,
                SpacedRepetitionCard.next_review_at <= moment,
                Question.status == "PUBLISHED",
                Question.deleted_at.is_(None),
            )
            # Oldest first: the most overdue card is the one most at risk of being
            # forgotten, and it should not be behind a card added a minute ago.
            .order_by(SpacedRepetitionCard.next_review_at)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in (await self._session.execute(stmt)).all()]

    async def stats(self, *, user_id: uuid.UUID, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        row = (
            await self._session.execute(
                select(
                    func.count(SpacedRepetitionCard.id).label("total"),
                    func.count(SpacedRepetitionCard.id)
                    .filter(SpacedRepetitionCard.next_review_at <= moment)
                    .label("due"),
                    func.count(SpacedRepetitionCard.id)
                    .filter(SpacedRepetitionCard.repetitions == 0)
                    .label("learning"),
                    func.count(SpacedRepetitionCard.id)
                    .filter(SpacedRepetitionCard.repetitions >= 3)
                    .label("mature"),
                    func.coalesce(func.avg(SpacedRepetitionCard.interval_days), 0).label(
                        "avg_interval"
                    ),
                ).where(SpacedRepetitionCard.user_id == user_id)
            )
        ).one()
        return {
            "total": int(row.total),
            "due": int(row.due),
            "learning": int(row.learning),
            "mature": int(row.mature),
            "averageIntervalDays": round(float(row.avg_interval), 1),
        }

    async def counts_by_box(self, *, user_id: uuid.UUID) -> dict[int, int]:
        """Leitner boxes, for the progress chart."""
        stmt = select(SpacedRepetitionCard.interval_days).where(
            SpacedRepetitionCard.user_id == user_id
        )
        boxes: dict[int, int] = {}
        for (interval,) in (await self._session.execute(stmt)).all():
            box = sr.leitner_box(interval)
            boxes[box] = boxes.get(box, 0) + 1
        return boxes
