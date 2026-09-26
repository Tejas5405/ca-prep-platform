"""User lookups.

The API's identity model has two ids and confusing them is a real bug class:

  * ``auth_user_id`` - the ``sub`` claim of a verified token (a Supabase user id).
  * ``id`` - the platform's own user row. What foreign keys point at.

A handler that needs "who did this" starts with the first and must resolve the
second. That resolution is not automatic: ``questions.created_by``,
``ingestion_drafts.reviewed_by`` and every other audit column is an FK to
``users.id``, so passing an auth user id into one of them produces a foreign-key
violation - or, worse on a looser schema, an attribution that points at nothing.

LOOKUPS FILTER OUT DEACTIVATED ACCOUNTS

``is_active`` is checked here rather than by every caller. A deactivated user
whose token has not yet expired can still present a valid signature; refusing to
resolve them means the audit column stays NULL instead of recording a
deactivated account as the author of content.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)


class SqlUserRepository:
    """Session-factory based user reads."""

    def __init__(self, session_factory: object = None) -> None:
        self._session_factory = session_factory

    def _factory(self):
        if self._session_factory is None:
            from app.core.dependencies import get_engine, get_session_factory

            get_engine()
            self._session_factory = get_session_factory()
        return self._session_factory

    async def get_by_auth_id(
        self,
        auth_user_id: str,
        *,
        include_inactive: bool = False,
        include_deleted: bool = False,
    ) -> User | None:
        """Resolve a token subject to a usable platform row.

        Two flags remove an account from circulation and they are not the same
        thing: ``deleted_at`` is an erasure, ``is_active`` is a suspension. Both
        must stop a token that is still cryptographically valid from being
        treated as a signed-in user, so both are filtered here.

        ``is_active`` was documented as filtered and was not, which is how a
        deactivated account kept full access: the flag was written by whoever
        suspended the account and read by nobody. It is checked here rather than
        by every caller because there are thirty-odd call sites and one place to
        get this right.

        ``include_inactive`` and ``include_deleted`` are for the callers that must
        SEE a barred row rather than be blinded to it: identity resolution, which
        answers such a token with 403 instead of silently re-registering the
        person, and the provisioning recovery read, which must not mistake a
        barred account for a free slot on the unique index.

        Both default to False, so a caller that has not thought about suspensions
        gets the safe answer rather than the convenient one.
        """
        if not auth_user_id:
            return None

        filters = [User.auth_user_id == auth_user_id]
        if not include_deleted:
            filters.append(User.deleted_at.is_(None))
        if not include_inactive:
            filters.append(User.is_active.is_(True))

        async with self._factory()() as session:
            return await session.scalar(select(User).where(*filters))

    async def get_by_id(self, user_id: object) -> User | None:
        async with self._factory()() as session:
            return await session.get(User, user_id)

    async def provision(
        self,
        session: AsyncSession,
        *,
        auth_user_id: str,
        email: str | None = None,
        is_admin_email: bool = False,
        display_name: str | None = None,
    ) -> User:
        """Create the platform row for a verified identity, on first sign-in.

        This ran as `get_current_user` calling a method that did not exist, so
        EVERY authenticated request returned 500 -- and the suite stayed green,
        because the integration tests override the current-user dependency and
        never exercise the default chain. The lesson is in the test, not the fix:
        `tests/integration/test_identity_provisioning.py` now calls this the way
        the app does.

        CONCURRENCY. Two requests can arrive together for a brand-new account: the
        app opens several screens at once, and each fires a request. Both find no
        row, both insert, and one hits the unique index on ``auth_user_id``. The
        insert therefore happens inside a SAVEPOINT, so the loser recovers by
        re-reading the winner's row instead of failing - and, just as importantly,
        the loser does not roll back the rest of the caller's transaction, which a
        plain ``rollback()`` on a shared session would do.

        The email is stored for display and notification routing only. Nothing
        authenticates against it: the identity is ``auth_user_id``, which comes
        from a signature.
        """
        user = User(
            auth_user_id=auth_user_id,
            email=email,
            display_name=display_name,
            role="ADMIN" if is_admin_email else "STUDENT",
        )
        try:
            async with session.begin_nested():
                session.add(user)
                await session.flush()
        except IntegrityError:
            # NOT filtered by activity: this read runs to recover from a race, and
            # the row that won it may be suspended. Returning it lets the caller
            # decide (identity resolution raises 403); excluding it would loop
            # back into another insert against the same unique index.
            existing = await session.scalar(select(User).where(User.auth_user_id == auth_user_id))
            if existing is None:
                # The unique index was not the cause, so this is not the race the
                # savepoint is here for. Re-raise rather than swallow it.
                raise
            logger.info(
                "provision raced for auth_user_id=%s; reusing the existing row",
                auth_user_id,
            )
            return existing
        logger.info(
            "provisioned user id=%s auth_user_id=%s role=%s",
            user.id,
            auth_user_id,
            user.role,
        )
        return user


async def get_user_by_auth_id(auth_user_id: str) -> User | None:
    """Convenience wrapper for the common case.

    Returns None for an unknown, deactivated or erased account rather than raising.
    Callers decide whether the absence is fatal: for an audit column it is not,
    and refusing an editor's approval because their profile row is missing would
    be a worse outcome than the missing attribution.
    """
    return await SqlUserRepository().get_by_auth_id(auth_user_id)
