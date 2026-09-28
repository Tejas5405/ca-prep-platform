"""The staging preflight must refuse BEFORE it connects, not after.

`scripts/staging_preflight.py` is the gate in front of `alembic upgrade head` -
the first action in this repository that cannot be undone by deleting a file. A
verification script that connects before it has proved identity is worse than no
script, because it becomes the thing that touches the wrong database.

So the load-bearing tests here monkeypatch `read_current_database` to FAIL THE
TEST if it is ever called while a check is failing. Everything else pins the
parsing and the reporting.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import pytest

#: Anchored to this file, not to the CWD. Pytest's rootdir is `apps/api`, so a
#: CWD-relative path resolves to the wrong place depending on where the run was
#: invoked from - which is the kind of thing that passes locally and fails in CI.
ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "staging_preflight.py"


def _load():
    """Import the script as a module, the way a test should.

    Loaded by path rather than by name because `scripts/` is not a package and is
    deliberately not on `sys.path`; the script is an entry point, not library code.
    """
    spec = importlib.util.spec_from_file_location("staging_preflight", SCRIPT)
    assert spec and spec.loader, "staging_preflight.py could not be loaded"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pf = _load()

DEV_REF = "zyrmlnpvylhcpyaoizyz"
STAGING_REF = "vfewnfwyagcxtaxbmwqb"


def run_main(*args: str) -> int:
    """Call the script's `main()` with a given argv and return its exit code.

    `main()` sets `ENVIRONMENT=staging` in the real process environment, which is
    correct for a CLI and wrong for a test: without cleanup it leaks into every
    later test in the session, and those then fail closed with a confusing
    `ConfigurationError` about a file that has nothing to do with them. That is not
    hypothetical - it is exactly what this fixture exists to prevent.
    """
    saved_argv, saved_env = sys.argv, os.environ.get("ENVIRONMENT")
    sys.argv = ["staging_preflight.py", *args]
    try:
        return pf.main()
    finally:
        sys.argv = saved_argv
        if saved_env is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = saved_env


class TestRefExtraction:
    """The ref is derived from the URL, never configured separately."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (f"https://{STAGING_REF}.supabase.co", STAGING_REF),
            (f"https://{STAGING_REF}.supabase.in", STAGING_REF),
            (f"HTTPS://{STAGING_REF}.SUPABASE.CO", STAGING_REF),  # case-insensitive
            (f"https://{STAGING_REF}.supabase.co/rest/v1", STAGING_REF),
            (None, None),
            ("", None),
            ("https://example.com", None),  # not a Supabase host
        ],
    )
    def test_it_reads_the_ref_out_of_the_url(self, url, expected) -> None:
        assert pf.supabase_ref(url) == expected

    def test_it_does_not_invent_a_ref_from_a_database_url(self) -> None:
        # A Postgres DSN is not a project URL. Guessing a ref from one would let a
        # pooler hostname stand in for proof of identity.
        assert pf.supabase_ref("postgresql://u:p@db.abc.supabase.co:5432/postgres") is None


class TestDatabaseName:
    def test_it_reads_the_path(self) -> None:
        url = f"postgresql://u:p@db.{STAGING_REF}.supabase.co:5432/postgres?sslmode=require"
        assert pf.database_name(url) == "postgres"

    def test_no_path_means_no_name(self) -> None:
        assert pf.database_name("postgresql://u:p@host:5432") is None


class TestIsolationRefusal:
    """The development project must be refused by name."""


class TestUrlMustNameTheProject:
    def test_a_url_carrying_the_staging_ref_passes(self) -> None:
        url = f"postgresql://u:p@db.{STAGING_REF}.supabase.co:5432/postgres"
        assert pf.check_url_points_at_project(url, STAGING_REF).ok

    def test_a_bare_host_fails(self) -> None:
        # Reachable is not the same as identifiable. A host with no ref in it
        # proves nothing about which project it reaches.
        verdict = pf.check_url_points_at_project(
            "postgresql://u:p@db.internal.example.com:5432/postgres", STAGING_REF
        )
        assert not verdict.ok

    def test_a_missing_url_fails(self) -> None:
        assert not pf.check_url_points_at_project(None, STAGING_REF).ok


class TestForbiddenDatabaseNames:
    @pytest.mark.parametrize("name", ["caprep", "caprep_test", "caprep_v2_rehearsal"])
    def test_each_named_database_is_refused(self, name) -> None:
        verdict = pf.check_database_name(name, {"caprep", "caprep_test", "caprep_v2_rehearsal"})
        assert not verdict.ok
        assert "STOP" in verdict.detail

    def test_an_unnamed_staging_database_passes(self) -> None:
        assert pf.check_database_name("postgres", {"caprep"}).ok

    def test_no_name_fails(self) -> None:
        assert not pf.check_database_name(None, {"caprep"}).ok


class TestTheGateRefusesBeforeConnecting:
    """The invariant that matters: no socket while identity is unproven."""

    def test_a_missing_file_blocks_without_connecting(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DEBUG", raising=False)
        monkeypatch.setattr(
            pf, "read_current_database", lambda url: pytest.fail("opened a connection!")
        )
        assert run_main("--forbid-ref", DEV_REF) == 2

    def test_staging_pointing_at_development_blocks_without_connecting(
        self, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / ".env.staging").write_text(
            f"SUPABASE_URL=https://{DEV_REF}.supabase.co\n"
            f"DIRECT_DATABASE_URL=postgresql://u:p@db.{DEV_REF}.supabase.co:5432/postgres\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DEBUG", raising=False)
        monkeypatch.setattr(
            pf, "read_current_database", lambda url: pytest.fail("opened a connection!")
        )
        assert run_main("--forbid-ref", DEV_REF) == 1

    def test_a_url_without_the_ref_blocks_without_connecting(self, tmp_path, monkeypatch) -> None:
        (tmp_path / ".env.staging").write_text(
            f"SUPABASE_URL=https://{STAGING_REF}.supabase.co\n"
            "DIRECT_DATABASE_URL=postgresql://u:p@db.internal.example.com:5432/postgres\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DEBUG", raising=False)
        monkeypatch.setattr(
            pf, "read_current_database", lambda url: pytest.fail("opened a connection!")
        )
        assert run_main("--forbid-ref", DEV_REF) == 1

    def test_an_invalid_field_does_not_crash_with_a_traceback(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        """A pydantic error must not be printed raw: it echoes the input value.

        An ambient `DEBUG=release` in the shell is enough to trigger this, and if
        the mistyped field were a secret, printing the message verbatim would
        publish it.
        """
        (tmp_path / ".env.staging").write_text(
            f"SUPABASE_URL=https://{STAGING_REF}.supabase.co\nDEBUG=release\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEBUG", "release")  # a real variable outranks the file
        monkeypatch.setattr(
            pf, "read_current_database", lambda url: pytest.fail("opened a connection!")
        )
        assert run_main("--forbid-ref", DEV_REF) == 2
        out = capsys.readouterr().out
        assert "release" not in out, "the offending value was echoed to stdout"
        assert "debug" in out, "the failing field should still be named"

    def test_the_development_ref_fails(self) -> None:
        verdict = pf.check_project_isolated(DEV_REF, {DEV_REF})
        assert not verdict.ok
        assert "STOP" in verdict.detail

    def test_a_distinct_ref_passes(self) -> None:
        verdict = pf.check_project_isolated(STAGING_REF, {DEV_REF})
        assert verdict.ok
        assert STAGING_REF in verdict.detail

    def test_no_ref_fails_rather_than_passing(self) -> None:
        # The dangerous default: an underivable ref must not read as "no clash".
        assert not pf.check_project_isolated(None, {DEV_REF}).ok

    def test_the_match_is_a_substring_not_an_equality(self) -> None:
        # A ref of `abc` must not pass a URL containing `abcdef`; a pooler host can
        # carry a longer name that starts with the forbidden ref.
        assert not pf.check_project_isolated(f"{DEV_REF}xyz", {DEV_REF}).ok
