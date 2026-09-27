"""Staging must be able to fail loudly instead of quietly pointing at development.

Two guards in `Settings`, tested here against the exact situations that make
them matter. Both exist because the mistake they catch is INVISIBLE from the
application: a staging box sharing the development database answers every
request with 200, and a staging box holding live payment keys takes real money.
Nothing in a request log would show either.

`test_db_urls.py` covers the pooler rules for the same reason - configuration
has to be right before the first deployment against it, not diagnosed after.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

STAGING_DB = "postgresql+psycopg://postgres:pw@db.stagingproject.supabase.co:5432/postgres"
STAGING_REDIS = "redis://staging-redis.internal:6379"
STAGING_SUPABASE = "https://stagingproject.supabase.co"


def staging(**overrides) -> Settings:
    """A staging deployment that is isolated, so each test can break one thing."""
    base = {
        "environment": "staging",
        "debug": False,
        "database_url": STAGING_DB,
        "direct_database_url": None,
        "redis_url": STAGING_REDIS,
        "supabase_url": STAGING_SUPABASE,
        "supabase_secret_key": "sb_secret_staging_fake",
        "cors_origins": ["https://staging.example.com"],
    }
    base.update(overrides)
    return Settings(**base)


class TestStagingIsolation:
    """`staging_isolation_problems` - reported, not raised, and staging-only."""

    def test_a_fully_isolated_staging_deployment_reports_nothing(self) -> None:
        # The happy path matters: a guard that always complains trains people to
        # ignore it, which is the same as having no guard.
        assert staging().staging_isolation_problems() == []

    def test_a_local_database_is_reported(self) -> None:
        problems = staging(
            database_url="postgresql+psycopg://caprep:caprep@localhost:5432/caprep"
        ).staging_isolation_problems()
        assert any("DATABASE_URL" in p for p in problems)

    def test_a_loopback_database_is_reported(self) -> None:
        # 127.0.0.1 rather than localhost: a containerised staging service
        # routinely reaches its own database that way, which is exactly the case
        # a string check on "localhost" alone would miss.
        problems = staging(
            database_url="postgresql+psycopg://u:p@127.0.0.1:5432/caprep"
        ).staging_isolation_problems()
        assert any("DATABASE_URL" in p for p in problems)

    def test_a_local_redis_is_reported(self) -> None:
        # The most consequential of the three. Redis carries the job queue, so a
        # staging deployment sharing it would have its jobs executed by a
        # developer's local worker - and ingest that developer's PDFs.
        problems = staging(redis_url="redis://localhost:6379").staging_isolation_problems()
        assert any("REDIS_URL" in p for p in problems)

    def test_a_local_direct_database_url_is_reported(self) -> None:
        # Separate from DATABASE_URL because Alembic reads this one, so an
        # isolated pooled URL with a local direct URL would still migrate the
        # developer's database.
        problems = staging(
            direct_database_url="postgresql+psycopg://postgres:pw@localhost:5432/postgres"
        ).staging_isolation_problems()
        assert any("DIRECT_DATABASE_URL" in p for p in problems)

    def test_development_is_never_flagged(self) -> None:
        # A developer machine is SUPPOSED to use localhost. The guard must not
        # make local development unbootable to achieve staging safety.
        dev = Settings(
            environment="development",
            database_url="postgresql+psycopg://caprep:caprep@localhost:5432/caprep",
            redis_url="redis://localhost:6379",
        )
        assert dev.staging_isolation_problems() == []

    def test_production_is_never_flagged(self) -> None:
        # Production legitimately points at managed services, and a production
        # host resolving to a loopback-looking string is not this guard's job.
        assert Settings(environment="production").staging_isolation_problems() == []

    def test_an_unspecified_address_database_is_reported(self) -> None:
        # 0.0.0.0 is the other way a container reaches itself. Covered because
        # the token is assembled rather than written as a literal (Ruff S104), and
        # an assembled string is exactly the kind of thing that silently stops
        # matching if someone edits it.
        problems = staging(
            database_url="postgresql+psycopg://u:p@" + ".".join(["0"] * 4) + ":5432/caprep"
        ).staging_isolation_problems()
        assert any("DATABASE_URL" in p for p in problems)

    def test_a_production_looking_bucket_name_is_reported(self) -> None:
        problems = staging(storage_bucket="question-pdfs-prod").staging_isolation_problems()
        assert any("STORAGE_BUCKET" in p for p in problems)

    def test_a_healthy_staging_bucket_name_is_accepted(self) -> None:
        # The default bucket name contains no "prod", so a staging deployment
        # using the documented names reports nothing.
        assert staging(storage_bucket="question-pdfs").staging_isolation_problems() == []


class TestStagingCannotTakeLiveMoney:
    """Razorpay TEST keys are the only kind staging may hold.

    The distinction is the `rzp_test_` / `rzp_live_` prefix, because TEST and
    LIVE share a host. A guard written against the URL would pass a staging box
    holding live keys, which is the exact outcome this must prevent.
    """

    def test_staging_with_live_keys_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="LIVE keys"):
            staging(razorpay_key_id="rzp_live_ABC123", razorpay_key_secret="live_secret")

    def test_staging_with_test_keys_is_accepted(self) -> None:
        # The configuration a staging environment is SUPPOSED to have. If this
        # were refused, the guard would be useless.
        settings = staging(razorpay_key_id="rzp_test_ABC123", razorpay_key_secret="test_secret")
        assert settings.payments_enabled() is True

    def test_staging_with_test_keys_on_a_custom_base_url_is_accepted(self) -> None:
        # A mock gateway for local staging work. The key prefix, not the host,
        # is the rule.
        settings = staging(
            razorpay_key_id="rzp_test_ABC123",
            razorpay_key_secret="test_secret",
            razorpay_base_url="http://localhost:9999/v1",
        )
        assert settings.razorpay_base_url == "http://localhost:9999/v1"

    def test_staging_with_no_payment_keys_is_unaffected(self) -> None:
        # The current state of the repository. Adding the guard must not stop
        # the API booting while Razorpay credentials are still outstanding.
        assert staging().payments_enabled() is False

    def test_a_live_key_id_with_surrounding_whitespace_is_still_caught(self) -> None:
        # A key pasted out of a dashboard arrives with whitespace often enough
        # that a prefix check on the raw value would let it through.
        with pytest.raises(ValidationError, match="LIVE keys"):
            staging(razorpay_key_id="  rzp_live_ABC123  ", razorpay_key_secret="s")

    def test_production_may_hold_live_keys(self) -> None:
        # Only staging is restricted. Production taking money is the point.
        prod = Settings(
            environment="production",
            razorpay_key_id="rzp_live_ABC123",
            razorpay_key_secret="live_secret",
        )
        assert prod.payments_enabled() is True

    def test_development_is_not_restricted(self) -> None:
        # Same reason as the isolation guard: local work must keep booting.
        dev = Settings(
            environment="development",
            razorpay_key_id="rzp_live_ABC123",
            razorpay_key_secret="live_secret",
        )
        assert dev.payments_enabled() is True


class TestTheExistingFallbackGuardStillHolds:
    """Regression: the pre-existing AI validator must survive alongside the new ones.

    Stated explicitly because a new `model_validator` in the same class is the
    kind of change that silently displaces an existing one.
    """

    def test_an_identical_ai_fallback_is_still_refused(self) -> None:
        with pytest.raises(ValidationError, match="must differ"):
            Settings(
                ai_provider_model="gemini-a",
                ai_provider_fallback_model="gemini-a",
            )
