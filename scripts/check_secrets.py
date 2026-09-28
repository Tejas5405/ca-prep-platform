#!/usr/bin/env python3
# print() is this CLI's designed interface: the scan report written to stdout is the
# product, not debug output left behind in library code, which is what T201 targets.
# ruff: noqa: T201
"""Scan a revision, the index, or the working tree for credentials.

Why this exists
---------------
A committed secret is permanent: deleting the file in a later commit does not
remove the object from history. This repository's first commit was gated by this
script (see docs/GIT_BASELINE.md).

Two independent checks run:

1. **Live-value check (strongest).** Every key-shaped value is read out of the
   git-ignored env files (``.env``, ``apps/web/.env.local``) and searched for by
   content, so a real credential is caught even where no pattern would recognise
   it. Only **file names** are printed - never the value.
2. **Pattern check.** Credential formats (Supabase, Razorpay, Stripe, Google,
   AWS, PEM, GitHub/Slack tokens, ``postgres://user:password@`` URLs) are matched
   across the target. Matches are printed **masked**; known fake fixtures are
   allow-listed with a written reason.

Matching is done in Python rather than with ``git grep``: git uses POSIX ERE
(no non-capturing groups, no ``\\s``), and its ``--cached``/``--untracked`` flags
must precede the pattern, which makes silent false negatives easy to write.

Usage
-----
    python scripts/check_secrets.py                 # the committed tree (HEAD)
    python scripts/check_secrets.py --staged        # what `git commit` would take
    python scripts/check_secrets.py --worktree      # tracked + untracked files
    python scripts/check_secrets.py --rev <rev>     # any revision/range

Exit code 0 = clean, 1 = something needs a human decision, 2 = no repository.
Stdlib only, so it runs in a fresh checkout.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Env files that hold live values. Missing files are skipped.
ENV_FILES = (".env", "apps/web/.env.local")

# Names that mark a value as credential-shaped when harvesting from env files.
SECRET_NAME_MARKERS = (
    "SECRET",
    "SERVICE_ROLE",
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "ANON",
    "PUBLISHABLE",
    "DATABASE_URL",
)

# Hosts whose credentials only work on the developer's own machine. The local
# Postgres role/password is a documented convention (`.env.example`, the default in
# app/core/config.py, the CI service container) and rotating it would break the
# documented local setup, so it is exempt from the live-value check - reported
# explicitly rather than silently ignored. A DSN pointing anywhere else still fails.
LOCAL_HOSTS = ("@localhost:", "@localhost/", "@127.0.0.1:", "@127.0.0.1/")

PATTERNS: tuple[tuple[str, str], ...] = (
    ("supabase-secret", r"sb_secret_[A-Za-z0-9_-]{12,}"),
    ("supabase-publishable", r"sb_publishable_[A-Za-z0-9_-]{12,}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    ("razorpay-live", r"rzp_live_[A-Za-z0-9]{8,}"),
    ("stripe-live", r"sk_live_[A-Za-z0-9]{8,}"),
    ("google-api-key", r"AIza[0-9A-Za-z_-]{30,}"),
    ("aws-access-key", r"AKIA[0-9A-Z]{16}"),
    ("pem-private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("github-token", r"gh[pousr]_[A-Za-z0-9]{30,}"),
    ("slack-token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("db-url-with-password", r"postgres(?:ql)?(?:\+psycopg)?://[^:\s/]+:([^@\s]{6,})@"),
)
COMPILED = tuple((name, re.compile(expr)) for name, expr in PATTERNS)

# Passwords in a matched DSN that are documentation, not credentials.
PLACEHOLDER_PASSWORDS = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "secret",
        "yourpassword",
        "your-password",
        "example",
        "placeholder",
        "changeme",
        "todo",
    }
)

# path fragment, pattern name, reason
ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    (
        "apps/api/tests/test_storage.py",
        "supabase-secret",
        "fake fixtures + a test function name; they assert server/browser key routing",
    ),
    (
        "apps/api/tests/test_roles_api.py",
        "supabase-secret",
        "fake fixture standing in for the secret key",
    ),
    (
        "apps/web/.env.example",
        "supabase-publishable",
        "`sb_publishable_xxxx...` placeholder; publishable keys are browser-safe by design",
    ),
    (
        ".github/workflows/ci-cd.yml",
        "supabase-publishable",
        "build-time value for the public bundle; publishable keys are browser-safe by design",
    ),
    (
        "scripts/check_secrets.py",
        "db-url-with-password",
        "the documented example DSN in this scanner's own docstring",
    ),
    (
        "apps/api/tests/test_staging_preflight.py",
        "db-url-with-password",
        "fake DSNs with a literal fake password; they assert the pooler/direct "
        "and IPv4/IPv6 fallback rules, and the password is not a credential",
    ),
    (
        "apps/api/tests/test_fill_staging_env.py",
        "db-url-with-password",
        "fake DSNs with a literal fake password; they assert the pooler/direct "
        "rules, and the password is not a credential",
    ),
)


def git(*args: str) -> subprocess.CompletedProcess[bytes]:
    # S603/S607: fixed argv, no shell, and the only variable parts are literals from
    # this file plus the operator's own --rev value - no untrusted input. `git` on
    # PATH is the documented prerequisite for running this scanner (GIT_BASELINE.md).
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no untrusted input
        ["git", *args], cwd=REPO, capture_output=True, check=False  # noqa: S607
    )


def materialize(mode: str, rev: str) -> tuple[Path | None, str]:
    """Write the target tree to a temp directory, so each file is read once.

    Reading blobs one `git show` at a time costs a subprocess per file (seconds
    on a repository this size); materialising costs one command in total.
    """
    temporary = Path(tempfile.mkdtemp(prefix="check_secrets_"))
    if mode == "worktree":
        return temporary, ""

    if mode == "staged":
        result = git("checkout-index", "-a", "-f", f"--prefix={temporary}/")
    else:
        archive = git("archive", "--format=tar", rev)
        if archive.returncode != 0:
            shutil.rmtree(temporary, ignore_errors=True)
            return None, archive.stderr.decode("utf-8", "replace").strip()
        try:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
                tar.extractall(temporary)  # noqa: S202 - repository content, not a boundary
        except tarfile.TarError as exc:  # pragma: no cover - corrupt archive
            shutil.rmtree(temporary, ignore_errors=True)
            return None, f"cannot read archive: {exc}"
        return temporary, ""

    if result.returncode != 0:
        shutil.rmtree(temporary, ignore_errors=True)
        return None, result.stderr.decode("utf-8", "replace").strip()
    return temporary, ""


def list_files(mode: str, root: Path) -> tuple[list[str] | None, str]:
    if mode == "worktree":
        result = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if result.returncode != 0:
            return None, result.stderr.decode("utf-8", "replace").strip()
        names = result.stdout.decode("utf-8", "replace").split("\0")
        return [name for name in names if name], ""

    names = [str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()]
    return sorted(names), ""


def read_file(mode: str, root: Path, path: str) -> str | None:
    """File text, or None when unreadable (also skips binary content)."""
    try:
        data = ((REPO / path) if mode == "worktree" else (root / path)).read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def harvest_live_values() -> tuple[set[str], int]:
    """Credential-shaped values from the git-ignored env files. Never printed.

    Returns the values to search for plus the count of local-development values
    that were deliberately exempted (see ``LOCAL_HOSTS``).
    """
    values: set[str] = set()
    local_exempt = 0
    for rel in ENV_FILES:
        path = REPO / rel
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if len(value) < 20 or not any(m in key.upper() for m in SECRET_NAME_MARKERS):
                continue
            if "://" in value and any(host in value for host in LOCAL_HOSTS):
                local_exempt += 1
                continue
            values.add(value)
            for segment in re.findall(r"://([^@/\s]+)@", value):
                if ":" in segment:
                    values.add(segment.split(":", 1)[1])
    return values, local_exempt


def mask(text: str) -> str:
    text = re.sub(r"(sb_(?:secret|publishable)_)[A-Za-z0-9_-]+", r"\1<MASKED>", text)
    text = re.sub(r"(eyJ)[A-Za-z0-9_-]{4,}", r"\1<MASKED>", text)
    text = re.sub(
        r"(rzp_live_|sk_live_|AIza|AKIA|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]+",
        r"\1<MASKED>",
        text,
    )
    return re.sub(r"(://[^@\s]+:)[^@\s]+@", r"\1<MASKED>@", text)


def is_local_dsn(text: str, end: int) -> bool:
    """A DSN whose host is the developer's machine.

    The password is part of the match, but the host is what follows the '@', so it
    has to be read from the text after the match rather than from the match itself.
    """
    following = text[end : end + 40]
    return (
        following.startswith("localhost:")
        or following.startswith("localhost/")
        or following.startswith("127.0.0.1:")
        or following.startswith("127.0.0.1/")
    )


def is_placeholder_value(value: str) -> bool:
    """Obvious non-secrets: repeated single characters and labelled stand-ins."""
    if len(set(value)) == 1:
        return True
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "xxx",
            "<",
            ">",
            "your",
            "replace",
            "example",
            "dummy",
            "fake",
            "placeholder",
            "changeme",
        )
    )


def allowed(path: str, pattern: str) -> str | None:
    for fragment, name, reason in ALLOWLIST:
        if name == pattern and fragment in path:
            return reason
    return None


def is_placeholder_password(password: str) -> bool:
    lowered = password.lower()
    if lowered in PLACEHOLDER_PASSWORDS:
        return True
    if len(set(password)) == 1:  # xxxxxx, ******
        return True
    return any(marker in lowered for marker in ("xxx", "...", "<", ">", "your"))


def scan(mode: str, rev: str) -> int:
    root, error = materialize(mode, rev)
    if root is None:
        print(f"      FAIL: cannot read the target: {error[:200]}")
        return 1
    try:
        files, error = list_files(mode, root)
        if files is None:
            print(f"      FAIL: cannot list files: {error[:200]}")
            return 1
        print(f"      files in scope: {len(files)}")

        values, local_exempt = harvest_live_values()
        print(
            f"[1/2] live-value scan: {len(values)} credential-shaped value(s) harvested, "
            f"{local_exempt} local-dev value(s) exempted"
        )
        live_hits: list[str] = []

        failures = 0
        allowed_hits = 0
        pattern_notes: list[str] = []

        for path in files:
            text = read_file(mode, root, path)
            if text is None:
                continue
            if values and any(value in text for value in values):
                live_hits.append(path)
            for name, expression in COMPILED:
                for match in expression.finditer(text):
                    line_number = text.count("\n", 0, match.start()) + 1
                    lines = text.splitlines()
                    line = lines[line_number - 1] if line_number <= len(lines) else ""
                    if name == "db-url-with-password":
                        if is_placeholder_password(match.group(1)):
                            allowed_hits += 1
                            pattern_notes.append(
                                f"      allowed [{name}] {path}:{line_number} - "
                                "placeholder password"
                            )
                            continue
                        if is_local_dsn(text, match.end()):
                            allowed_hits += 1
                            pattern_notes.append(
                                f"      allowed [{name}] {path}:{line_number} - "
                                "localhost-only dev credential (documented convention)"
                            )
                            continue
                    if name in {"supabase-secret", "supabase-publishable"} and (
                        is_placeholder_value(match.group(0))
                    ):
                        allowed_hits += 1
                        pattern_notes.append(
                            f"      allowed [{name}] {path}:{line_number} - placeholder value"
                        )
                        continue
                    reason = allowed(path, name)
                    if reason:
                        allowed_hits += 1
                        pattern_notes.append(f"      allowed [{name}] {path} - {reason}")
                    else:
                        failures += 1
                        pattern_notes.append(f"      FAIL [{name}] {mask(line)[:160]}")

        if live_hits:
            failures += len(live_hits)
            print(f"      FAIL: {len(live_hits)} file(s) contain a live env value:")
            for hit in live_hits:
                print(f"        {hit}")
        else:
            print("      OK: no live env value appears in the target")

        print("[2/2] pattern scan")
        for note in pattern_notes:
            print(note)
        print(f"      allow-listed fake fixtures: {allowed_hits}; unexpected matches: {failures}")
        return 1 if failures else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for credentials.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="scan the index")
    group.add_argument("--worktree", action="store_true", help="scan the working tree")
    parser.add_argument("--rev", default="HEAD", help="revision or range (default HEAD)")
    args = parser.parse_args()

    mode = "staged" if args.staged else "worktree" if args.worktree else "rev"
    label = {"staged": "the index", "worktree": "the working tree"}.get(mode, args.rev)
    print(f"check_secrets: scanning {label} in {REPO}")

    if git("rev-parse", "--git-dir").returncode != 0:
        print("check_secrets: not a git repository")
        return 2

    failures = scan(mode, args.rev)
    print("check_secrets: CLEAN" if failures == 0 else "check_secrets: REVIEW REQUIRED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
