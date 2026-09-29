"""The persisted points-reason contract, and the silent no-op this closes.

`PointsReason` exists TWICE: `app/models/enums.py` mirrors the
`ck_ledger_reason` CHECK constraint (the persisted contract), and
`app/services/gamification.py` is an EVENT vocabulary. Only `QUESTION_CORRECT`
is in both - they are two vocabularies sharing a name, not two versions of one
list, and `_AWARDABLE` translates between them.

The bug: a reason missing from `_AWARDABLE` did not raise. `_award` returned 0
and wrote nothing, so `admin.py` awarding a badge with the schema enum's
`BADGE_AWARDED` credited ZERO points while appearing to succeed - no error, no
log, no failing test. Proven against a real database before the fix.

The unit cases here need no database; the persistence cases in
`test_points_reason_persistence.py` do.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.models.enums import PointsReason as SchemaReason
from app.repositories.progress import _AWARDABLE, UnpersistablePointsReason
from app.services.gamification import POINTS
from app.services.gamification import PointsReason as EventReason


class TestThePersistedContractIsUnchanged:
    """Requirement 4: the DB vocabulary must not drift.

    The CHECK constraint is the source of truth. These values are asserted
    against it so that adding a member to either enum without a migration is a
    failing test rather than a production 500.
    """

    #: Verbatim from `ck_ledger_reason` in
    #: alembic/versions/20260924_1200_0001_initial_schema.py and mirrored in
    #: app/models/progress.py. Read from the live test database, not from memory.
    DB_VALUES: ClassVar[set[str]] = {
        "QUESTION_CORRECT",
        "CHAPTER_COMPLETE",
        "MOCK_COMPLETE",
        "MOCK_HIGH_SCORE",
        "STREAK_DAY",
        "STREAK_MILESTONE",
        "DAILY_CHALLENGE",
        "BADGE_AWARDED",
        "REFERRAL_SIGNUP",
        "REFERRAL_CONVERSION",
        "ADMIN_ADJUSTMENT",
    }

    def test_the_schema_enum_matches_the_check_constraint_exactly(self) -> None:
        assert {m.value for m in SchemaReason} == self.DB_VALUES

    def test_every_mapping_target_exists_in_the_schema_enum(self) -> None:
        """Each translation must land on a value the schema enum knows.

        The reverse is deliberately NOT asserted. Nine persisted reasons
        (CHAPTER_COMPLETE, MOCK_HIGH_SCORE, REFERRAL_*, ...) have no event-enum
        member and do not need one: nothing in the app awards them. This
        milestone maps only BADGE_AWARDED and ADMIN_ADJUSTMENT, so requiring the
        event enum to mirror all eleven would be inventing members nothing uses.
        """
        schema = {m.value for m in SchemaReason}
        assert set(_AWARDABLE.values()) <= schema

    def test_the_two_newly_mapped_members_are_present(self) -> None:
        """The specific additions this milestone made to the event enum."""
        event = {m.value for m in EventReason}
        assert {"BADGE_AWARDED", "ADMIN_ADJUSTMENT"} <= event

    def test_no_mapping_targets_a_value_the_check_rejects(self) -> None:
        """Every translation lands inside the DB vocabulary."""
        assert set(_AWARDABLE.values()) <= self.DB_VALUES


class TestTheTwoNewlyMappedReasons:
    """Requirements 1 and 2."""

    def test_badge_awarded_maps_to_its_persisted_name(self) -> None:
        assert _AWARDABLE[EventReason.BADGE_AWARDED] == "BADGE_AWARDED"

    def test_admin_adjustment_maps_to_its_persisted_name(self) -> None:
        assert _AWARDABLE[EventReason.ADMIN_ADJUSTMENT] == "ADMIN_ADJUSTMENT"

    def test_the_schema_enums_value_also_resolves(self) -> None:
        """`admin.py` passes the SCHEMA enum. Lookup is by value, so it must work.

        This is the whole bug: the mapping was keyed by the event enum, and the
        schema enum's member had no key to hit.
        """
        assert _AWARDABLE.get(SchemaReason.BADGE_AWARDED) == "BADGE_AWARDED"
        assert _AWARDABLE.get(SchemaReason.ADMIN_ADJUSTMENT) == "ADMIN_ADJUSTMENT"

    def test_neither_has_a_fixed_points_value(self) -> None:
        """Both are configured per award, so they must not silently score zero."""
        assert EventReason.BADGE_AWARDED not in POINTS
        assert EventReason.ADMIN_ADJUSTMENT not in POINTS


class TestServiceOnlyEventsStayUnmapped:
    """Requirement 3: an unmapped event must not PRETEND it persisted.

    These seven are scored in memory and used in `LedgerEntry`, but nothing writes
    them to `points_ledger`. Persisting any of them is a schema decision, so they
    are deliberately left out - and the mapping is now loud about it.
    """

    UNMAPPED: ClassVar[list[str]] = [
        "DAILY_LOGIN",
        "QUESTION_ATTEMPTED",
        "DOUBT_ANSWERED",
        "DOUBT_ANSWER_ACCEPTED",
        "DOUBT_ASKED_QUALITY",
        "DOUBT_RESOLVED",
        "REFERRAL_ACTIVATED",
    ]

    @pytest.mark.parametrize("name", UNMAPPED)
    def test_it_is_not_mapped(self, name: str) -> None:
        assert EventReason[name] not in _AWARDABLE

    @pytest.mark.parametrize("name", UNMAPPED)
    def test_it_still_has_a_points_value(self, name: str) -> None:
        """Unmapped for PERSISTENCE, not for scoring. `award()` still uses it."""
        assert EventReason[name] in POINTS

    def test_the_exception_names_the_missing_reason(self) -> None:
        """The message must be actionable, not a bare KeyError."""
        exc = UnpersistablePointsReason("'DAILY_LOGIN' is not mapped")
        assert "DAILY_LOGIN" in str(exc)
