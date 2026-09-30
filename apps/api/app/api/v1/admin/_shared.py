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

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Role
from app.models.user import User

router = APIRouter(tags=["admin"])

"""Helpers and constants shared by more than one sub-module. Unchanged; only relocated."""


#: Roles that may be assigned through the API. SUPER_ADMIN is excluded: it is the
#: owner's own role, granted out of band by configuration, and an endpoint that can
#: mint more owners is an endpoint that can be used to mint one after a compromise.
ASSIGNABLE_ROLES: tuple[str, ...] = tuple(
    role.value for role in Role if role is not Role.SUPER_ADMIN
)


async def _record(
    session: AsyncSession, name: str, actor: User, properties: dict[str, Any]
) -> None:
    from app.services.analytics import record_event

    await record_event(session, name, user_id=actor.id, role=actor.role, properties=properties)


def _ilike_contains(term: str) -> str:
    """A contains-pattern that does not treat the operator's typing as wildcards.

    An unescaped `%` in `ILIKE '%…%'` matches every row. Email search is how an
    operator finds one student; it must not become "show me the whole ledger"
    because they typed a percent sign.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
