"""The admin badge award must actually write points. (Regression.)

Proven broken before the fix, against this same database: `admin.py` awards a
badge with the SCHEMA enum's `BADGE_AWARDED`, which was absent from
`_AWARDABLE`, so `_award` returned 0 and wrote nothing. The badge was granted,
the request succeeded, and the points never arrived - with no error anywhere.

These tests assert a ROW, not a return value, because the return value is exactly
what lied.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.enums import PointsReason as SchemaReason
from app.models.progress import PointsLedger
from app.repositories.progress import SqlProgressRepository, UnpersistablePointsReason
from app.services.gamification import POINTS
from app.services.gamification import PointsReason as EventReason

pytestmark = pytest.mark.postgres


def _harness():
    """The fixtures neighbouring integration tests already use.

    Imported lazily so a broken module fails inside the test rather than at
    collection, matching this directory's convention.
    """
    from tests.integration._db import run_in_database
    from tests.integration.test_postgres_content_library import make_user

    return run_in_database, make_user


async def _ledger(session):
    """Every (reason, points) currently in the ledger, as tuples."""
    result = await session.execute(select(PointsLedger.reason, PointsLedger.points))
    return sorted(result.tuples().all())


class TestTheAdminBadgeAwardPersists:
    def test_a_badge_award_writes_a_ledger_row(self, database_url: str) -> None:
        """The bug itself: BADGE_AWARDED now maps, so points are credited."""
        run_in_database, make_user = _harness()

        async def body(session) -> None:
            student = await make_user(session, "STUDENT")
            repo = SqlProgressRepository(session)

            # EXACTLY the call admin.py:1083 makes.
            awarded = await repo.award(
                user_id=student.id,
                reason=SchemaReason.BADGE_AWARDED,
                reference_id="badge-1",
                amount=25,  # the badge's configured points_reward
            )
            await session.commit()

            rows = await _ledger(session)
            assert awarded == 25, f"returned {awarded}; the silent no-op returned 0"
            assert rows == [("BADGE_AWARDED", 25)], rows

        run_in_database(database_url, body)

    def test_the_amount_comes_from_the_badge_not_a_constant(self, database_url: str) -> None:
        """Two badges with different configured rewards must credit differently."""
        run_in_database, make_user = _harness()

        async def body(session) -> None:
            student = await make_user(session, "STUDENT")
            repo = SqlProgressRepository(session)
            for reward in (10, 90):
                await repo.award(
                    user_id=student.id,
                    reason=SchemaReason.BADGE_AWARDED,
                    reference_id=f"badge-{reward}",
                    amount=reward,
                )
            await session.commit()
            assert await _ledger(session) == [("BADGE_AWARDED", 10), ("BADGE_AWARDED", 90)]

        run_in_database(database_url, body)

    def test_admin_adjustment_persists(self, database_url: str) -> None:
        """Mapped for contract completeness; no caller exists in the app yet.

        The amount is POSITIVE. `ck_profile_points_non_negative` forbids a
        profile total below zero, so a negative adjustment cannot be applied to a
        brand-new profile - the ledger allows it, the aggregate does not. That is
        a separate product decision about how corrections are represented, and it
        is not what this milestone is testing.
        """
        run_in_database, make_user = _harness()

        async def body(session) -> None:
            student = await make_user(session, "STUDENT")
            repo = SqlProgressRepository(session)
            awarded = await repo.award(
                user_id=student.id,
                reason=SchemaReason.ADMIN_ADJUSTMENT,
                reference_id="corr-1",
                amount=15,
            )
            await session.commit()
            assert awarded == 15
            assert await _ledger(session) == [("ADMIN_ADJUSTMENT", 15)]

        run_in_database(database_url, body)


class TestUnmappedEventsNowFailLoudly:
    """Requirement 3, at the boundary. This is the behaviour change."""

    def test_an_unmapped_event_raises_instead_of_writing_nothing(self, database_url: str) -> None:
        run_in_database, make_user = _harness()

        async def body(session) -> None:
            student = await make_user(session, "STUDENT")
            repo = SqlProgressRepository(session)

            with pytest.raises(UnpersistablePointsReason) as caught:
                await repo.award(
                    user_id=student.id, reason=EventReason.DAILY_LOGIN, reference_id="d1"
                )
            assert "DAILY_LOGIN" in str(caught.value)
            assert await _ledger(session) == [], "a raised award must not have written a row"

        run_in_database(database_url, body)

    def test_a_reason_with_a_fixed_value_still_works(self, database_url: str) -> None:
        """The raise must not break any path that always mapped."""
        run_in_database, make_user = _harness()

        async def body(session) -> None:
            student = await make_user(session, "STUDENT")
            awarded = await SqlProgressRepository(session).award(
                user_id=student.id, reason=EventReason.QUESTION_CORRECT, reference_id="q1"
            )
            await session.commit()
            expected = POINTS[EventReason.QUESTION_CORRECT]
            assert awarded == expected
            assert await _ledger(session) == [("QUESTION_CORRECT", expected)]

        run_in_database(database_url, body)

    def test_a_reason_needing_an_explicit_amount_says_so(self, database_url: str) -> None:
        """BADGE_AWARDED has no fixed value, so omitting `amount` must explain itself."""
        run_in_database, make_user = _harness()

        async def body(session) -> None:
            student = await make_user(session, "STUDENT")
            with pytest.raises(UnpersistablePointsReason) as caught:
                await SqlProgressRepository(session).award(
                    user_id=student.id, reason=EventReason.BADGE_AWARDED, reference_id="b"
                )
            assert "amount" in str(caught.value)

        run_in_database(database_url, body)

        run_in_database(database_url, body)
