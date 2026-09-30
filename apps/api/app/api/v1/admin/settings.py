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

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import (
    Permission,
    require_permission,
)
from app.models.engagement import PlatformSetting
from app.models.user import User
from app.schemas.base import StrictRequest
from app.services.audit import AuditAction, record_audit

router = APIRouter(tags=["admin"])

"""Platform settings."""


@router.get("/admin/settings", summary="Platform settings")
async def list_settings(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    rows = (
        (await session.execute(select(PlatformSetting).order_by(PlatformSetting.key)))
        .scalars()
        .all()
    )
    return success(
        {
            "settings": [
                {
                    "key": row.key,
                    "value": row.value,
                    "description": row.description,
                    "isPublic": row.is_public,
                    "updatedAt": row.updated_at,
                }
                for row in rows
            ]
        },
        request_id=get_request_id(),
    )


class SettingIn(StrictRequest):
    key: str = Field(min_length=1, max_length=80)
    value: Any = None
    description: str | None = Field(default=None, max_length=300)


@router.put("/admin/settings", summary="Change a setting")
async def put_setting(
    payload: SettingIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_SETTINGS)),
) -> Any:
    """Upsert one setting.

    SECRETS ARE REFUSED BY NAME. A settings table is the most tempting place to put an
    API key, and the read path for public settings is unauthenticated - so a key here
    would be a key on a public endpoint. The guard below is a blunt instrument
    ("refuse keys that look like secrets") and that is the intention: it catches the
    mistake rather than pretending it cannot happen.
    """
    key = payload.key.strip().lower()
    forbidden_fragments = ("secret", "key", "token", "password", "dsn", "credential")
    reserved = {"billing.plan_overrides"}
    if any(fragment in key for fragment in forbidden_fragments) or key in reserved:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Secrets do not belong here",
            detail=(
                "Settings are readable by the app and some are public. Put credentials "
                "in the environment instead (Render dashboard or .env)."
            ),
            type_slug="settings",
        )

    row = (
        await session.execute(select(PlatformSetting).where(PlatformSetting.key == key))
    ).scalar_one_or_none()
    previous = row.value if row is not None else None
    if row is None:
        row = PlatformSetting(
            id=uuid.uuid4(),
            key=key,
            # Wrapped in an object so a bare string, number or bool is representable
            # without the column type fighting it.
            value={"value": payload.value},
            description=payload.description,
            updated_by=actor.id,
        )
        session.add(row)
    else:
        row.value = {"value": payload.value}
        if payload.description is not None:
            row.description = payload.description
        row.updated_by = actor.id
    await session.flush()

    await record_audit(
        session,
        AuditAction.SETTING_CHANGED,
        actor=actor,
        summary=f"Set {key}",
        target_type="setting",
        target_id=key,
        changes={"from": previous, "to": row.value},
        request=request,
    )
    await session.commit()
    return success(
        {"key": row.key, "value": row.value, "updatedAt": row.updated_at},
        request_id=get_request_id(),
    )
