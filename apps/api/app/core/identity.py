"""Resolving a verified principal to a platform user row.

THE FIRST REQUEST FROM A NEW ACCOUNT HAS TO WORK. Supabase proves who someone is;
it knows nothing about this platform. Until their `users` row exists there is no
user id to attach progress, entitlements or doubts to, so a synchronous
"provision on first authenticated request" is the only flow that does not require
a separate sign-up screen that asks again for information Google already sent.

It is safe precisely because the input is the VERIFIED token: the email and uid
come from Google's signature, not from the request body. Provisioning from
client-supplied fields would let anyone mint an account with any email.

The alternative - an onboarding form that POSTs to /users - has the same effect
with one more step, and the step is the one that loses users.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.models.user import User
from app.repositories.users import SqlUserRepository

#: Roles that may answer doubts, review content and see other students' threads.
#: Mirrors `app.lib.roles` in the frontend - the two lists are asserted equal by a
#: test, because a role that exists on one side only is a permissions bug.
STAFF_ROLES = frozenset({"EDITOR", "CONTENT_MANAGER", "MODERATOR", "ADMIN", "SUPER_ADMIN"})


def is_staff(user: User) -> bool:
    """Staff status comes from the DATABASE row, not the token claim.

    The token claim is what ``require_role`` uses, and it can only be set by the
    Supabase Admin API, which needs a secret this deployment may not hold. The
    database row is already the authoritative record of a user's role, and a
    moderator whose token predates their promotion should not be locked out of the
    queue they were promoted to work.
    """
    return user.role in STAFF_ROLES


async def get_current_user(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """The platform user behind the verified token, created on first sight."""
    repo = SqlUserRepository()
    user = await repo.get_by_auth_id(principal.auth_user_id)
    if user is not None:
        return user

    # A BARRED ACCOUNT IS NOT A NEW ACCOUNT.
    #
    # `get_by_auth_id` hides suspended and erased rows, so without this probe the
    # code below would treat a suspended student as a first-time sign-in. The row
    # still occupies the unique index on `auth_user_id`, so the insert would either
    # fail or - the more dangerous reading - look like a race and hand back the
    # suspended row. Either way the suspension would be undoable by the suspended
    # person simply signing in again, which is the definition of a control that
    # does nothing.
    barred = await _barred_reason(repo, principal.auth_user_id)
    if barred is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=barred)

    user = await repo.provision(
        session,
        auth_user_id=principal.auth_user_id,
        email=principal.email,
        is_admin_email=(
            principal.email is not None and settings.is_bootstrap_admin_email(principal.email)
        ),
    )
    # Checked AGAIN, because `provision` may have returned a row it did not
    # create: when two requests race for a new account, the loser recovers by
    # re-reading the winner, and the winner may be a barred row. A check that only
    # ran before the insert would be skipped on exactly that path.
    barred = _barred_reason_on(user)
    if barred is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=barred)

    # COMMITTED HERE, not by the route. The route may fail after this point (a
    # missing chapter id, a validation error), and the account would still not
    # exist - so every subsequent request would provision again, forever.
    await session.commit()
    return user


def _barred_reason_on(user: User) -> str | None:
    """Why this row must not be signed in as, or None if it may be."""
    if user.deleted_at is not None:
        return (
            "This account was deleted. Sign up again if you want to start over - the "
            "previous account's data is not recoverable."
        )
    if not user.is_active:
        return (
            "This account has been deactivated. Contact support if you believe that is a mistake."
        )
    return None


async def _barred_reason(repo: SqlUserRepository, auth_user_id: str) -> str | None:
    """Look past the repository's own filters to see if a barred row exists.

    Only ever called when the ordinary lookup found nothing, so the extra query
    costs nothing on the happy path and cannot turn a normal sign-in into an error.
    """
    row = await repo.get_by_auth_id(auth_user_id, include_inactive=True, include_deleted=True)
    return _barred_reason_on(row) if row is not None else None


async def get_optional_user(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> User | None:
    """For endpoints that work for anonymous callers too. Never provisions."""
    return await SqlUserRepository().get_by_auth_id(principal.auth_user_id)
