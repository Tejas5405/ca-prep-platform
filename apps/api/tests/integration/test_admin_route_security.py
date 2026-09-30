"""Every /api/v1/admin route must be permission-gated. Enforced, not reviewed.

THE PROPERTY

A route under `/api/v1/admin/` that is missing its `require_permission`
dependency is unauthenticated. It is not "less protected" - it is open, and it
reads or writes the platform's users, roles, billing history and content.

WHY A TEST AND NOT A REVIEW CHECKLIST

The surface is 180 routes and growing. "Did you remember the dependency?" is a
question a human answers well once and badly the four hundredth time, and the
failure is invisible in review because an ungated route looks exactly like a
gated one at the point of definition - the difference is an omission, not an
error.

So the property is asserted against the assembled application, which means it
also covers routes added by future refactors. Splitting a module cannot lose a
dependency, because the test never reads a source file.

HOW IT RECOGNISES A GATE

`require_permission(...)` returns a fresh closure per call, so two routes using
it are different objects that compare unequal to anything. The closure carries
`__caprep_permission_gate__` precisely so this question is answerable. See the
comment in `app/core/permissions.py`.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from app.main import app

pytestmark = pytest.mark.postgres

#: Marker attribute set by `require_permission` on the closure it returns.
GATE_MARKER = "__caprep_permission_gate__"

ADMIN_PREFIX = "/api/v1/admin/"

#: Routes under /admin that are intentionally open, with the reason.
#:
#: EMPTY ON PURPOSE. If one is ever added it must be a line here naming why,
#: because the whole value of this test is that "no exceptions" is the default
#: and an exception is a deliberate, reviewed act.
ALLOWED_UNGATED: dict[str, str] = {}


def _iter_routes(application: FastAPI) -> list[tuple[str, APIRoute]]:
    """Every APIRoute with its FULL public path, including nested sub-routers.

    Two FastAPI details make the obvious version of this silently return
    NOTHING, which is why `test_the_walk_actually_finds_admin_routes` exists:

      1. `app.routes` holds `_IncludedRouter` wrappers, not `APIRoute` objects, so
         a naive walk sees 26 entries and no routes.
      2. Reaching through `original_router` is not enough either - those routes
         carry PRE-prefix paths (`/admin/audit`, not `/api/v1/admin/audit`),
         because the prefix is applied when the router is included. Matching on
         `/api/v1/admin/` against them finds nothing.

    So the walk carries the include prefix down and joins it. This is the same
    path a client calls, which is the only definition of "an admin route" worth
    asserting on.
    """

    def walk(routes: object, prefix: str) -> list[tuple[str, APIRoute]]:
        found: list[tuple[str, APIRoute]] = []
        for route in routes or []:  # type: ignore[union-attr]
            if isinstance(route, APIRoute):
                found.append((f"{prefix}{route.path}", route))
                continue
            if isinstance(route, APIRouter):
                found.extend(walk(route.routes, prefix))
                continue
            original = getattr(route, "original_router", None)
            if original is not None:
                context = getattr(route, "include_context", None)
                nested = getattr(context, "prefix", "") or ""
                found.extend(walk(original.routes, f"{prefix}{nested}"))
        return found

    return walk(application.routes, "")


def _is_gated(route: APIRoute) -> bool:
    """True when the route carries a `require_permission` dependency.

    Two different objects can hold the callable, and checking only one produces
    FALSE NEGATIVES against real, gated routes:

      * `route.dependencies` holds `params.Depends` objects, where the callable
        is `.dependency`. This is empty for a dependency declared as a function
        parameter default - which is how every route in the codebase declares it.
      * `route.dependant.dependencies` holds flattened `Dependant` objects, where
        the callable is `.call`. This is where a parameter-default `Depends`
        actually lands.

    An earlier version of this test read only `.dependency` and reported ten
    genuinely gated admin routes as open - a guard that cries wolf gets turned
    off, so both accessors are checked.
    """
    candidates: list[object] = []
    for dependency in route.dependencies or []:
        candidates.append(getattr(dependency, "dependency", None))
    for dependant in route.dependant.dependencies or []:
        candidates.append(getattr(dependant, "call", None))
    return any(call is not None and hasattr(call, GATE_MARKER) for call in candidates)


def test_the_walk_actually_finds_admin_routes() -> None:
    """A guard against the guard.

    If the traversal silently stopped resolving sub-routers, the test below
    would pass having asserted nothing. This pins a non-trivial count, so a
    regression in the walk fails LOUDLY instead of turning the security control
    into a no-op that still reports success.
    """
    admin = [path for path, _ in _iter_routes(app) if path.startswith(ADMIN_PREFIX)]

    assert len(admin) >= 25, f"only found {len(admin)} admin routes; the walk is broken"
    assert len(set(admin)) >= 15, "implausibly few distinct admin paths"


def test_every_admin_route_is_permission_gated() -> None:
    ungated: list[str] = []

    for path, route in _iter_routes(app):
        if not path.startswith(ADMIN_PREFIX):
            continue
        if path in ALLOWED_UNGATED:
            continue
        if not _is_gated(route):
            methods = ",".join(sorted(route.methods or ()))
            ungated.append(f"{methods} {path}")

    assert not ungated, (
        "UNGATED ADMIN ROUTES - each is reachable without any permission check:\n  "
        + "\n  ".join(sorted(ungated))
        + "\n\nFix: add Depends(require_permission(Permission.XXX)) to each, or - if "
        "one genuinely must be open - add it to ALLOWED_UNGATED with a reason."
    )


def test_no_route_below_admin_prefix_escapes_the_prefix_check() -> None:
    """`/api/v1/administrators` must not be swept in, and `/api/v1/admin`
    without the trailing slash must not be missed.

    The prefix is compared exactly as written, so a future route at
    `/api/v1/admin-x` is not falsely reported - a guard that cries wolf gets
    disabled.
    """
    paths = {path for path, _ in _iter_routes(app)}

    for path in paths:
        if path.startswith("/api/v1/admin") and not path.startswith(ADMIN_PREFIX):
            pytest.fail(
                f"{path} looks like an admin route but is outside the checked "
                f"prefix {ADMIN_PREFIX!r}; the guard would silently skip it"
            )
