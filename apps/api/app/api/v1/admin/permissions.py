"""The owner's back office: platform numbers, people, permissions, settings, logs.

WHAT THIS FILE IS FOR

Everything here answers a question the owner has to answer without a database client:
"What is the platform doing?", "who is on it?", "what may they do?", "who changed
this?", and "turn that feature off". Each route requires a PERMISSION rather than a
role, so a delegated accountant can be given payments and nothing else - see
``app/core/permissions.py`` for why that distinction exists and what it costs.

WHY THE PERMISSION CHECKS READ THE DATABASE

``require_permission`` resolves the caller's role from ``users.role`` on every
request instead of trusting the token claim alone. An access token lives for an hour;
"revoke this person's admin access" must not mean "it takes effect within an hour",
especially when the permission being revoked gates deleting the entire library.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.dependencies import get_request_id
from app.core.envelope import success
from app.core.permissions import (
    ALL_PERMISSIONS,
    OWNER_ONLY,
    ROLE_PERMISSIONS,
    Permission,
    permissions_for,
    require_permission,
)
from app.core.security import ROLE_RANK, Role
from app.models.user import User

from ._shared import (
    ASSIGNABLE_ROLES,
)

router = APIRouter(tags=["admin"])

"""The role/permission matrix and the caller own permissions."""


@router.get("/admin/permissions", summary="The role/permission matrix")
async def permission_matrix(
    _actor: User = Depends(require_permission(Permission.VIEW_USERS)),
) -> Any:
    """What each role may do, as the SERVER computes it.

    This endpoint exists so the admin UI never hard-codes the matrix. A front-end copy
    of "Editors can publish" is a comment that compiles, and it is wrong the first
    time a permission moves.
    """
    return success(
        {
            "roles": [
                {
                    "role": role.value,
                    "rank": ROLE_RANK[role],
                    "assignable": role.value in ASSIGNABLE_ROLES,
                    "permissions": sorted(p.value for p in ROLE_PERMISSIONS.get(role, frozenset())),
                }
                for role in Role
            ],
            "permissions": [
                {"key": permission.value, "ownerOnly": permission in OWNER_ONLY}
                for permission in ALL_PERMISSIONS
            ],
            "note": (
                "Permissions are enforced on the server on every request; this list is for display."
            ),
        },
        request_id=get_request_id(),
    )


@router.get("/admin/me/permissions", summary="What the caller may do")
async def my_permissions(
    actor: User = Depends(require_permission(Permission.VIEW_CONTENT)),
) -> Any:
    """Drives the admin navigation.

    The sidebar hides sections this list does not contain, and every one of those
    sections is also refused by its own route - the list is a convenience, not the
    control.
    """
    granted = permissions_for(actor.role)
    return success(
        {
            "role": actor.role,
            "permissions": sorted(p.value for p in granted),
            "isOwner": actor.role == Role.SUPER_ADMIN.value,
        },
        request_id=get_request_id(),
    )
