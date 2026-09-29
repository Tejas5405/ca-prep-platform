"""Progress bookkeeping: attempts, per-question state, daily activity, points.

WHAT THIS MODULE OWNS

One student answers one question. Four things must then be true, atomically:

  1. an immutable ``practice_attempts`` row, because analytics and dispute
     resolution read history, not a running total;
  2. an upserted ``user_question_progress`` row, because "have I seen this before"
     is asked on every question render;
  3. an upserted ``daily_activities`` row, because the streak is derived from days,
     and a day that is written twice would break it;
  4. points, awarded AT MOST ONCE per (user, reason, reference) - so re-answering a
     question correctly cannot farm the ledger.

All four happen in the caller's transaction. Nothing here commits; the router
decides where the transaction boundary is, and a half-applied answer (a stored
attempt with no progress row) is the kind of inconsistency that surfaces weeks
later as a wrong accuracy percentage.

IDEMPOTENT UPSERTS NEED UNIQUE INDEXES, and three of them did not exist. Migration
0005 adds them - ``user_question_progress (user_id, question_id)``,
``spaced_repetition_cards (user_id, question_id)`` and ``daily_activities
(user_id, activity_date)``. Without a unique index, ``ON CONFLICT`` has nothing to
conflict on and PostgreSQL rejects the statement; the alternative (SELECT then
INSERT) has a race that a student double-tapping "next" would win.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.progress import (
    DailyActivity,
    PointsLedger,
    PracticeAttempt,
    UserQuestionProgress,
)
from app.models.question import Question
from app.models.user import UserProfile
from app.repositories.practice import grade
from app.services import gamification
from app.services.gamification import LedgerEntry, PointsReason, StreakState
from app.services.mock_scoring import derive_focus_areas, derive_strong_areas

#: Ratio below which a chapter is worth revising, and above which it is a strength.
#: Taken from the mock-scoring service rather than re-typed, so the dashboard and
#: the post-mock analysis cannot disagree about what "weak" means.
WEAK_ACCURACY = 0.6
STRONG_ACCURACY = 0.8
MIN_ATTEMPTS_FOR_VERDICT = 5


class UnpersistablePointsReason(RuntimeError):
    """An award was requested that has no persisted ledger reason.

    Raised rather than silently ignored. A points award that quietly writes
    nothing is worse than one that fails: the user is told the badge was granted,
    the badge is granted, and the points simply never arrive - with nothing in any
    log to say so. That is the exact failure this exception exists to prevent.
    """


#: Points ledgers are append-only, so the reason vocabulary is a database CHECK
#: (`ck_ledger_reason`, 11 values). That constraint - not this mapping - is the
#: persisted contract, and it is what `app/models/enums.py` mirrors exactly.
#:
#: The service enum is a different thing: an EVENT vocabulary. It has members the
#: database has never heard of (`DAILY_LOGIN`, the four `DOUBT_*` events,
#: `REFERRAL_ACTIVATED`, `QUESTION_ATTEMPTED`) and lacks members the database
#: requires. Only 1 of its 11 values (`QUESTION_CORRECT`) is also a persisted
#: reason, so the two enums are NOT two versions of one list - they are two
#: vocabularies that share a name, which is why this mapping is explicit.
#:
#: CONSEQUENCE, and the bug this fixes: a reason missing from here does not raise.
#: `_award` returned 0 and wrote nothing, so `admin.py` awarding a badge with the
#: SCHEMA enum's `BADGE_AWARDED` silently credited zero points - proven against a
#: real database, with no error and no failing test. Adding `BADGE_AWARDED` and
#: `ADMIN_ADJUSTMENT` below closes that, and the regression test asserts a written
#: row rather than a return value.
#:
#: The seven service-only events stay OUT deliberately. Persisting them would be a
#: schema change, and inventing DB values to make two enums overlap would trade a
#: behavioural bug for an unnecessary migration. If any of them ever needs to be
#: persisted, that is a separate product decision with its own migration.
_AWARDABLE: dict[PointsReason, str] = {
    PointsReason.QUESTION_CORRECT: "QUESTION_CORRECT",
    PointsReason.MOCK_COMPLETED: "MOCK_COMPLETE",
    PointsReason.STREAK_7: "STREAK_MILESTONE",
    PointsReason.DAILY_CHALLENGE_CORRECT: "DAILY_CHALLENGE",
    # Awarded by admin.py when a MANAGER grants a badge. The amount is the badge's
    # configured `points_reward`, not a constant, so it arrives as `amount=` and
    # bypasses the POINTS table.
    PointsReason.BADGE_AWARDED: "BADGE_AWARDED",
    # Verified 2026-09-28: present in the DB CHECK and in models/enums.py, but with
    # NO reachable write path anywhere in the app - no admin points-adjust endpoint
    # exists. Mapped so the contract is complete and an eventual caller cannot
    # silently no-op; adding the caller is out of scope here.
    PointsReason.ADMIN_ADJUSTMENT: "ADMIN_ADJUSTMENT",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AnswerOutcome:
    """What the student is told immediately after answering."""

    is_correct: bool | None
    correct_answer: str | None
    explanation: str | None
    points_awarded: int
    attempts_count: int
    correct_count: int
    accuracy: float | None
    current_streak: int


@dataclass(frozen=True)
class ChapterStat:
    chapter_id: uuid.UUID
    chapter_name: str
    subject_name: str
    attempted: int
    correct: int
    #: The chapter's syllabus weight. Carried here because the focus ranking is
    #: BY WEIGHTAGE - a student with limited time should revise the heavy chapter
    #: they are weak at, not the first weak chapter alphabetically. Without it the
    #: ranking silently fell back to the dataclass default of 0 for every chapter,
    #: which makes the sort order arbitrary while looking deliberate.
    weightage: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.attempted if self.attempted else 0.0


class SqlProgressRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    # ------------------------------------------------------------------ writing

    async def record_answer(
        self,
        *,
        user_id: uuid.UUID,
        question: Question,
        chosen_option: str | None,
        time_spent_seconds: int = 0,
        used_hint: bool = False,
        tz_offset_minutes: int = 330,
    ) -> AnswerOutcome:
        """Record one answer and return the immediate feedback."""
        now = utcnow()
        is_correct = grade(question, chosen_option)

        from app.services.question_history import current_version

        self._session.add(
            PracticeAttempt(
                user_id=user_id,
                question_id=question.id,
                chosen_option=(chosen_option or None),
                is_correct=is_correct,
                time_spent_seconds=max(0, int(time_spent_seconds)),
                used_hint=used_hint,
                question_version=await current_version(self._session, question.id),
                created_at=now,
                updated_at=now,
            )
        )

        # Unanswered (ungradeable) attempts still count as attempts, but a NULL
        # is_correct must not be counted as a wrong answer in the accuracy figure.
        attempts_delta = 1
        correct_delta = 1 if is_correct else 0
        progress = await self._upsert_progress(
            user_id=user_id,
            question_id=question.id,
            attempts_delta=attempts_delta,
            correct_delta=correct_delta,
            is_correct=is_correct,
            now=now,
        )

        # Denormalised counters on the question itself: a content manager needs
        # "which questions are statistically broken" without scanning attempts.
        await self._session.execute(
            update(Question)
            .where(Question.id == question.id)
            .values(
                times_attempted=Question.times_attempted + 1,
                times_correct=Question.times_correct + correct_delta,
            )
        )

        activity_date = (now + timedelta(minutes=tz_offset_minutes)).date()
        await self._upsert_daily_activity(
            user_id=user_id,
            activity_date=activity_date,
            questions=1,
            minutes=max(0, int(time_spent_seconds)) // 60,
            points=0,
        )

        points = 0
        if is_correct:
            # The PUBLIC award path, not ``_award``: it credits the day's activity
            # row as well as the ledger. Calling the private one here silently lost
            # the daily points total - the ledger said 10 points and the streak
            # chart said 0 - until this ran against a real database.
            points = await self.award(
                user_id=user_id,
                reason=PointsReason.QUESTION_CORRECT,
                reference_id=str(question.id),
                activity_date=activity_date,
            )

        streak = await self._refresh_streak(user_id=user_id, activity_date=activity_date, now=now)

        return AnswerOutcome(
            is_correct=is_correct,
            correct_answer=question.correct_answer,
            explanation=question.explanation,
            points_awarded=points,
            attempts_count=progress.attempts_count,
            correct_count=progress.correct_count,
            accuracy=(
                progress.correct_count / progress.attempts_count
                if progress.attempts_count
                else None
            ),
            current_streak=streak,
        )

    async def _upsert_progress(
        self,
        *,
        user_id: uuid.UUID,
        question_id: uuid.UUID,
        attempts_delta: int,
        correct_delta: int,
        is_correct: bool | None,
        now: datetime,
    ) -> UserQuestionProgress:
        stmt = (
            pg_insert(UserQuestionProgress)
            .values(
                user_id=user_id,
                question_id=question_id,
                attempts_count=attempts_delta,
                correct_count=correct_delta,
                last_attempted_at=now,
                last_is_correct=is_correct,
                is_marked_for_review=False,
                accuracy=correct_delta / attempts_delta,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "question_id"],
                set_={
                    "attempts_count": UserQuestionProgress.attempts_count + attempts_delta,
                    "correct_count": UserQuestionProgress.correct_count + correct_delta,
                    "last_attempted_at": now,
                    "last_is_correct": is_correct,
                    "updated_at": now,
                    # Recomputed in SQL from the ALREADY-UPDATED columns, so the
                    # value cannot drift from the counts if two requests interleave.
                    "accuracy": (
                        (UserQuestionProgress.correct_count + correct_delta)
                        / (UserQuestionProgress.attempts_count + attempts_delta)
                    ),
                },
            )
            .returning(UserQuestionProgress)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        return row

    async def _upsert_daily_activity(
        self,
        *,
        user_id: uuid.UUID,
        activity_date: date,
        questions: int,
        minutes: int,
        points: int,
    ) -> None:
        now = utcnow()
        stmt = (
            pg_insert(DailyActivity)
            .values(
                user_id=user_id,
                activity_date=activity_date,
                questions_attempted=questions,
                minutes_studied=minutes,
                points_earned=points,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "activity_date"],
                set_={
                    "questions_attempted": DailyActivity.questions_attempted + questions,
                    "minutes_studied": DailyActivity.minutes_studied + minutes,
                    "points_earned": DailyActivity.points_earned + points,
                    "updated_at": now,
                },
            )
        )
        await self._session.execute(stmt)

    async def _award(
        self,
        *,
        user_id: uuid.UUID,
        reason: PointsReason,
        reference_id: str | None = None,
        amount: int | None = None,
    ) -> int:
        """Write a ledger row, or return 0 if this award already exists.

        The unique index on ``idempotency_key`` is the arbiter, not the read: two
        concurrent submissions both read "no such key" and only one insert
        survives. The pure ``gamification.award`` check is used first so the common
        case costs no write.
        """
        key = gamification.ledger_key(
            LedgerEntry(user_id=str(user_id), reason=reason, ref_id=reference_id)
        )
        existing = await self._session.execute(
            select(PointsLedger.id).where(PointsLedger.idempotency_key == key).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return 0

        db_reason = _AWARDABLE.get(reason)
        if db_reason is None:
            # LOUD, deliberately. This used to `return 0`, which meant a caller
            # asking for an unmapped reason got a silent no-op: no row, no error,
            # no log. That is precisely how the admin badge award came to credit
            # zero points while appearing to succeed.
            #
            # Raising is right because every current caller maps cleanly, so this
            # can only be reached by a NEW event someone forgot to translate - and
            # at that point the developer needs to be told, not left debugging a
            # points total that is quietly short. A 0 here would read as "already
            # awarded", which is a different and false statement.
            raise UnpersistablePointsReason(
                f"{getattr(reason, 'value', reason)!r} is not mapped to a persisted "
                f"ledger reason. Mapped: {sorted(_AWARDABLE)}. Adding a value here "
                f"means choosing a reason the ck_ledger_reason CHECK accepts, which "
                f"is a schema decision - not a code one."
            )

        # `POINTS[...]` would raise a bare KeyError for BADGE_AWARDED and
        # ADMIN_ADJUSTMENT, whose value is configured per award rather than fixed.
        # Both current callers pass `amount=`, so this is unreachable today - but a
        # future caller that forgets it should be told which field is missing, not
        # handed a KeyError about a dict.
        if amount is not None:
            points = amount
        elif reason in gamification.POINTS:
            points = gamification.POINTS[reason]
        else:
            raise UnpersistablePointsReason(
                f"{getattr(reason, 'value', reason)!r} has no fixed value in "
                f"gamification.POINTS, so the award must pass an explicit `amount`. "
                f"Known fixed values: {sorted(gamification.POINTS)}"
            )
        await self._session.execute(
            pg_insert(PointsLedger)
            .values(
                user_id=user_id,
                points=points,
                reason=db_reason,
                reference_id=reference_id,
                idempotency_key=key,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            # DO NOTHING, not DO UPDATE: a duplicate award must be a no-op, and
            # RETURNING tells the caller whether a row was actually inserted.
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        return points

    async def _refresh_streak(
        self, *, user_id: uuid.UUID, activity_date: date, now: datetime
    ) -> int:
        """Advance the streak for today's activity, awarding milestones once."""
        previous = await self._session.execute(
            select(DailyActivity.activity_date)
            .where(
                DailyActivity.user_id == user_id,
                DailyActivity.activity_date < activity_date,
            )
            .order_by(DailyActivity.activity_date.desc())
            .limit(1)
        )
        last_active = previous.scalar_one_or_none()

        profile = await self._profile(user_id, now=now)
        state = gamification.apply_activity(
            StreakState(
                current=profile.current_streak,
                longest=profile.longest_streak,
                last_active_at=last_active,
            ),
            activity_date,
        )
        if state.current != profile.current_streak or state.longest != profile.longest_streak:
            profile.current_streak = state.current
            profile.longest_streak = state.longest
            profile.updated_at = now

            # A milestone is once per streak length, so the ledger key is the
            # length - not the date, which would pay out again tomorrow.
            if state.current == 7:
                await self._award(
                    user_id=user_id,
                    reason=PointsReason.STREAK_7,
                    reference_id=f"streak-7-{activity_date.isoformat()}",
                )
        return state.current

    async def _profile(self, user_id: uuid.UUID, *, now: datetime) -> UserProfile:
        profile = (
            await self._session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(user_id=user_id, created_at=now, updated_at=now)
            self._session.add(profile)
            await self._session.flush()
        return profile

    async def award(
        self,
        *,
        user_id: uuid.UUID,
        reason: PointsReason,
        reference_id: str | None = None,
        activity_date: date | None = None,
        points_to_daily: bool = True,
        amount: int | None = None,
    ) -> int:
        """Public award path, used by mocks and (later) doubts and referrals.

        ``amount`` is for awards whose value is CONFIGURED rather than fixed - a badge
        carries its own ``points_reward`` set by an admin. When it is None the fixed
        table in ``services/gamification`` decides, which is what every existing
        caller wants and why this parameter is last.
        """
        points = await self._award(
            user_id=user_id, reason=reason, reference_id=reference_id, amount=amount
        )
        if points and points_to_daily:
            await self._upsert_daily_activity(
                user_id=user_id,
                activity_date=activity_date or utcnow().date(),
                questions=0,
                minutes=0,
                points=points,
            )
        await self._sync_totals(user_id)
        return points

    async def _sync_totals(self, user_id: uuid.UUID) -> None:
        """Recompute the cached totals on ``user_profiles``.

        RECOMPUTED, not incremented. The ledger is the single source of truth, and
        an incremented cache is a cache that will eventually disagree with it - at
        which point nobody can tell which one is right.
        """
        total = (
            await self._session.execute(
                select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
                    PointsLedger.user_id == user_id
                )
            )
        ).scalar_one()
        profile = await self._profile(user_id, now=utcnow())
        profile.total_points = int(total)
        # The RANK, not the name: the column is a SmallInteger.
        profile.current_level = gamification.level_rank(int(total))
        profile.updated_at = utcnow()

    # ------------------------------------------------------------------ reading

    async def totals(self, user_id: uuid.UUID) -> dict[str, int | float | None]:
        row = (
            await self._session.execute(
                select(
                    func.count(PracticeAttempt.id).label("attempts"),
                    func.count(PracticeAttempt.id)
                    .filter(PracticeAttempt.is_correct.is_(True))
                    .label("correct"),
                    func.count(PracticeAttempt.id)
                    .filter(PracticeAttempt.is_correct.is_(None))
                    .label("pending"),
                ).where(PracticeAttempt.user_id == user_id)
            )
        ).one()
        attempts = int(row.attempts)
        correct = int(row.correct)
        graded = attempts - int(row.pending)
        return {
            "attempts": attempts,
            "correct": correct,
            "pendingReview": int(row.pending),
            # Denominator is GRADED attempts: counting an ungraded descriptive
            # answer as wrong would make the accuracy figure a lie.
            "accuracy": (correct / graded) if graded else None,
        }

    async def subject_breakdown(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        from app.models.curriculum import Subject

        stmt = (
            select(
                Subject.id,
                Subject.name,
                func.count(PracticeAttempt.id).label("attempted"),
                func.count(PracticeAttempt.id)
                .filter(PracticeAttempt.is_correct.is_(True))
                .label("correct"),
            )
            .join(Question, Question.id == PracticeAttempt.question_id)
            .join(Subject, Subject.id == Question.subject_id)
            .where(PracticeAttempt.user_id == user_id)
            .group_by(Subject.id, Subject.name)
            .order_by(Subject.name)
        )
        return [
            {
                "subjectId": str(row.id),
                "name": row.name,
                "attempted": int(row.attempted),
                "correct": int(row.correct),
                "accuracy": (int(row.correct) / int(row.attempted)) if row.attempted else None,
            }
            for row in (await self._session.execute(stmt)).all()
        ]

    async def chapter_stats(self, user_id: uuid.UUID) -> list[ChapterStat]:
        from app.models.curriculum import Chapter, Subject

        stmt: Select[Any] = (
            select(
                Chapter.id,
                Chapter.name,
                Subject.name.label("subject_name"),
                Chapter.weightage,
                func.count(PracticeAttempt.id).label("attempted"),
                func.count(PracticeAttempt.id)
                .filter(PracticeAttempt.is_correct.is_(True))
                .label("correct"),
            )
            .join(Question, Question.id == PracticeAttempt.question_id)
            .join(Chapter, Chapter.id == Question.chapter_id)
            .join(Subject, Subject.id == Question.subject_id)
            .where(PracticeAttempt.user_id == user_id)
            .group_by(Chapter.id, Chapter.name, Subject.name, Chapter.weightage)
            .order_by(func.count(PracticeAttempt.id).desc())
        )
        return [
            ChapterStat(
                chapter_id=row.id,
                chapter_name=row.name,
                subject_name=row.subject_name,
                attempted=int(row.attempted),
                correct=int(row.correct),
                weightage=int(row.weightage or 0),
            )
            for row in (await self._session.execute(stmt)).all()
        ]

    @staticmethod
    def focus_and_strong(
        stats: list[ChapterStat],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split chapters into "revise this" and "you have this".

        The verdicts come from the mock-scoring service, which owns the thresholds,
        so a chapter cannot be weak on one screen and strong on another.
        """
        from app.services.mock_scoring import ChapterPerformance

        performances = [
            ChapterPerformance(
                # The fields this dataclass actually declares. It was being built
                # with chapter_name= and correct=, which raised TypeError the first
                # time /progress/overview ran against real data - and accuracy, the
                # field the verdicts are computed FROM, was never passed at all.
                chapter_id=str(stat.chapter_id),
                accuracy=stat.accuracy,
                attempted=stat.attempted,
                weightage=stat.weightage,
            )
            for stat in stats
        ]
        by_id = {str(stat.chapter_id): stat for stat in stats}
        focus_ids = derive_focus_areas(
            performances,
            min_attempts=MIN_ATTEMPTS_FOR_VERDICT,
            accuracy_threshold=WEAK_ACCURACY,
        )
        strong_ids = derive_strong_areas(
            performances,
            min_attempts=MIN_ATTEMPTS_FOR_VERDICT,
            accuracy_threshold=STRONG_ACCURACY,
        )

        def _render(ids: list[str]) -> list[dict[str, Any]]:
            out = []
            for chapter_id in ids:
                stat = by_id.get(chapter_id)
                if stat is None:  # pragma: no cover - ids come from the same list
                    continue
                out.append(
                    {
                        "chapterId": chapter_id,
                        "chapterName": stat.chapter_name,
                        "subjectName": stat.subject_name,
                        "attempted": stat.attempted,
                        "accuracy": stat.accuracy,
                    }
                )
            return out

        return _render(focus_ids), _render(strong_ids)

    async def recent_activity(self, user_id: uuid.UUID, *, days: int = 14) -> list[dict[str, Any]]:
        stmt = (
            select(DailyActivity)
            .where(DailyActivity.user_id == user_id)
            .order_by(DailyActivity.activity_date.desc())
            .limit(days)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        # Oldest first: a chart draws left to right, and reversing in the client is
        # how a streak chart ends up mirrored.
        rows.reverse()
        return [
            {
                "date": row.activity_date.isoformat(),
                "questionsAttempted": row.questions_attempted,
                "minutesStudied": row.minutes_studied,
                "pointsEarned": row.points_earned,
            }
            for row in rows
        ]

    async def profile_snapshot(self, user_id: uuid.UUID) -> dict[str, Any]:
        profile = (
            await self._session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
        points = int(profile.total_points) if profile else 0
        level = gamification.level_progress(points)
        return {
            "totalPoints": points,
            "currentStreak": profile.current_streak if profile else 0,
            "longestStreak": profile.longest_streak if profile else 0,
            "level": level.level,
            "nextLevel": level.next_level,
            "pointsToNextLevel": level.points_to_next,
            "levelProgress": level.progress,
            "overallAccuracy": float(profile.overall_accuracy)
            if profile and profile.overall_accuracy is not None
            else None,
        }

    async def refresh_accuracy(self, user_id: uuid.UUID) -> None:
        """Cache overall accuracy on the profile, recomputed from attempts."""
        totals = await self.totals(user_id)
        accuracy = totals["accuracy"]
        profile = await self._profile(user_id, now=utcnow())
        profile.overall_accuracy = None if accuracy is None else round(float(accuracy), 3)
        profile.updated_at = utcnow()

    async def mark_for_review(
        self, *, user_id: uuid.UUID, question_id: uuid.UUID, marked: bool
    ) -> bool:
        """Toggle the bookmark flag. Returns False when there is no progress row.

        No row means the student has never seen the question, and creating one here
        would report "0 of 1 correct" for a question they have not answered.
        """
        result = await self._session.execute(
            update(UserQuestionProgress)
            .where(
                UserQuestionProgress.user_id == user_id,
                UserQuestionProgress.question_id == question_id,
            )
            .values(is_marked_for_review=marked, updated_at=utcnow())
        )
        return bool(result.rowcount)
