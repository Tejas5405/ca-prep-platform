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

import logging

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.models.user import User
from app.repositories.users import SqlUserRepository

logger = logging.getLogger(__name__)

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


#: Claims that can carry Supabase's email-confirmation state, most specific first.
#:
#: Supabase does NOT put an `email_verified` claim in an access token. Its
#: documented claims are iss/aud/exp/iat/sub/role/aal/session_id/email/phone/
#: is_anonymous, with app_metadata and user_metadata as optional bags. The
#: authoritative "is this address confirmed" fact lives on the USER OBJECT as
#: `email_confirmed_at` (null means not confirmed), which is not in the token.
#:
#: So these are checked in order of trustworthiness, and all of them are treated
#: as evidence only:
#:
#:   * `email_confirmed_at` - the real field, if a hook or a custom claim carries it.
#:   * `email_verified`      - what a custom access-token hook most often adds, and
#:                              what the milestone brief names. Honoured ONLY when
#:                              it is the boolean True: a string "false", the int 0,
#:                              or any other value is not a confirmation.
#:   * `app_metadata.email_verified` / `.email_confirmed_at` - server-written, and
#:                              therefore trusted ahead of user_metadata.
#:
#: `user_metadata` is deliberately NOT consulted. Supabase states it is editable by
#: the user without checks, so a claim read from it would be self-asserted by the very
#: person trying to become an administrator.
_CONFIRMATION_CLAIMS = ("email_confirmed_at", "email_verified")


def email_is_confirmed(claims: dict[str, object] | None) -> bool:
    """Whether the verified token proves this address was confirmed.

    FAIL CLOSED. Every outcome other than positive, unambiguous evidence of
    confirmation returns False, because the caller's decision is a privilege
    grant: an absent claim, a null claim, a malformed claim, or a claim naming
    something this function does not recognise must all read as "not proven".

    The narrow reading of `email_verified` is deliberate. ``bool("false")`` is
    True in Python, and ``bool(0)`` is False, so a naive truthiness test would
    read the STRING "false" as a confirmation - turning the one value an attacker
    is most likely to be able to influence into a grant. Only a real boolean True
    counts, and a non-empty `email_confirmed_at` string counts because that is the
    shape Supabase actually returns.
    """
    if not isinstance(claims, dict):
        return False

    def _bag(name: str) -> dict[str, object]:
        value = claims.get(name)
        return value if isinstance(value, dict) else {}

    sources: tuple[dict[str, object], ...] = (claims, _bag("app_metadata"))

    for source in sources:
        for name in _CONFIRMATION_CLAIMS:
            if name not in source:
                continue
            value = source[name]
            if value is None or value is False:
                continue
            if value is True:
                return True
            # A STRING is a confirmation ONLY for the timestamp claim, which is
            # what Supabase actually returns. It is deliberately NOT accepted for
            # `email_verified`: a boolean field arriving as the string "false" is a
            # plausible shape for a custom hook or a hand-rolled claim, and
            # treating any non-empty string as a confirmation would promote on the
            # one value an attacker has the most reason to arrange. A stricter
            # reading here fails closed, which is the correct direction to be
            # wrong in.
            if name == "email_confirmed_at" and isinstance(value, str) and value.strip():
                return True
    return False


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

    # BOOTSTRAP ADMIN IS A PRIVILEGE GRANT, SO IT NEEDS PROOF OF THE ADDRESS.
    #
    # Before this check, the only condition was "the token's email is in the
    # allow-list" - and the email in a Supabase token is self-asserted at signup
    # unless the project requires confirmation. So anyone able to sign up as
    # `BOOTSTRAP_ADMIN_EMAILS` became an administrator, which is the exact
    # privilege escalation this closes.
    #
    # Both conditions are required, and the confirmation check fails closed. The
    # decision lives HERE, at the one place the role is chosen, rather than in a
    # helper each route calls - there is exactly one caller of `provision` with
    # `is_admin_email=True` in the codebase, which is what makes this a boundary
    # rather than one of several checks.
    is_admin_email = bool(
        principal.email is not None
        and settings.is_bootstrap_admin_email(principal.email)
        and email_is_confirmed(principal.claims)
    )
    if (
        principal.email is not None
        and settings.is_bootstrap_admin_email(principal.email)
        and not is_admin_email
    ):
        # Logged, never raised: the account is still created, as a STUDENT, so the
        # person can sign in and confirm their address. Refusing the request
        # instead would leave a real operator locked out of their own platform
        # with a 403 and no explanation, which is a worse failure than a missing
        # privilege and a log line naming the reason.
        logger.warning(
            "Bootstrap admin NOT granted to %s: the token carries no evidence that "
            "the address is confirmed. A STUDENT account was created instead.",
            principal.email,
        )

    user = await repo.provision(
        session,
        auth_user_id=principal.auth_user_id,
        email=principal.email,
        is_admin_email=is_admin_email,
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
