"""Which configuration an environment reads, and what happens when it is missing.

The invariant this file exists to protect:

    Staging and production NEVER fall back to the development `.env`.

Before this, `Settings.model_config` carried `env_file=".env"` unconditionally,
so the dangerous sequence was possible with no warning at all:

    ENVIRONMENT=staging
      -> .env read anyway
      -> the development database URL loaded
      -> the service starts, connects to localhost, reports healthy

Nothing errored, because the file it fell back to was perfectly valid. That is
what makes it worth a dedicated file rather than a line in the config.

Every test uses `tmp_path` and `monkeypatch.chdir`, so it runs against a synthetic
directory and never touches the developer's real `.env`.
"""

from __future__ import annotations

import pathlib

import pytest
from pydantic import ValidationError

from app.core.config import (
    ENV_FILE_FOR_ENVIRONMENT,
    FAIL_CLOSED_ENVIRONMENTS,
    ConfigurationError,
    Settings,
    get_settings,
    resolve_env_file,
)
from app.workers import rq_worker

#: Written to the synthetic `.env`, so a test can prove it was NOT read.
DEV_MARKER = "dev-bucket-must-never-be-used-outside-development"
#: Written to the synthetic `.env.staging`, to prove that file WAS read.
STAGING_MARKER = "staging-bucket"


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    """Every test starts from a known ENVIRONMENT with no cached Settings.

    Two sources of contamination, both of which produced confusing failures here:

    * The developer's shell may already export `ENVIRONMENT`, and this file's
      whole subject is what that value selects.
    * `get_settings` is `lru_cache`d, so a value resolved by an earlier test
      would be reused here and the test would pass or fail for the wrong reason.

    `ENV_FILE` is cleared too, since it overrides the mapping.
    """
    for name in ("ENVIRONMENT", "ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A directory holding a development `.env` and a staging `.env.staging`.

    Both exist, each with a distinguishable value, so a test can tell which file
    was read. `chdir` makes the relative path resolve here, never in the repo.
    """
    (tmp_path / ".env").write_text(f"ENVIRONMENT=development\nSTORAGE_BUCKET={DEV_MARKER}\n")
    (tmp_path / ".env.staging").write_text(
        f"ENVIRONMENT=staging\nSTORAGE_BUCKET={STAGING_MARKER}\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def staging_workspace(workspace, monkeypatch):
    """`workspace` plus the operator's explicit choice of environment.

    `ENVIRONMENT` must be set by the PROCESS, not read from the file. The file is
    chosen *before* it is read, so a file that declares `ENVIRONMENT=staging`
    cannot select itself - which is the correct and safer design, and the reason
    these tests set it here rather than relying on the file's contents.
    """
    monkeypatch.setenv("ENVIRONMENT", "staging")
    return workspace


class TestResolveEnvFile:
    """The mapping. Pure, and the thing everything else is built on."""

    @pytest.mark.parametrize(
        ("environment", "expected"),
        [
            ("development", ".env"),
            ("staging", ".env.staging"),
            ("production", ".env.production"),
            # No file: CI and a deployed service both supply real variables.
            ("test", None),
            # An unrecognised environment must not inherit the development file.
            ("bogus", None),
            # Unset behaves as development, preserving the historical default so
            # a contributor who exports nothing is unaffected.
            (None, ".env"),
        ],
    )
    def test_each_environment_resolves_to_its_own_file(
        self, environment: str | None, expected: str | None
    ) -> None:
        assert resolve_env_file(environment) == expected

    def test_env_file_variable_overrides_the_mapping(self, monkeypatch) -> None:
        # A deployment wanting a differently named file, with no code change.
        monkeypatch.setenv("ENV_FILE", "/etc/caprep/secret.env")
        assert resolve_env_file("staging") == "/etc/caprep/secret.env"

    def test_every_environment_is_declared(self) -> None:
        # An environment missing from the mapping would read NO file. That is the
        # safe direction but still surprising, so the omission is made deliberate.
        assert set(ENV_FILE_FOR_ENVIRONMENT) >= {
            "development",
            "staging",
            "production",
            "test",
        }


class TestStagingNeverFallsBack:
    """The critical regression: `.env` present, `.env.staging` absent -> FAIL."""

    def test_staging_reads_its_own_file(self, staging_workspace) -> None:
        assert Settings().storage_bucket == STAGING_MARKER

    def test_staging_never_reads_the_development_file(self, staging_workspace) -> None:
        # `.env` is right there and perfectly valid. It must be ignored.
        assert (staging_workspace / ".env").is_file(), "precondition: .env exists"
        assert Settings().storage_bucket != DEV_MARKER

    def test_staging_without_its_file_fails_closed(self, staging_workspace) -> None:
        # THE regression. Previously this read .env and started on localhost.
        (staging_workspace / ".env.staging").unlink()
        with pytest.raises(ConfigurationError) as caught:
            Settings()
        message = str(caught.value)
        assert ".env.staging" in message, "the error must name the missing file"
        assert "fall back" in message, "the error must say why it refuses"

    def test_the_failure_is_about_the_file_not_a_field(self, staging_workspace) -> None:
        # Supplying values explicitly still fails, which proves the check is on
        # the configuration SOURCE and not on some unrelated field being empty.
        (staging_workspace / ".env.staging").unlink()
        with pytest.raises(ConfigurationError):
            Settings(
                database_url="postgresql+psycopg://u:p@db.staging.supabase.co:5432/postgres",
                supabase_url="https://staging.supabase.co",
            )

    def test_staging_is_fail_closed_and_development_is_not(self) -> None:
        assert "staging" in FAIL_CLOSED_ENVIRONMENTS
        assert "development" not in FAIL_CLOSED_ENVIRONMENTS


class TestProduction:
    """Production behaves like staging: explicit, or it does not start."""

    def test_production_without_its_file_fails_closed(self, workspace, monkeypatch) -> None:
        assert not (workspace / ".env.production").exists(), "precondition"
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(ConfigurationError):
            Settings()

    def test_production_reads_its_own_file_when_present(self, workspace, monkeypatch) -> None:
        (workspace / ".env.production").write_text(
            "ENVIRONMENT=production\nSTORAGE_BUCKET=production-bucket\n"
        )
        monkeypatch.setenv("ENVIRONMENT", "production")
        settings = Settings()
        assert settings.storage_bucket == "production-bucket"
        assert settings.storage_bucket != DEV_MARKER


class TestCIAndExplicitVariables:
    """Real environment variables, with no file involved."""

    def test_test_environment_needs_no_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("STORAGE_BUCKET", "from-ci-variable")
        assert Settings().storage_bucket == "from-ci-variable"


class TestTheWorkerUsesTheSameRules:
    """The worker must not reach a database the API would refuse.

    It is a separate process with its own `get_settings()` call, so it needs its
    own proof rather than inheriting the API's.
    """

    def test_the_worker_shares_the_config_module(self) -> None:
        # Structural, and the cheapest possible assertion: if this breaks, the
        # behaviour tests below are exercising a different code path entirely.
        assert rq_worker.get_settings is get_settings

    def test_the_worker_fails_closed_without_a_staging_file(self, staging_workspace) -> None:
        (staging_workspace / ".env.staging").unlink()
        with pytest.raises(ConfigurationError):
            get_settings()

    def test_the_worker_reads_the_staging_file(self, staging_workspace) -> None:
        assert get_settings().storage_bucket == STAGING_MARKER

    def test_the_worker_would_not_silently_use_the_development_file(
        self, staging_workspace
    ) -> None:
        assert get_settings().storage_bucket != DEV_MARKER


class TestFrontendConfigurationStaysSeparate:
    """No backend secret may reach a VITE_ variable.

    Source-level rather than behavioural, because the failure it guards is a
    bundle that has already been built. `test_infra_contract.py` additionally
    scans the built output; this is the cheap check that runs on every commit.
    """

    def test_no_settings_field_is_vite_prefixed(self) -> None:
        for name in Settings.model_fields:
            assert not name.upper().startswith("VITE_"), (
                f"{name} would be inlined into the client bundle by Vite"
            )

    def test_the_secret_fields_exist_and_are_server_side(self) -> None:
        # Positive statement of the same rule, so the intent survives a rename.
        for name in (
            "supabase_secret_key",
            "database_url",
            "redis_url",
            "razorpay_key_secret",
        ):
            assert name in Settings.model_fields

    def test_the_frontend_declares_no_backend_secret(self) -> None:
        web_env = pathlib.Path("../../apps/web/.env.example").read_text()
        for banned in (
            "SUPABASE_SECRET_KEY",
            "DATABASE_URL",
            "REDIS_URL",
            "RAZORPAY_KEY_SECRET",
            "RAZORPAY_WEBHOOK_SECRET",
            "AI_PROVIDER_API_KEY",
        ):
            assert not any(line.startswith(f"VITE_{banned}") for line in web_env.splitlines()), (
                f"VITE_{banned} would publish a server-side secret"
            )

    def test_a_real_variable_beats_the_file(self, staging_workspace, monkeypatch) -> None:
        # Precedence is unchanged: a deploy can override one value without
        # editing a file, which is the whole point of environment variables.
        monkeypatch.setenv("STORAGE_BUCKET", "from-the-environment")
        assert Settings().storage_bucket == "from-the-environment"

    def test_explicit_keyword_arguments_still_win(self, workspace) -> None:
        assert Settings(storage_bucket="explicit").storage_bucket == "explicit"

    def test_a_typo_in_environment_fails_loudly(self, workspace, monkeypatch) -> None:
        """An unrecognised ENVIRONMENT is a ValidationError, not a silent default.

        `environment` is a `Literal`, so pydantic rejects `"stagng"` outright.
        That is strictly better than reading no file and quietly using defaults:
        a typo stops the process instead of starting it against the wrong
        configuration. Asserted here because the failure mode changed during
        implementation and the change is worth keeping deliberately.
        """
        monkeypatch.setenv("ENVIRONMENT", "stagng")
        with pytest.raises(ValidationError) as caught:
            Settings()
        assert "environment" in str(caught.value)


class TestDevelopmentIsUnchanged:
    """The existing developer workflow must keep working exactly as before."""

    def test_development_reads_dot_env(self, workspace) -> None:
        assert Settings().storage_bucket == DEV_MARKER

    def test_development_needs_no_extra_setup(self, workspace) -> None:
        # No export, no ENV_FILE: setting ENVIRONMENT explicitly to `development`
        # is equivalent, which is the property that matters.
        assert Settings(environment="development").storage_bucket == DEV_MARKER

    def test_development_does_not_require_a_file_to_exist(self, tmp_path, monkeypatch) -> None:
        # A new contributor must be able to run the suite before creating any
        # file, which is why development is not in FAIL_CLOSED_ENVIRONMENTS.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert Settings().environment == "development"
