#!/usr/bin/env python3
# ruff: noqa: T201
"""Prove a staging target is staging BEFORE anything irreversible runs against it.

WHY THIS EXISTS
---------------
`alembic upgrade head` is the first action in this repository that cannot be
undone by deleting a file. Everything before it is cheap: reading a ref, opening a
connection, asking the server its own name. Everything after it is not.

So this script is deliberately the ONLY thing that runs first, and it is
read-only by construction: it opens a connection, runs ``SELECT
current_database()``, and closes it. It never runs DDL, never runs Alembic, and
never writes.

THE ORDERING IT ENFORCES
-------------------------
    project ref  !=  development ref      <- the project is not dev
    DIRECT_DATABASE_URL carries that ref  <- the URL points at that project
    current_database() is not a dev name <- the server agrees
    ------------------------------------------------------------------
    only now may `alembic upgrade head` be run, and by a human, deliberately

Each step can fail while the previous ones passed, which is the point. A staging
project can be perfectly isolated and still have its URL pointed at the
development database; only the third step distinguishes "staging project" from
"staging database". Those are different claims and conflating them is how a
migration lands on `caprep`.

SECRETS
-------
This script NEVER prints a secret, a password, a connection string, or a token -
not on success, not on failure, not in a traceback. Connection failures are
reported as a verdict carrying the host and database name, never the credentials
embedded in the same string. If this script ever appears to need to show you a
URL to be useful, that is a bug in the script.

USAGE
-----
    # read-only; exits non-zero unless every check passes
    python scripts/staging_preflight.py --forbid-ref <development-ref>

    # in CI, where the refs are already environment variables
    python scripts/staging_preflight.py --forbid-ref "$DEV_SUPABASE_REF"

Exit code 0 means "identity proven, migrations are now safe to run by hand".
It does NOT run them.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

#: The repository root. This script runs from the repo root like every other
#: entry point here, and it must be able to find `apps/api` on the path.
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from pydantic import ValidationError  # noqa: E402 - after the sys.path insert above

from app.core.config import ConfigurationError, Settings  # noqa: E402

#: Database names that must never be the target of a staging migration. The brief
#: names these; the list is a CLI input, not a constant, so it cannot drift from
#: what the operator was told.
DEFAULT_FORBIDDEN_DATABASES = (
    "caprep",
    "caprep_test",
    "caprep_v2_test",
    "caprep_v2_rehearsal",
)


@dataclass(frozen=True)
class Verdict:
    """One check's outcome, and why.

    `ok=False` with `detail` set is the only failure shape, so a caller cannot
    report success while quietly omitting the reason.
    """

    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        return f"  [{mark}] {self.name}: {self.detail}"


def supabase_ref(url: str | None) -> str | None:
    """The project ref in a Supabase URL, or None if there isn't one.

    Derived from the URL rather than configured separately, because a second
    source of truth for something already present is a second thing to forget to
    update. `<ref>.supabase.co` and `<ref>.supabase.in` are both handled.
    """
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    for suffix in (".supabase.co", ".supabase.in"):
        if host.endswith(suffix):
            label = host[: -len(suffix)]
            # A Supabase ref is a single 20-character label. A database hostnames
            # form like `db.<ref>.supabase.co` would otherwise yield `db.<ref>`, and
            # that wrong value would then be compared against the forbidden ref and
            # fail for the wrong reason - or, worse, be printed as if it were the
            # project identity. Only the exact shape is accepted.
            if len(label) == 20 and label.isalnum():
                return label
            return None
    return None


def database_name(url: str | None) -> str | None:
    """The database name in a Postgres URL, or None.

    Path-only: `/mydb?sslmode=require` -> `mydb`. Returns None for a URL with no
    path, which is itself a finding - a DSN must name a database.
    """
    if not url:
        return None
    return urlsplit(url).path.lstrip("/") or None


def url_host(url: str | None) -> str | None:
    """Hostname only. Never the credentials, which share the same string."""
    return urlsplit(url or "").hostname


def check_project_isolated(staging_ref: str | None, forbidden_refs: set[str]) -> Verdict:
    if not staging_ref:
        return Verdict(
            "supabase project ref",
            False,
            "no ref could be derived from SUPABASE_URL; refusing to continue",
        )
    clashes = sorted(r for r in forbidden_refs if r and r in staging_ref)
    if clashes:
        # The forbidden ref is printed because it IS the finding, and a project
        # ref is public by design - it is the hostname.
        return Verdict(
            "supabase project ref",
            False,
            f"staging points at the DEVELOPMENT project ({clashes[0]}); "
            "STOP - do not connect, do not migrate",
        )
    return Verdict("supabase project ref", True, f"{staging_ref} (distinct from development)")


def check_url_points_at_project(url: str | None, staging_ref: str | None) -> Verdict:
    """The URL must name the staging project, not merely be reachable.

    A staging `DIRECT_DATABASE_URL` that omits the ref (a bare `db.internal` host,
    a localhost port-forward) proves nothing about which project it reaches, so it
    is a failure rather than a pass.
    """
    host = url_host(url)
    if not url:
        return Verdict("DIRECT_DATABASE_URL", False, "not set")
    if not staging_ref:
        return Verdict("DIRECT_DATABASE_URL", False, f"host {host} cannot be tied to a ref")
    if staging_ref not in url:
        return Verdict(
            "DIRECT_DATABASE_URL",
            False,
            f"host {host} does not carry the staging ref {staging_ref}; "
            "it may point at another project",
        )
    # The wording names the ref that was actually matched, because this line can
    # print even when an EARLIER check already failed - and "carries the staging
    # ref" next to a staging ref that was just rejected as the development project
    # reads as a contradiction. It is a fact about the URL, not an endorsement.
    return Verdict("DIRECT_DATABASE_URL", True, f"host {host} matches ref {staging_ref}")


def check_database_name(name: str | None, forbidden: set[str]) -> Verdict:
    if not name:
        return Verdict(
            "current_database()",
            False,
            "the connection named no database; refusing to continue",
        )
    if name in forbidden:
        return Verdict(
            "current_database()",
            False,
            f"the server reports {name!r}, which is on the do-not-touch list; "
            "STOP - do not migrate",
        )
    return Verdict("current_database()", True, f"the server reports {name!r}")


def read_current_database(url: str) -> str | None:
    """Ask the server its own name. Read-only, one statement, no DDL.

    Returns None if the connection could not be made. The caller reports that as a
    failure WITHOUT the exception text, because a psycopg DSN error can embed the
    URL it failed on.
    """
    try:
        from sqlalchemy import create_engine, text
    except ImportError:  # pragma: no cover - sqlalchemy is a hard dependency
        return None
    try:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                return connection.execute(text("SELECT current_database()")).scalar_one()
        finally:
            engine.dispose()
    except Exception:  # noqa: BLE001 - deliberately broad; the message may leak a DSN
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only proof that a staging target is staging. Runs no migrations.",
    )
    parser.add_argument(
        "--forbid-ref",
        action="append",
        default=[],
        metavar="REF",
        help="a project ref that must NOT be the staging target; repeatable",
    )
    parser.add_argument(
        "--forbid-database",
        action="append",
        default=[],
        metavar="NAME",
        help="a database name that must NOT be the target; repeatable",
    )
    args = parser.parse_args()

    forbidden_refs = {r for r in [*args.forbid_ref, os.getenv("DEV_SUPABASE_REF", "")] if r}
    forbidden_dbs = set(args.forbid_database) or set(DEFAULT_FORBIDDEN_DATABASES)

    print("Staging preflight - read-only. No migration is run by this script.\n")

    # Load through the SUPPORTED selection mechanism, never by sourcing the file by
    # hand. `ENVIRONMENT` is set here so the app's own fail-closed rules apply; if
    # `.env.staging` is missing this raises and we stop, rather than quietly reading
    # `.env` and reporting on the development database.
    os.environ["ENVIRONMENT"] = "staging"
    try:
        settings = Settings()
    except ConfigurationError as exc:
        # The one message safe to show in full: it is composed entirely of the
        # environment name and the file name, neither of which is a secret.
        print(f"  [FAIL] staging configuration: {exc}")
        print("\nRESULT: BLOCKED_EXTERNAL - no staging configuration to verify.")
        return 2
    except ValidationError as exc:
        # NOT shown in full, deliberately. A pydantic message embeds the offending
        # INPUT VALUE (`input_value='...'`), so printing it verbatim would echo a
        # credential whenever the mistyped field happens to be a secret one. Only
        # the field names and the expected types are safe, and they are enough to
        # fix the file.
        #
        # This is not hypothetical: an ambient `DEBUG=release` in the shell - a
        # Django-ism that has no meaning here - is enough to land here, because a
        # real environment variable outranks the file.
        fields = ", ".join(
            f"{'.'.join(str(p) for p in err['loc'])} (expected {err['type']})"
            for err in exc.errors()
        )
        print(f"  [FAIL] staging configuration is invalid: {fields}")
        print("  (values are not shown: a pydantic error echoes the input, and the")
        print("   input may be the secret you were trying to protect)")
        print("\nRESULT: BLOCKED_EXTERNAL - staging configuration does not validate.")
        return 2

    staging_ref = supabase_ref(settings.supabase_url)
    verdicts = [
        check_project_isolated(staging_ref, forbidden_refs),
        check_url_points_at_project(settings.direct_database_url, staging_ref),
    ]

    # The third check needs a live connection, and only the first two passing makes
    # it safe to open one. Opening it earlier is how a verification script becomes
    # the thing that touches the wrong database.
    if all(v.ok for v in verdicts):
        direct = settings.direct_database_url or settings.database_url
        if not direct:
            verdicts.append(
                Verdict(
                    "current_database()",
                    False,
                    "neither DATABASE_URL nor DIRECT_DATABASE_URL is set",
                )
            )
        elif (name := read_current_database(direct)) is None:
            verdicts.append(
                Verdict(
                    "current_database()",
                    False,
                    "could not connect using the staging direct URL "
                    f"(host {url_host(direct)}); the DSN is deliberately not shown",
                )
            )
        else:
            verdicts.append(check_database_name(name, forbidden_dbs))
    else:
        verdicts.append(
            Verdict(
                "current_database()",
                False,
                "SKIPPED - identity was already unproven, so no connection was opened",
            )
        )

    for verdict in verdicts:
        print(verdict.line())

    ok = all(v.ok for v in verdicts)
    print()
    if ok:
        print("RESULT: ISOLATED")
        print(
            "\nIdentity proven. `alembic upgrade head` against the staging DIRECT URL is "
            "now safe to run.\nExpected head: 6c3f7b0a0c13. Do not run downgrade."
        )
        return 0

    print("RESULT: NOT_PROVEN")
    print(
        "\nDo not connect, do not migrate, do not create storage objects. "
        "Fix the reported item and run this again."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
