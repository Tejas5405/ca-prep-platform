"""The test harness must refuse to empty a database it does not own.

WHY THIS EXISTS
===============

``tests/integration/_db.py`` truncates every table in the database it is given.
That is a destructive operation aimed at whatever name appears in
``TEST_DATABASE_URL``, and during the P1 milestone a concurrent agent in a
sibling checkout recreated the shared ``caprep_test`` database from a DIFFERENT
migration chain while this suite was running - 163 tests failed with
``relation "users" does not exist``.

A convention written only in a document is a convention that gets forgotten the
first time somebody is in a hurry. These tests make the rule executable: the
shared names are refused, this repository's own names are accepted, and the
refusal happens BEFORE any TRUNCATE is issued.

They are pure unit tests - no database is contacted, and nothing is destroyed.
"""

from __future__ import annotations

import pytest

from tests.integration._db import (
    OWNED_PREFIX,
    SHARED_NAMES,
    UnsafeDatabaseError,
    assert_disposable_database,
    database_name,
)


def _url(name: str) -> str:
    return f"postgresql://caprep:caprep@127.0.0.1:5432/{name}"


class TestDatabaseNameParsing:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (_url("caprep_v2_test"), "caprep_v2_test"),
            # Every driver spelling the repo actually uses must yield the name.
            ("postgresql+psycopg://u:p@h:5432/caprep_v2_test", "caprep_v2_test"),
            ("postgresql+asyncpg://u:p@h:5432/caprep_v2_test", "caprep_v2_test"),
            # A query string must not be mistaken for part of the name.
            (_url("caprep_v2_test") + "?sslmode=require", "caprep_v2_test"),
            # A trailing slash is a common paste artefact.
            (_url("caprep_v2_test") + "/", "caprep_v2_test"),
        ],
    )
    def test_the_name_is_read_correctly(self, url: str, expected: str) -> None:
        assert database_name(url) == expected


class TestSharedDatabasesAreRefused:
    """The exact accident that happened, now impossible."""

    @pytest.mark.parametrize(
        "name", ["caprep", "caprep_test", "postgres", "template0", "template1"]
    )
    def test_a_shared_or_generic_name_is_refused(self, name: str) -> None:
        with pytest.raises(UnsafeDatabaseError) as excinfo:
            assert_disposable_database(_url(name))
        # The message must EXPLAIN the convention, not just report a mismatch,
        # because the person seeing it is mid-incident and needs the rule.
        assert OWNED_PREFIX in str(excinfo.value)
        assert "ENVIRONMENT_ISOLATION" in str(excinfo.value)

    def test_caprep_test_is_refused_by_name_not_merely_by_prefix(self) -> None:
        """It is a plausible name; the refusal must be deliberate.

        ``caprep_test`` does not start with the owned prefix, so a prefix check
        alone would catch it - but it is called out separately in SHARED_NAMES so
        the error can explain that it is shared with other checkouts, which is
        the actual reason it is dangerous.
        """
        assert "caprep_test" in SHARED_NAMES
        with pytest.raises(UnsafeDatabaseError, match="shared"):
            assert_disposable_database(_url("caprep_test"))


class TestOwnedDatabasesAreAccepted:
    @pytest.mark.parametrize(
        "name", ["caprep_v2_test", "caprep_v2_rehearsal", "caprep_v2_staging_probe"]
    )
    def test_this_repositor_own_names_are_accepted(self, name: str) -> None:
        assert assert_disposable_database(_url(name)) == name

    def test_the_development_database_is_never_a_test_target(self) -> None:
        """``caprep`` holds seeded reference content; it must not be emptied."""
        with pytest.raises(UnsafeDatabaseError):
            assert_disposable_database(_url("caprep"))


class TestEscapeHatch:
    def test_an_explicit_override_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A deliberate override is allowed, and must be explicit.

        Someone running a genuinely single-purpose database they have confirmed is
        theirs should not be locked out by a convention; but the override has to be
        typed on purpose, so it can never happen by accident or by inheritance
        from a shell profile.
        """
        monkeypatch.setenv("CAPREP_ALLOW_SHARED_TEST_DB", "1")
        assert assert_disposable_database(_url("caprep_test")) == "caprep_test"

    def test_the_override_is_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CAPREP_ALLOW_SHARED_TEST_DB", raising=False)
        with pytest.raises(UnsafeDatabaseError):
            assert_disposable_database(_url("caprep_test"))


class TestTheConventionIsEnforcedBeforeDamage:
    """The guard must sit ahead of the TRUNCATE, not after it."""

    def test_open_database_checks_before_truncating(self) -> None:
        """Reads the call order out of the source.

        Asserting behaviour would need a live server and a real table to destroy,
        which is exactly what this test exists to avoid. Reading the source is
        adequate here: the requirement is about ordering, and ordering is visible
        in the source.
        """
        import inspect
        import pathlib

        source = pathlib.Path(inspect.getfile(assert_disposable_database)).read_text()
        body = source.split("async def open_database", 1)[1]
        guard = body.find("assert_disposable_database")
        truncate = body.find("_truncate_all")
        assert guard != -1, "open_database no longer calls the guard"
        assert truncate != -1
        assert guard < truncate, (
            "the database-name check must run BEFORE the truncation; "
            "checking afterwards would be checking a database that is already empty"
        )


class TestDocumentation:
    def test_the_isolation_document_exists_and_states_the_rule(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[3]
        document = root / "docs" / "ENVIRONMENT_ISOLATION.md"
        assert document.is_file(), "docs/ENVIRONMENT_ISOLATION.md is missing"
        text = document.read_text()
        assert OWNED_PREFIX in text
        # The prohibition has to be written down, not only implied by code.
        assert "caprep_test" in text
