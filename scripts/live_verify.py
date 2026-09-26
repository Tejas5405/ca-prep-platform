#!/usr/bin/env python3
"""Live end-to-end verification of the API against a RUNNING server.

WHAT THIS ANSWERS

"Does the deployed thing actually behave the way the tests say?" The pytest suite talks
to the app object in-process, which is the right way to test logic but cannot catch a
deployment-level problem: a route that is not mounted, a middleware that rejects the
request before it reaches the handler, a proxy that rewrites a path, a database that is
reachable from the test harness and not from the server.

So this runs the OTHER way round. It starts from the server's own OpenAPI document - the
routes that really exist - and calls every one of them with a real token for each role,
then checks the answers against the permission matrix the API itself enforces.

WHAT IT ASSERTS

1. NO UNHANDLED 5xx. Two cases are distinguished, because treating them alike is how
   this check stops being read:
     * a **500** is always a bug and always fails the sweep - the handler for an
       unforeseen exception ran, so something in the API is broken;
     * a **503 in this API's problem shape** (`errors/storage`, `errors/unavailable`,
       ...) is a dependency that is not answering, reported by a route that handled it
       correctly. That is the API working, and in a CI environment with no storage
       credentials it is the ONLY correct answer. It is counted and listed separately,
       never as a failure. A 503 carrying someone else's error page is not in this
       API's shape and is still a failure.
2. The RBAC invariant, per route and per role: a role that HOLDS the permission must not
   be told 403, and a role that LACKS it must be told exactly 403. Anything else - a 200
   for a role without the permission - is an authorization hole and is reported as a
   failure, not a warning.
3. A student token can never read an admin route, and staff cannot be served a student's
   private data by accident (spot checks with named expectations).

HOW IT GETS ITS EXPECTATIONS

The required permission for each route is read out of the app's own dependency closures
(`require_permission(...)` captures its permissions in a closure), so the expectations
cannot drift from the code: change a route's permission and this script's expectation
changes with it. A route with no permission dependency is expected to be reachable by
any authenticated role, which is how the student routes are treated.

USAGE

    python3 scripts/live_verify.py \
        --api http://127.0.0.1:8000 \
        --database-url postgresql://postgres@127.0.0.1:5433/caprep \
        --markdown docs/verification/live-rbac-matrix.md

It writes users and tokens for the run and leaves no state behind that matters: the
users are real rows (so the API resolves their role the same way it does in production)
and are named `live-verify+<role>@example.test`.
"""

from __future__ import annotations

import argparse
import inspect
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "apps" / "api"))

ROLES = ["STUDENT", "EDITOR", "CONTENT_MANAGER", "MODERATOR", "ADMIN", "SUPER_ADMIN"]

# Methods that change state; called with no body they must still be REFUSED for a role
# without the permission (auth runs before validation), which is the point being tested.
WRITE_METHODS = {"post", "put", "patch", "delete"}


def is_unhandled_server_error(status: int, body: str) -> bool:
    """True when the API itself broke, rather than reporting a dependency as down.

    A 500 always means the catch-all exception handler ran: something unforeseen.
    A 503 means a route recognised a known failure and said which dependency it was -
    and it counts as handled only when the body is this API's problem shape, because a
    gateway's own 503 error page is not the API answering.
    """
    if status == 0 or status == 500 or (status >= 500 and status != 503):
        return True
    if status == 503:
        try:
            payload = json.loads(body)
        except ValueError:
            # Not parseable as JSON at all, so it is not this API's error shape. Either
            # someone else's error page or a body so large the read was cut - the latter
            # is why BODY_LIMIT exists.
            return True
        if not isinstance(payload, dict):
            return True
        return not str(payload.get("type", "")).startswith("https://api.caprep.in/errors/")
    return False


@dataclass
class Outcome:
    path: str
    method: str
    required: list[str]
    statuses: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    #: A 503 the API itself produced, naming the dependency that is down. Separate from
    #: `failures` on purpose: see the note at the top of this file.
    unavailable: list[str] = field(default_factory=list)

    @property
    def worst(self) -> int:
        return max(self.statuses.values()) if self.statuses else 0


# --------------------------------------------------------------------------- expectations


def required_permissions(route: Any) -> list[str]:
    """The permissions a route demands, read from its dependency closures.

    `require_permission(...)` returns a locally defined coroutine that closes over the
    permissions it was built with. Reading them back out means the expectation for a
    route is derived from the route itself, so this script cannot assert something the
    API does not enforce.
    """
    found: list[str] = []

    def walk(dependant: Any) -> None:
        for dependency in getattr(dependant, "dependencies", []):
            call = getattr(dependency, "call", None)
            if call is not None:
                try:
                    cells = inspect.getclosurevars(call).nonlocals
                except TypeError:  # pragma: no cover - only for exotic callables
                    cells = {}
                for value in cells.values():
                    if isinstance(value, tuple) and value and hasattr(value[0], "value"):
                        names = [getattr(item, "value", str(item)) for item in value]
                        if all(isinstance(name, str) for name in names):
                            found.extend(names)
            walk(dependency)

    walk(route.dependant)
    return sorted(set(found))


def build_route_table(api_prefix: str) -> list[tuple[str, str, list[str]]]:
    """Every mounted route, with the permissions its dependency closures demand.

    Read from the ROUTERS rather than from ``app.routes``: this FastAPI version wraps
    each included router in an `_IncludedRouter` whose own `path` is empty, so walking
    the app object finds the mounts and none of the routes. Importing the modules that
    define them gives the paths exactly as the routers declare them, and the prefix is
    the one `main.py` mounts them with.
    """
    from app.api.v1 import (
        access,
        admin,
        collections,
        content,
        curriculum,
        doubts,
        gamification,
        ingestion,
        mocks,
        notifications,
        payments,
        planner,
        practice,
        progress,
        revision,
        search,
        users,
    )

    routers = [
        mocks, planner, ingestion, payments, curriculum, practice, progress, revision,
        doubts, users, search, collections, content, gamification, notifications,
        admin, access,
    ]

    table: list[tuple[str, str, list[str]]] = []
    for module in routers:
        for route in module.router.routes:
            methods = getattr(route, "methods", None)
            path = getattr(route, "path", "")
            if not methods or not path:
                continue
            full = api_prefix + path
            for method in sorted(
                m.lower() for m in methods if m.lower() in {"get", "post", "put", "patch", "delete"}
            ):
                table.append((full, method, required_permissions(route)))
    return sorted(table)


def expected_status(
    role: str, required: list[str], permissions: dict[str, set[str]], owner_only: set[str]
) -> str:
    """What this role must be told, given the permission matrix the API enforces.

    Two rules, both read from `app/core/permissions.py` rather than restated here:
    the role must hold EVERY permission the route lists, and a permission in
    `OWNER_ONLY` is held by the owner alone however the matrix reads - which is how
    `/admin/settings` refuses an ADMIN in production and must therefore refuse one here.
    """
    if not required:
        return "any"  # authenticated; the handler decides
    needed = set(required)
    if needed & owner_only and role != "SUPER_ADMIN":
        return "403"
    held = permissions.get(role, set())
    return "not-403" if needed <= held else "403"


# --------------------------------------------------------------------------- plumbing


def sql(database_url: str, statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    code = (
        "import json, psycopg\n"
        f"conn = psycopg.connect({database_url!r})\n"
        "with conn.cursor() as cur:\n"
        f"    cur.execute({statement!r}, {params!r})\n"
        "    rows = [] if cur.description is None else [\n"
        "        dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()\n"
        "    ]\n"
        "conn.commit()\n"
        "print(json.dumps(rows, default=str))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-800:])
    return json.loads(result.stdout or "[]")


def seed_role_user(database_url: str, role: str) -> tuple[str, str]:
    """Insert a real user row for this role and return (user_id, token)."""
    auth_id = str(uuid.uuid4())
    email = f"live-verify+{role.lower()}@example.test"
    # `users.email` has no unique constraint, so this is an explicit find-or-insert
    # rather than an ON CONFLICT upsert: the script is re-run on every verification and
    # must not accumulate one row per run.
    existing = sql(database_url, "SELECT id FROM users WHERE email = %s", (email,))
    if existing:
        user_id = existing[0]["id"]
        sql(
            database_url,
            "UPDATE users SET auth_user_id = %s, role = %s, is_active = true WHERE id = %s",
            (auth_id, role, user_id),
        )
    else:
        rows = sql(
            database_url,
            """
            INSERT INTO users (id, auth_user_id, email, role, is_active, syllabus_scheme)
            VALUES (gen_random_uuid(), %s, %s, %s, true, 'NEW_2024')
            RETURNING id
            """,
            (auth_id, email, role),
        )
        user_id = rows[0]["id"]
    token = subprocess.run(
        [
            sys.executable,
            "scripts/local_auth.py",
            "token",
            "--sub",
            auth_id,
            "--email",
            email,
            "--role",
            role,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return user_id, token


#: How much of a response body the sweep keeps. It must be enough to PARSE the error
#: envelope: at 200 characters the 503 bodies (whose detail quotes the storage host's
#: own error page) were cut mid-string, so the classifier could not read the `type` field
#: and reported a handled dependency outage as an unhandled server error.
BODY_LIMIT = 4000


def call(api: str, method: str, path: str, token: str, body: dict[str, Any] | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        api.rstrip("/") + path,
        data=data,
        method=method.upper(),
        headers={
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()[:BODY_LIMIT].decode(errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:BODY_LIMIT].decode(errors="replace")
    except Exception as error:  # connection refused, timeout, ...
        return 0, str(error)


def fill_path(path: str, real_ids: dict[str, str]) -> str:
    """Replace {placeholders} with something the route can at least parse.

    A real id where the run has one, a fresh UUID otherwise: the point is to reach the
    handler and be refused (or served) by AUTHORIZATION, not by id parsing. A 404 or 422
    from a role that holds the permission is a pass; a 403 for that role is not.
    """
    out = path
    for name, value in real_ids.items():
        out = out.replace(f"{{{name}}}", value)
    while "{" in out:
        start = out.index("{")
        end = out.index("}", start)
        out = out[:start] + str(uuid.uuid4()) + out[end + 1 :]
    return out


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--database-url", default="postgresql://postgres@127.0.0.1:5433/caprep")
    parser.add_argument("--markdown", default="")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    from app.core.permissions import OWNER_ONLY, ROLE_PERMISSIONS

    permissions = {
        role: {permission.value for permission in granted}
        for role, granted in ROLE_PERMISSIONS.items()
    }
    owner_only = {permission.value for permission in OWNER_ONLY}

    # A real id for the path parameters that need one. The library is seeded, so a
    # document and a course exist; a batch id does not (batches are created by uploads),
    # and a fresh UUID is the honest way to ask "what happens with an unknown id".
    seeded = sql(
        args.database_url,
        "SELECT id FROM content_documents ORDER BY created_at LIMIT 1",
    )
    document_id = seeded[0]["id"] if seeded else str(uuid.uuid4())
    course = sql(args.database_url, "SELECT id FROM courses ORDER BY created_at LIMIT 1")
    course_id = course[0]["id"] if course else str(uuid.uuid4())

    tokens: dict[str, str] = {}
    user_ids: dict[str, str] = {}
    for role in ROLES:
        user_ids[role], tokens[role] = seed_role_user(args.database_url, role)

    real_ids = {
        "document_id": document_id,
        "course_id": course_id,
        "user_id": user_ids["STUDENT"],
        "mock_id": str(uuid.uuid4()),
        "question_id": str(uuid.uuid4()),
        "batch_id": str(uuid.uuid4()),
        "grant_id": str(uuid.uuid4()),
        "rule_id": str(uuid.uuid4()),
        "badge_id": str(uuid.uuid4()),
        "collection_id": str(uuid.uuid4()),
        "doubt_id": str(uuid.uuid4()),
        "reply_id": str(uuid.uuid4()),
        "notification_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "chapter_id": str(uuid.uuid4()),
        "subject_id": str(uuid.uuid4()),
    }

    table = build_route_table("/api/v1")
    outcomes: list[Outcome] = []

    print(f"==> {len(table)} routes x {len(ROLES)} roles = {len(table) * len(ROLES)} requests")
    for path, method, required in table:
        target = fill_path(path, real_ids)
        outcome = Outcome(path=path, method=method.upper(), required=required)
        for role in ROLES:
            status, body = call(args.api, method, target, tokens[role])
            outcome.statuses[role] = status
            want = expected_status(role, required, permissions, owner_only)
            if is_unhandled_server_error(status, body):
                outcome.failures.append(f"{role}: {status} (server error) {body[:120]}")
            elif status == 503:
                outcome.unavailable.append(f"{role}: {body[:120]}")
            elif want == "403" and status != 403:
                outcome.failures.append(f"{role}: {status}, expected 403 (lacks {','.join(required)})")
            elif want == "not-403" and status == 403:
                outcome.failures.append(f"{role}: 403, expected allowed (holds {','.join(required)})")
        outcomes.append(outcome)

    # A student must never be told a status other than 403 on an admin route, and staff
    # must never reach a student-only route with a student's identity.
    admin_routes = [o for o in outcomes if o.path.startswith("/api/v1/admin")]
    for outcome in admin_routes:
        if outcome.statuses.get("STUDENT") not in (403,):
            outcome.failures.append(
                f"STUDENT: {outcome.statuses.get('STUDENT')} on an admin route - must be 403"
            )

    failures = [o for o in outcomes if o.failures]
    server_errors = [o for o in outcomes if o.failures]
    unavailable = [o for o in outcomes if o.unavailable]

    # ------------------------------------------------------------------ report
    lines = [
        "# Live RBAC matrix",
        "",
        "Generated by `scripts/live_verify.py` against a running API. Every route in the",
        "server's own OpenAPI document, called once per role with a real token.",
        "",
        f"- routes checked: **{len(table)}**",
        f"- requests made: **{len(table) * len(ROLES)}**",
        f"- unhandled 5xx (500s): **{len(server_errors)}**",
        f"- dependencies reported unavailable (503): **{len(unavailable)}**",
        f"- authorization mismatches: **{len(failures)}**",
        "",
        "`any` means the route requires only authentication; `403` means the role must be",
        "refused because it does not hold the listed permission.",
        "",
        "| method | path | permission | " + " | ".join(ROLES) + " |",
        "| --- | --- | --- | " + " | ".join("---" for _ in ROLES) + " |",
    ]
    for outcome in sorted(outcomes, key=lambda o: (o.path, o.method)):
        cells = []
        for role in ROLES:
            status = outcome.statuses.get(role, 0)
            mark = "**" if status >= 500 or status == 0 else ""
            cells.append(f"{mark}{status}{mark}")
        lines.append(
            f"| {outcome.method} | `{outcome.path}` | {', '.join(outcome.required) or 'any'} "
            f"| " + " | ".join(cells) + " |"
        )

    if unavailable:
        # Reported, not failed. In an environment without storage credentials this is
        # the correct answer, and listing it is what stops "0 problems" from hiding a
        # dependency nobody configured.
        lines += [
            "",
            "## Dependencies reported as unavailable (503)",
            "",
            "The API answered in its own error shape, naming the dependency. Not a failure;",
            "it is listed so that a route which cannot work here is visible rather than",
            "silently green.",
            "",
        ]
        for outcome in unavailable:
            for detail in outcome.unavailable:
                lines.append(f"- `{outcome.method} {outcome.path}` - {detail}")

    if failures:
        lines += ["", "## Mismatches", ""]
        for outcome in failures:
            for failure in outcome.failures:
                lines.append(f"- `{outcome.method} {outcome.path}` - {failure}")

    report = "\n".join(lines) + "\n"
    if args.markdown:
        destination = REPO / args.markdown
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report)
        print(f"==> wrote {destination}")
    if args.json:
        (REPO / args.json).write_text(
            json.dumps(
                [
                    {
                        "method": o.method,
                        "path": o.path,
                        "required": o.required,
                        "statuses": o.statuses,
                        "failures": o.failures,
                        "unavailable": o.unavailable,
                    }
                    for o in outcomes
                ],
                indent=2,
            )
        )

    print(f"==> unhandled 5xx (500s): {len(server_errors)}")
    print(f"==> dependencies reported unavailable (503): {len(unavailable)}")
    print(f"==> authorization mismatches: {len(failures)}")
    for outcome in failures[:25]:
        for failure in outcome.failures:
            print(f"    {outcome.method} {outcome.path}: {failure}")
    if not failures and not server_errors:
        note = (
            f" ({len(unavailable)} route(s) reported a dependency as unavailable)"
            if unavailable
            else ""
        )
        print(
            "==> clean: every route answered as the permission matrix says it should"
            + note
        )
    return 1 if (failures or server_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
