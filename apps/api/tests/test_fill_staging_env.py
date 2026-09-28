"""Filling `.env.staging` must not be possible to do WRONG.

The four staging values exist only in the owner's Supabase dashboard, and every
way of getting them into the file by hand has already failed once: the dashboard
PAGE was pasted in as text, and a secret was wrapped across two lines by the
dashboard's own display. Both produced a file that looked plausible and contained
nothing usable.

`scripts/fill_staging_env.py` replaces the hand-edit. These tests pin the checks
that make it safe - each one corresponds to a way this has actually gone wrong, or
to a failure that stays invisible until it corrupts a migration.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "fill_staging_env.py"


def _load():
    spec = importlib.util.spec_from_file_location("fill_staging_env", SCRIPT)
    assert spec and spec.loader, "fill_staging_env.py could not be loaded"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fill = _load()

DEV_REF = "zyrmlnpvylhcpyaoizyz"
STAGING_REF = "vfewnfwyagcxtaxbmwqb"
GOOD_DIRECT = (
    f"postgresql://postgres.{STAGING_REF}:Xk3%40mPq7zR@db.{STAGING_REF}.supabase.co:5432/postgres"
)
GOOD_POOLER = (
    f"postgresql://postgres.{STAGING_REF}:Xk3%40mPq7zR@"
    f"aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
)


class TestTheDevelopmentRefIsRefused:
    def test_the_dev_project_is_rejected_outright(self) -> None:
        problems = fill.validate_supabase_url(f"https://{DEV_REF}.supabase.co", DEV_REF)
        assert problems
        assert any("DEVELOPMENT" in p for p in problems)

    def test_a_dev_database_string_is_rejected_for_staging(self) -> None:
        url = f"postgresql://postgres.{DEV_REF}:pw@db.{DEV_REF}.supabase.co:5432/postgres"
        problems = fill.validate_database_url(url, "DATABASE_URL", STAGING_REF)
        assert problems
        assert any("staging ref" in p for p in problems)


class TestDashboardTextIsNotAValue:
    """The failure that actually happened: the API Keys PAGE pasted as a value."""

    @pytest.mark.parametrize(
        "junk",
        [
            "Secret keys",
            "default\nNo description",
            "sb_secret_abc\nsb_secret_def",  # wrapped across two lines
            "NAME\tAPI KEY",
        ],
    )
    def test_dashboard_text_is_never_a_valid_url(self, junk) -> None:
        assert fill.validate_supabase_url(junk, DEV_REF)

    def test_a_two_line_secret_is_not_a_valid_key(self) -> None:
        # Exactly what the dashboard's wrapping produced.
        assert fill.validate_secret_key("sb_secret_y6Cq7\njmbSJRfvvbj0QfJDQ_hcaBhkM7")

    def test_an_empty_secret_is_rejected(self) -> None:
        assert fill.validate_secret_key("")


class TestPlaceholdersCannotSlipThrough:
    @pytest.mark.parametrize("password", ["YOUR_PASSWORD", "YOUR%5FPASSWORD", "your-ref-pw"])
    def test_a_template_password_is_caught_decoded_and_raw(self, password) -> None:
        """A password inside a DSN is percent-encoded, so both forms are checked.

        Checking only the raw URL let `YOUR_PASSWORD` through, because the same
        substring is not guaranteed to survive encoding unchanged.
        """
        url = (
            f"postgresql://postgres.{STAGING_REF}:{password}"
            f"@db.{STAGING_REF}.supabase.co:5432/postgres"
        )
        problems = fill.validate_database_url(url, "DIRECT_DATABASE_URL", STAGING_REF)
        assert any("placeholder" in p for p in problems)


class TestThePoolerCannotReachAlembic:
    """The failure this prevents is a half-applied schema, not a failed deploy.

    `alembic upgrade` takes an advisory lock and runs a multi-statement migration
    inside one transaction. A transaction-mode pooler can hand it a different
    connection between statements, leaving the version table and the schema
    disagreeing - which is discovered later, by whatever runs next.
    """

    def test_a_pooler_url_in_DIRECT_is_refused(self) -> None:
        problems = fill.validate_database_url(GOOD_POOLER, "DIRECT_DATABASE_URL", STAGING_REF)
        assert any("pooler port" in p for p in problems)

    def test_the_same_pooler_url_is_fine_for_the_app(self) -> None:
        assert fill.validate_database_url(GOOD_POOLER, "DATABASE_URL", STAGING_REF) == []

    def test_a_direct_url_in_DIRECT_is_fine(self) -> None:
        assert fill.validate_database_url(GOOD_DIRECT, "DIRECT_DATABASE_URL", STAGING_REF) == []


class TestACompleteCorrectConfigurationIsAccepted:
    """Every check above must not fire on the right answer."""

    def test_all_four_values_pass_together(self) -> None:
        assert fill.validate_supabase_url(f"https://{STAGING_REF}.supabase.co", DEV_REF) == []
        assert fill.validate_database_url(GOOD_POOLER, "DATABASE_URL", STAGING_REF) == []
        assert fill.validate_database_url(GOOD_DIRECT, "DIRECT_DATABASE_URL", STAGING_REF) == []
        assert fill.validate_secret_key("sb_secret_" + "a" * 40) == []

    @pytest.mark.parametrize("url", ["not a url at all", "", "postgresql://"])
    def test_malformed_urls_are_reported_not_raised(self, url) -> None:
        # The helper must never raise on operator input; it reports.
        assert isinstance(fill.validate_database_url(url, "DATABASE_URL", None), list)


class TestUpsert:
    def test_it_replaces_an_existing_line(self) -> None:
        text = "A=1\nSUPABASE_URL=old\nB=2\n"
        assert "SUPABASE_URL=new" in fill.upsert(text, "SUPABASE_URL", "new")
        assert "old" not in fill.upsert(text, "SUPABASE_URL", "new")

    def test_it_appends_when_absent(self) -> None:
        assert "NEW=7" in fill.upsert("A=1\n", "NEW", "7")

    def test_it_does_not_double_up_on_repeat(self) -> None:
        once = fill.upsert("A=1\n", "K", "v")
        twice = fill.upsert(once, "K", "v")
        assert once.count("K=") == twice.count("K=") == 1

    def test_a_real_encoded_password_is_still_accepted(self) -> None:
        """The check must not be so broad that it refuses a legitimate password."""
        assert fill.validate_database_url(GOOD_DIRECT, "DIRECT_DATABASE_URL", STAGING_REF) == []
