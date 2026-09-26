"""Where the database lives, and what changes when that is a managed pooler.

These assertions are about configuration, not about a connection: they run with no
database present, which is the point - the pooler rules have to be right BEFORE the
first deployment against one, and the failure they prevent (a prepared statement
that exists on one server connection and not the next) only appears under load.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.db_urls import (
    engine_kwargs,
    is_pooled_database,
    resolve_migration_url,
    with_sync_driver,
)

LOCAL = "postgresql+psycopg://caprep:caprep@localhost:5432/caprep"
SUPABASE_DIRECT = (
    "postgresql+psycopg://postgres:pw@db.zyrmlnpvylhcpyaoizyz.supabase.co:5432/postgres"
)
SUPABASE_POOLER = (
    "postgresql+psycopg://postgres.zyrmlnpvylhcpyaoizyz:pw"
    "@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
)
POOLER_ASYNC = SUPABASE_POOLER.replace("+psycopg", "+asyncpg")


class TestPoolerDetection:
    def test_a_local_server_is_not_a_pooler(self) -> None:
        assert is_pooled_database(LOCAL) is False

    def test_a_direct_supabase_connection_is_not_a_pooler(self) -> None:
        # Port 5432 is Supabase's direct (and session-pooler) port: a normal
        # session, so prepared statements are fine and disabling them would be a
        # needless round trip per query.
        assert is_pooled_database(SUPABASE_DIRECT) is False

    def test_the_transaction_pooler_is_detected_by_port(self) -> None:
        assert is_pooled_database(SUPABASE_POOLER) is True

    def test_a_pooler_hostname_is_detected_without_the_port(self) -> None:
        # Neon and some Supabase regions serve the pooler on 5432, so the host
        # name is the only signal. Detecting both is why this is not just a port
        # check.
        assert is_pooled_database("postgresql://u:p@ep-x-pooler.us-east-1.aws.neon.tech/db") is True

    def test_pgbouncer_on_its_usual_port_is_detected(self) -> None:
        assert is_pooled_database("postgresql+asyncpg://u:p@127.0.0.1:6432/caprep") is True

    def test_a_url_with_no_port_does_not_raise(self) -> None:
        # urlsplit().port is None here; the comparison must not blow up on it.
        assert is_pooled_database("postgresql://user@/caprep") is False


class TestEngineOptions:
    def test_a_direct_connection_keeps_prepared_statements(self) -> None:
        kwargs = engine_kwargs(LOCAL)
        assert "connect_args" not in kwargs
        assert kwargs["pool_pre_ping"] is True

    def test_psycopg_disables_prepared_statements_behind_a_pooler(self) -> None:
        assert engine_kwargs(SUPABASE_POOLER)["connect_args"] == {"prepare_threshold": None}

    def test_asyncpg_disables_its_caches_behind_a_pooler(self) -> None:
        # The integration harness builds an asyncpg engine from the same URL, so
        # both drivers have to be handled - the asyncpg one silently caches
        # prepared statements by default.
        assert engine_kwargs(POOLER_ASYNC)["connect_args"] == {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        }


class TestMigrationUrl:
    """Migrations must not run through a transaction pooler.

    ``alembic upgrade`` takes a session-scoped advisory lock and applies a
    multi-statement migration inside one transaction; a pooler that swaps the
    server connection underneath it can leave a half-applied schema, with the
    version table and the actual schema disagreeing from then on.
    """

    def test_the_direct_url_wins_over_the_app_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", SUPABASE_POOLER)
        monkeypatch.setenv("DIRECT_DATABASE_URL", SUPABASE_DIRECT)
        assert resolve_migration_url(Settings(_env_file=None)) == SUPABASE_DIRECT

    def test_it_falls_back_to_the_app_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DIRECT_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", LOCAL)
        assert resolve_migration_url(Settings(_env_file=None)) == LOCAL

    def test_the_settings_object_is_a_fallback_not_the_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An operator can point migrations elsewhere without editing the app's
        # configuration, and CI can aim them at a throwaway container.
        monkeypatch.delenv("DIRECT_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        settings = Settings(
            _env_file=None, database_url=SUPABASE_POOLER, direct_database_url=SUPABASE_DIRECT
        )
        assert resolve_migration_url(settings) == SUPABASE_DIRECT

    def test_the_scheme_is_normalised_for_sqlalchemy(self) -> None:
        # Render and Supabase hand out `postgresql://`; without an explicit driver
        # SQLAlchemy assumes psycopg2, which is not installed, so the failure is an
        # import error at connect time that never names the URL.
        assert with_sync_driver("postgresql://u:p@host:5432/db") == (
            "postgresql+psycopg://u:p@host:5432/db"
        )
        assert with_sync_driver("postgres://u:p@host:5432/db") == (
            "postgresql+psycopg://u:p@host:5432/db"
        )

    def test_an_already_normalised_url_is_left_alone(self) -> None:
        assert with_sync_driver(LOCAL) == LOCAL


class TestSettingsExposeBothUrls:
    def test_direct_defaults_to_unset_so_it_is_optional(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.direct_database_url is None

    def test_both_urls_can_be_set_independently(self) -> None:
        settings = Settings(
            _env_file=None,
            database_url=SUPABASE_POOLER,
            direct_database_url=SUPABASE_DIRECT,
        )
        assert is_pooled_database(settings.database_url) is True
        assert is_pooled_database(settings.direct_database_url or "") is False
