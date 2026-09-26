"""Collections, filter validation and LDR migration.

The filter validator is a security boundary, so it is tested adversarially:
unknown keys, object values (operator injection), arrays, oversized strings and
enum escapes must all be rejected rather than silently dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import pytest

from app.services.collections import (
    FREE_TIER_MAX_COLLECTIONS,
    CollectionFilters,
    FilterValidationError,
    LdrRow,
    canonical_filters,
    check_collection_quota,
    seed_ldr_collection,
    validate_filters,
)


class TestFilterValidationBoundary:
    def test_accepts_a_well_formed_filter_set(self):
        f = validate_filters(
            {
                "subject_id": "sub_1",
                "difficulty": "HARD",
                "user_accuracy": "wrong",
                "is_historical": True,
            }
        )
        assert f.subject_id == "sub_1"
        assert f.difficulty == "HARD"
        assert f.is_historical is True

    def test_rejects_unknown_keys(self):
        # Pydantic ignores extras by default; extra="forbid" turns this into a
        # hard failure instead of a silent accept.
        with pytest.raises(FilterValidationError):
            validate_filters({"password_hash": "x"})
        with pytest.raises(FilterValidationError):
            validate_filters({"internal_notes": "x"})

    def test_rejects_object_values_that_could_become_sql_operators(self):
        with pytest.raises(FilterValidationError):
            validate_filters({"subject_id": {"ne": None}})
        with pytest.raises(FilterValidationError):
            validate_filters({"subject_id": {"$ne": None}})

    def test_rejects_arrays(self):
        with pytest.raises(FilterValidationError):
            validate_filters({"subject_id": ["a", "b"]})

    def test_rejects_enum_escapes(self):
        with pytest.raises(FilterValidationError):
            validate_filters({"difficulty": "IMPOSSIBLE"})
        with pytest.raises(FilterValidationError):
            validate_filters({"syllabus_scheme": "NEW_2025"})

    def test_rejects_oversized_strings(self):
        with pytest.raises(FilterValidationError):
            validate_filters({"subject_id": "x" * 65})

    def test_rejects_blank_strings(self):
        with pytest.raises(FilterValidationError):
            validate_filters({"subject_id": "   "})

    def test_rejects_non_boolean_is_historical(self):
        with pytest.raises(FilterValidationError):
            validate_filters({"is_historical": "yes"})

    def test_rejects_a_non_object_payload(self):
        with pytest.raises(FilterValidationError):
            validate_filters("subject_id=1")
        with pytest.raises(FilterValidationError):
            validate_filters([1, 2])

    def test_treats_none_as_empty(self):
        assert validate_filters(None) == CollectionFilters()

    def test_error_carries_the_offending_field(self):
        with pytest.raises(FilterValidationError) as exc:
            validate_filters({"difficulty": "NOPE"})
        assert exc.value.field_name is not None

    def test_accepts_every_documented_key(self):
        all_keys = {
            "subject_id": "s",
            "chapter_id": "c",
            "difficulty": "EASY",
            "user_accuracy": "correct",
            "weightage": "HIGH",
            "syllabus_scheme": "NEW_2024",
            "finance_act_year": "AY 2025-26",
            "is_historical": False,
        }
        assert validate_filters(all_keys).subject_id == "s"


class TestCanonicalFilters:
    def test_is_order_independent_for_stable_cache_keys(self):
        a = CollectionFilters(subject_id="s", difficulty="EASY")
        b = CollectionFilters(difficulty="EASY", subject_id="s")
        assert canonical_filters(a) == canonical_filters(b)

    def test_distinguishes_different_filter_sets(self):
        assert canonical_filters(CollectionFilters(difficulty="EASY")) != canonical_filters(
            CollectionFilters(difficulty="HARD")
        )


class TestLdrMigration:
    ROWS: ClassVar[list] = [
        LdrRow("u1", "q2", datetime(2026, 9, 20, 10, tzinfo=UTC), None),
        LdrRow("u1", "q1", datetime(2026, 9, 18, 10, tzinfo=UTC), "revise"),
        LdrRow("u1", "q2", datetime(2026, 9, 22, 10, tzinfo=UTC), "again"),
    ]

    def test_collapses_duplicate_marks(self):
        seeded = seed_ldr_collection(self.ROWS)
        assert len(seeded.items) == 2
        assert sorted(i.question_id for i in seeded.items) == ["q1", "q2"]

    def test_keeps_earliest_date_and_merges_a_later_note(self):
        seeded = seed_ldr_collection(self.ROWS)
        q2 = next(i for i in seeded.items if i.question_id == "q2")
        assert q2.added_at == datetime(2026, 9, 20, 10, tzinfo=UTC)
        assert q2.note == "again"

    def test_does_not_overwrite_a_note_with_none(self):
        seeded = seed_ldr_collection(
            [
                LdrRow("u1", "q1", datetime(2026, 9, 1, tzinfo=UTC), "keep me"),
                LdrRow("u1", "q1", datetime(2026, 9, 2, tzinfo=UTC), None),
            ]
        )
        assert seeded.items[0].note == "keep me"

    def test_is_idempotent(self):
        a = seed_ldr_collection(self.ROWS)
        b = seed_ldr_collection(self.ROWS)
        assert a == b

    def test_is_marked_as_a_system_collection(self):
        seeded = seed_ldr_collection(self.ROWS)
        assert seeded.is_system is True
        assert seeded.name == "Marked for later resolution"

    def test_handles_a_user_with_no_ldr_rows(self):
        assert seed_ldr_collection([]).items == []


class TestQuota:
    def test_premium_is_unrestricted(self):
        assert check_collection_quota("PREMIUM", 999, 99_999, 500).allowed is True

    def test_blocks_a_sixth_free_collection(self):
        r = check_collection_quota("FREE", FREE_TIER_MAX_COLLECTIONS, 10, 1)
        assert r.allowed is False
        assert r.reason == "COLLECTION_LIMIT"
        assert r.upgrade_required is True

    def test_allows_the_fifth_free_collection(self):
        assert check_collection_quota("FREE", 4, 10, 1).allowed is True

    def test_blocks_exceeding_the_question_cap(self):
        r = check_collection_quota("FREE", 1, 195, 10)
        assert r.allowed is False
        assert r.reason == "QUESTION_LIMIT"

    def test_allows_filling_exactly_to_the_cap(self):
        assert check_collection_quota("FREE", 1, 190, 10).allowed is True

    def test_is_case_insensitive_on_tier(self):
        assert check_collection_quota("premium", 999, 99_999, 1).allowed is True
