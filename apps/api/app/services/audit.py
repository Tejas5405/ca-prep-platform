"""The audit trail: who did what to the platform.

WHY THIS IS NOT THE ANALYTICS TABLE

``analytics_events`` answers "is the product used?" and its rows are disposable -
one would happily drop a year of them behind a retention rule. ``audit_logs``
answers "who deleted the Financial Reporting material, and when?" and those rows are
evidence. Sharing one table would tie the second question's answer to the first
question's retention policy, in a platform where a single admin account controls
every student's content.

WHY IT RECORDS BEFORE/AFTER VALUES

"Role changed" is not an answer. "Editor -> Content Manager, by owner@caprep.in" is.
The ``changes`` field holds only the fields that actually moved, so the log does not
carry a full row image that could itself leak data (a password hash in a log is how
logs become a liability).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


class AuditAction:
    """Every audited action, in one place so the log view can group by prefix."""

    ADMIN_SIGNED_IN = "admin.signed_in"

    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_SUSPENDED = "user.suspended"
    USER_REACTIVATED = "user.reactivated"
    USER_DELETED = "user.deleted"
    ROLE_CHANGED = "user.role_changed"

    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_ARCHIVED = "document.archived"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_REPLACED = "document.replaced"
    DOCUMENT_REPROCESSED = "document.reprocessed"
    BULK_UPLOAD_COMPLETED = "document.bulk_uploaded"

    ACCESS_GRANTED = "access.granted"
    ACCESS_REVOKED = "access.revoked"

    COURSE_CREATED = "curriculum.course_created"
    COURSE_UPDATED = "curriculum.course_updated"

    QUESTION_CREATED = "question.created"
    QUESTION_UPDATED = "question.updated"
    QUESTION_REVISED = "question.revised"
    QUESTION_FLAGGED = "question.flagged"
    QUESTION_PUBLISHED = "question.published"
    QUESTION_REVIEWED = "question.reviewed"
    MOCK_CREATED = "test.created"
    MOCK_UPDATED = "test.updated"
    MOCK_PUBLISHED = "test.published"
    CURRICULUM_WRITTEN = "curriculum.written"

    PLAN_UPDATED = "payments.plan_updated"
    GATEWAY_CONFIGURED = "payments.gateway_configured"
    LAW_NOTICE_RECORDED = "content.law_notice_recorded"
    QUESTION_CLASSIFIED = "question.classified"
    PAYMENT_OVERRIDE = "payments.override"
    REFUND_RECORDED = "payments.refund_recorded"

    SUBSCRIPTION_CHANGED = "subscription.changed"
    NOTIFICATION_SENT = "notification.sent"
    BADGE_CREATED = "gamification.badge_created"
    BADGE_UPDATED = "gamification.badge_updated"
    POINTS_ADJUSTED = "gamification.points_adjusted"
    SETTING_CHANGED = "settings.changed"
    AI_SETTING_CHANGED = "ai.setting_changed"


def client_ip(request: Request | None) -> str | None:
    """The caller's address, from the proxy header when present.

    ``X-Forwarded-For`` is set by Render's edge. It is untrusted input - a client can
    send its own - so it is recorded for context and NEVER used for authorization.
    """
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


async def record_audit(
    session: AsyncSession,
    action: str,
    *,
    actor: User | None = None,
    summary: str = "",
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    request: Request | None = None,
    commit: bool = False,
) -> AuditLog | None:
    """Append one audit row.

    Swallows its own failures, for the same reason analytics does: an audit write
    must not be able to roll back the operation it is describing. The trade is
    explicit - a failed audit write leaves the action done and unlogged, which is
    strictly better than an action half-done because the log was unavailable.
    """
    try:
        entry = AuditLog(
            id=uuid.uuid4(),
            actor_user_id=actor.id if actor else None,
            actor_email=getattr(actor, "email", None),
            actor_role=getattr(actor, "role", None),
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            summary=summary,
            changes=changes or None,
            ip_address=client_ip(request),
        )
        session.add(entry)
        if commit:
            await session.commit()
        else:
            await session.flush()
        return entry
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("audit entry %s could not be recorded", action, exc_info=True)
        return None


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Only the keys that changed, as ``{field: {"from": x, "to": y}}``."""
    changed: dict[str, Any] = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changed[key] = {"from": old_value, "to": new_value}
    return changed
