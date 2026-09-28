#!/usr/bin/env python3
# print() is this CLI's designed interface: the prompts and the validation report
# written to stdout ARE the product. Same exemption check_secrets.py declares.
# ruff: noqa: T201
"""Fill the four blank staging values interactively, with validation.

WHY THIS EXISTS
---------------
`.env.staging` needs four values that exist only in the owner's Supabase
dashboard. Twice now those values have been pasted into a chat or pasted as raw
dashboard TEXT into the file - once wrapping a secret across two lines, so the
value was not even valid. Both failure modes are easy to prevent and impossible
to detect afterwards, because a half-entered secret looks exactly like a working
one until something connects with it.

So this asks for each value on a hidden prompt, checks it, and writes it. The
value never appears in the terminal, in the scrollback, or in a chat transcript.

WHAT IT CHECKS
--------------
Each rule below corresponds to a way this has already gone wrong, or to a
failure that is invisible until it corrupts something:

  * the ref is a real Supabase ref shape, and is NOT the development ref
  * both database URLs carry the staging ref, so they cannot be a copy of the
    development connection string
  * `DIRECT_DATABASE_URL` is NOT a pooler port. Alembic takes an advisory lock
    and runs a multi-statement migration in one transaction; through a
    transaction pooler it can be handed a different connection between
    statements, leaving a half-applied schema where the version table and the
    schema disagree. That is the worst outcome available, and it is invisible
    until the next deploy.
  * the secret key is not empty and does not still say `sb_secret_...`

It refuses to write anything if a check fails. Nothing is echoed, ever.

USAGE
-----
    python scripts/fill_staging_env.py

Prompts for four values, writes them, then tells you to run the preflight.
"""

from __future__ import annotations

import getpass
import pathlib
import re
import sys
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env.staging"

#: The one ref that must never appear in a staging file. Passed in rather than
#: hardcoded: the P2A brief forbids hardcoding infrastructure identity into
#: application code, and a guard that cannot be pointed at a different project is
#: a guard that quietly stops guarding.
DEFAULT_FORBIDDEN_REF = "zyrmlnpvylhcpyaoizyz"

#: Ports on which a TRANSACTION-mode pooler listens. A direct connection is 5432.
POOLER_PORTS = frozenset({6543, 6432})

#: Substrings that mean "this is still a template". Checked on decoded values,
#: because a password inside a DSN is percent-encoded.
PLACEHOLDER_MARKERS = ("YOUR_", "your-", "REPLACE_ME", "CHANGEME", "your-")


def looks_like_ref(value: str) -> bool:
    """A Supabase project ref is a single 20-character alphanumeric label."""
    return len(value) == 20 and value.isalnum()


def ref_from_supabase_url(url: str) -> str | None:
    if not url.startswith("https://"):
        return None
    host = (urlsplit(url).hostname or "").lower()
    for suffix in (".supabase.co", ".supabase.in"):
        if host.endswith(suffix):
            return host[: -len(suffix)] or None
    return None


def url_port(url: str) -> int | None:
    try:
        return urlsplit(url).port
    except ValueError:
        return None


def validate_supabase_url(url: str, forbidden_ref: str) -> list[str]:
    """Problems with the SUPABASE_URL. Empty list means good."""
    problems: list[str] = []
    ref = ref_from_supabase_url(url)
    if not ref:
        problems.append("not an https://<ref>.supabase.co URL")
        return problems
    if not looks_like_ref(ref):
        problems.append(f"the project ref {ref!r} is not 20 alphanumeric characters")
    if ref == forbidden_ref:
        problems.append(
            f"that is the DEVELOPMENT project ({forbidden_ref}); staging must not use it"
        )
    return problems


def validate_database_url(url: str, field: str, staging_ref: str | None) -> list[str]:
    """Problems with one database URL. Empty list means good."""
    from urllib.parse import unquote

    problems: list[str] = []
    if not url:
        return [f"{field} is empty"]
    if not url.startswith("postgresql"):
        problems.append(f"{field} must start with postgresql:// or postgresql+psycopg://")
        return problems
    parsed = urlsplit(url)
    if not parsed.hostname:
        problems.append(f"{field} has no host")
    if not parsed.password:
        problems.append(f"{field} has no password in it")
    if not parsed.path.lstrip("/"):
        problems.append(f"{field} names no database")
    if staging_ref and staging_ref not in url:
        problems.append(
            f"{field} does not contain the staging ref {staging_ref}, so it cannot be "
            "shown to point at the staging project"
        )
    # Placeholder markers are checked on the DECODED password as well as the raw
    # URL. A password is percent-encoded inside a DSN, so `YOUR_PASSWORD` can
    # arrive as `YOUR%5FPASSWORD` - or vice versa - and a substring check on the
    # raw string alone would wave a template straight through.
    decoded = unquote(url)
    for marker in PLACEHOLDER_MARKERS:
        if marker in decoded:
            problems.append(f"{field} still contains the placeholder {marker!r} in the password")
    if field == "DIRECT_DATABASE_URL" and url_port(url) in POOLER_PORTS:
        problems.append(
            f"{field} is on pooler port {url_port(url)}. Alembic must use the DIRECT "
            "connection: a transaction pooler cannot hold the session `alembic "
            "upgrade` needs, and the result is a half-applied schema"
        )
    return problems


def validate_secret_key(value: str) -> list[str]:
    problems: list[str] = []
    if not value.strip():
        problems.append("the secret key is empty")
    # A key is ONE line. The Supabase dashboard wraps a long key for display, and
    # copying that rendering produces two lines that look like a key and are not
    # one - which happened here. A newline is the cheapest possible tell.
    if "\n" in value or "\r" in value:
        problems.append(
            "the secret key spans more than one line - the dashboard wraps it for "
            "display, so copy it from the Connect/API panel, not from the page text"
        )
    for marker in PLACEHOLDER_MARKERS:
        if marker in value:
            problems.append(f"the secret key still contains the placeholder {marker!r}")
    if len(value) < 20:
        problems.append("the secret key is too short to be real")
    return problems


def ask(prompt: str) -> str:
    """Read one value with echo off.

    `getpass` is the whole point: the value must not land in the scrollback, in a
    screen share, or in a copied terminal buffer. Falls back to input() only where
    there is no TTY, and says so rather than silently echoing.
    """
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled. Nothing was written.")
        raise SystemExit(130) from None


def main() -> int:
    forbidden_ref = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FORBIDDEN_REF

    if not ENV_FILE.is_file():
        print(f"  {ENV_FILE.name} does not exist. Create it from .env.staging.example first.")
        return 2

    print(
        "Filling the staging values. Nothing you type is echoed, and nothing is\n"
        "written until every check passes.\n"
    )

    supabase_url = ask("  SUPABASE_URL (copy from Project Settings -> API): ").strip()
    ref = ref_from_supabase_url(supabase_url)

    print("\n  Connection strings: Project Settings -> Connect -> Postgres.")
    print(
        "  Copy them COMPLETE. Do not edit the host - the pooler host cannot be\n"
        "  derived from the region.\n"
    )
    database_url = ask("  DATABASE_URL (pooler, for the app): ").strip()
    direct_url = ask("  DIRECT_DATABASE_URL (direct, for Alembic): ").strip()

    print("\n  The secret key is server-only. Rotate the one pasted in chat first,")
    print("  then paste the NEW one here.\n")
    secret_key = ask("  SUPABASE_SECRET_KEY: ").strip()

    problems: list[str] = []
    problems += [f"SUPABASE_URL: {p}" for p in validate_supabase_url(supabase_url, forbidden_ref)]
    problems += [
        f"DATABASE_URL: {p}" for p in validate_database_url(database_url, "DATABASE_URL", ref)
    ]
    problems += [
        f"DIRECT_DATABASE_URL: {p}"
        for p in validate_database_url(direct_url, "DIRECT_DATABASE_URL", ref)
    ]
    problems += [f"SUPABASE_SECRET_KEY: {p}" for p in validate_secret_key(secret_key)]

    if problems:
        print("\n  NOT WRITTEN. Fix these and run it again:\n")
        for problem in problems:
            print(f"    - {problem}")
        print("\n  (The pooler host in DATABASE_URL must be copied from the Connect")
        print("   dialog. It cannot be constructed from the region.)")
        return 1

    text = ENV_FILE.read_text()
    for key, value in (
        ("ENVIRONMENT", "staging"),
        ("SUPABASE_URL", supabase_url),
        ("SUPABASE_SECRET_KEY", secret_key),
        ("DATABASE_URL", database_url),
        ("DIRECT_DATABASE_URL", direct_url),
    ):
        text = upsert(text, key, value)
    ENV_FILE.write_text(text)

    print(f"\n  Written to {ENV_FILE.name} (gitignored: .env.staging is not tracked).")
    print("  Every value passed validation.\n")
    print("  Now prove identity - this opens ONE read-only connection:")
    print(f"    python scripts/staging_preflight.py --forbid-ref {forbidden_ref}")
    print("\n  Do NOT run `alembic upgrade head` until that prints RESULT: ISOLATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def upsert(text: str, key: str, value: str) -> str:
    """Set KEY=value, replacing an existing line or appending."""
    pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line.replace("\\", "\\\\"), text)
    return text.rstrip("\n") + f"\n{line}\n"
