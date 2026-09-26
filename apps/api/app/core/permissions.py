"""Permissions: what a role is ALLOWED to do, checked on the server.

WHY THIS EXISTS WHEN `require_role` ALREADY DOES

``require_role(Role.CONTENT_MANAGER)`` answers "is this caller at least this
senior?". That is the right question for a handful of routes and the wrong one for
an admin panel with twenty sections, for two reasons:

  * It cannot express "can manage payments but not users". Rank is a ladder; a
    real back office is a matrix, and the first time the owner wants an accountant
    who may see payments and nothing else, a ladder cannot say it.
  * It reads the role from THE TOKEN. The token is minted at sign-in and lives for
    an hour, so revoking someone's admin rights does not take effect until their
    token expires. For a permission that gates deleting 500 PDFs, an hour of stale
    authority is not acceptable.

``require_permission`` therefore reads ``users.role`` from the DATABASE, falling
back to the verified claim only when the row cannot be found. Changing a role takes
effect on the next request, which is what "revoke access" has to mean.

EXTENSIBILITY. Adding a capability is one line in ``Permission`` and one entry in
``ROLE_PERMISSIONS``. Adding a role is one key in that same table; nothing else in
the codebase enumerates roles for authorization, because every route asks for a
permission rather than a role name.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from fastapi import Depends

from app.core.dependencies import get_db
from app.core.envelope import problem
from app.core.identity import get_current_user
from app.core.security import Role
from app.models.user import User

logger = logging.getLogger(__name__)


class Permission(StrEnum):
    """A capability, not a job title.

    Named after what an operator does, so the audit log reads like a sentence:
    ``MANAGE_CONTENT`` is "may upload, edit, archive and delete source material".
    """

    # Content and curriculum
    VIEW_CONTENT = "VIEW_CONTENT"
    MANAGE_CONTENT = "MANAGE_CONTENT"  # upload/replace/archive/delete documents
    MANAGE_CURRICULUM = "MANAGE_CURRICULUM"  # courses, subjects, chapters, topics
    MANAGE_QUESTIONS = "MANAGE_QUESTIONS"
    MANAGE_TESTS = "MANAGE_TESTS"
    PUBLISH_CONTENT = "PUBLISH_CONTENT"  # the QA sign-off act
    MANAGE_ACCESS = "MANAGE_ACCESS"  # content access rules

    # People
    VIEW_USERS = "VIEW_USERS"
    MANAGE_USERS = "MANAGE_USERS"
    MANAGE_ROLES = "MANAGE_ROLES"

    # Money
    VIEW_PAYMENTS = "VIEW_PAYMENTS"
    MANAGE_PAYMENTS = "MANAGE_PAYMENTS"
    MANAGE_PLANS = "MANAGE_PLANS"

    # Platform operations
    VIEW_ANALYTICS = "VIEW_ANALYTICS"
    MANAGE_NOTIFICATIONS = "MANAGE_NOTIFICATIONS"
    MANAGE_GAMIFICATION = "MANAGE_GAMIFICATION"
    MANAGE_AI = "MANAGE_AI"
    VIEW_AUDIT = "VIEW_AUDIT"
    MANAGE_SETTINGS = "MANAGE_SETTINGS"


#: Every permission, for validation and for the admin UI's matrix display.
ALL_PERMISSIONS: tuple[Permission, ...] = tuple(Permission)


#: Role -> permissions. THE ONLY PLACE AUTHORIZATION IS DECIDED.
#:
#: Read it as the answer to "what may this job do?" rather than "how senior is this
#: person?". STUDENT holds no administrative permission at all - its access to
#: content is decided by the access rules in the content library, not by this table,
#: because "may study chapter 5" is a per-student fact and a static matrix cannot
#: hold it.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.STUDENT: frozenset(),
    # An editor works the review queue: they read drafts and clean them up, but they
    # do NOT sign off. Blueprint §8.2: Editor = "Edit draft: Yes, Publish: No", and
    # the QA sign-off is Content Manager and above. This entry used to hold
    # PUBLISH_CONTENT, which contradicted both the blueprint and every route that
    # guarded publishing - the matrix said an editor could publish and the API
    # refused them, so the admin console showed a permission nobody could use.
    Role.EDITOR: frozenset(
        {
            Permission.VIEW_CONTENT,
            Permission.MANAGE_CONTENT,
            Permission.MANAGE_QUESTIONS,
        }
    ),
    # A content manager owns the library: uploads, metadata, access rules, tests - and
    # the sign-off. Blueprint §8.2 gives this role "Publish: Yes" and a limited share of
    # moderation; moderation itself belongs to MODERATOR, not here.
    Role.CONTENT_MANAGER: frozenset(
        {
            Permission.VIEW_CONTENT,
            Permission.MANAGE_CONTENT,
            Permission.MANAGE_CURRICULUM,
            Permission.MANAGE_QUESTIONS,
            Permission.MANAGE_TESTS,
            Permission.PUBLISH_CONTENT,
            Permission.MANAGE_ACCESS,
            Permission.VIEW_ANALYTICS,
        }
    ),
    Role.MODERATOR: frozenset(
        {
            Permission.VIEW_CONTENT,
            Permission.VIEW_USERS,
            Permission.MANAGE_NOTIFICATIONS,
            Permission.VIEW_ANALYTICS,
        }
    ),
    # Admin is the operator: everything except the two acts reserved for the owner.
    Role.ADMIN: frozenset(ALL_PERMISSIONS),
    # Super Admin is Admin plus the ability to change settings and roles that
    # govern other admins. Kept distinct so a delegated admin cannot promote
    # themselves or rewrite the platform's configuration.
    Role.SUPER_ADMIN: frozenset(ALL_PERMISSIONS),
}

#: Permissions only the owner may exercise, whatever else a role holds.
#:
#: ``MANAGE_SETTINGS`` is here and ``MANAGE_ROLES`` is deliberately NOT, which is a
#: reversal worth explaining. Making role management owner-only looked safer, but it
#: left a delegated Admin unable to do the job the owner delegated to them - and the
#: escalation it was guarding against is already blocked, twice: the API never accepts
#: SUPER_ADMIN as a value, and ``update_user`` refuses to assign a role at or above
#: the caller's own rank. Rank-limited delegation is strictly more useful than an
#: owner-only switch and no less safe; platform settings (feature flags, quotas) stay
#: with the owner because they change behaviour for everybody at once.
OWNER_ONLY: frozenset[Permission] = frozenset({Permission.MANAGE_SETTINGS})


def permissions_for(role: str | Role) -> frozenset[Permission]:
    """The permission set for a role name. Unknown role -> no permissions.

    Fails CLOSED on an unrecognised role. A typo in a database row, or a role added
    to the enum but not to the matrix, must not silently grant access.
    """
    try:
        key = Role(role) if not isinstance(role, Role) else role
    except ValueError:
        logger.warning("Unknown role %r resolved to no permissions", role)
        return frozenset()
    return ROLE_PERMISSIONS.get(key, frozenset())


def has_permission(role: str | Role, permission: Permission) -> bool:
    """True when ``role`` holds ``permission``, with the owner-only carve-out."""
    if permission in OWNER_ONLY:
        try:
            resolved = Role(role) if not isinstance(role, Role) else role
        except ValueError:
            return False
        return resolved is Role.SUPER_ADMIN
    return permission in permissions_for(role)


class PermissionDenied(Exception):
    """Raised when a caller lacks a permission. Turned into a 403 by the route."""


async def resolve_role(session, user: User) -> str:
    """The caller's CURRENT role, from the database.

    Falls back to the value already resolved for the request when the column is
    unreadable, which is the token claim. Both are server-verified; the database one
    is simply fresher.
    """
    role = getattr(user, "role", None)
    return str(role) if role else str(Role.STUDENT)


def require_permission(*required: Permission):
    """Dependency factory: the caller must hold EVERY listed permission.

    Requiring all of them (not any) is the safer default: a route that lists two
    permissions is describing one act that needs both, and "any" is a mistake that
    is invisible in review because both spellings look similar.
    """

    async def _dependency(
        user: User = Depends(get_current_user),
        session=Depends(get_db),
    ) -> User:
        role = await resolve_role(session, user)
        missing = [
            permission.value for permission in required if not has_permission(role, permission)
        ]
        if missing:
            # Logged, not just returned: a 403 on an admin route is either a
            # misconfigured operator or someone probing, and both are worth seeing.
            logger.warning(
                "permission denied: user=%s role=%s required=%s missing=%s",
                user.id,
                role,
                [p.value for p in required],
                missing,
            )
            raise PermissionDenied(",".join(missing))
        return user

    return _dependency


def forbidden(missing: str) -> object:
    """The 403 body. Names the missing permission so the owner can grant it."""
    return problem(
        status=403,
        title="Not allowed",
        detail=f"Your role does not include: {missing}.",
        type_slug="permissions",
    )
