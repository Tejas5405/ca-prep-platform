"""The content library: student reading and admin bulk upload.

TWO AUDIENCES, ONE FILE, DELIBERATELY

The student routes and the admin routes are separated by path (``/content/...`` vs
``/admin/content/...``) and by permission, but they live together because they share
one idea and one access rule. Splitting them into two modules is how the student list
and the admin list drift apart - one gains a filter the other lacks, one forgets the
access clause. Anyone touching document visibility should have to scroll past both.

BULK UPLOAD, WHICH IS THE POINT OF THE WHOLE FEATURE

500 PDFs cannot go through the API as 500 request bodies. Two facts shape the design:

  1. The bytes never touch the API. Each file gets its own short-lived signed URL and
     the browser PUTs straight to Supabase Storage, in parallel, with a concurrency
     cap. Render never buffers a 50 MB file, and one slow upload cannot block the
     other 499.
  2. One request creates the batch. ``POST /admin/content/uploads`` takes a manifest
     of up to 500 entries, computes duplicates by checksum, and returns a row per
     file with its upload URL and its document id. The admin screen then works
     through that list, showing per-file state and retrying only what failed.

That is how 500 files become one operation with a progress bar instead of an
afternoon of clicking.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.permissions import Permission, require_permission
from app.models.user import User
from app.repositories.content import SqlContentStore
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit

router = APIRouter(tags=["content"])

"""Per-document access rules."""


class AccessRuleIn(StrictRequest):
    """An explicit grant or denial of access to one document."""

    scope: str = Field(pattern="^(ROLE|USER|COURSE|PLAN|TIER)$")
    effect: str = Field(default="ALLOW", pattern="^(ALLOW|DENY)$")
    role: str | None = Field(default=None, max_length=20)
    user_id: UuidRef | None = None
    course_id: UuidRef | None = None
    plan_code: str | None = Field(default=None, max_length=20)
    tier: str | None = Field(default=None, max_length=20)
    reason: str | None = Field(default=None, max_length=300)
    expires_at: datetime | None = None


@router.get("/admin/content/documents/{document_id}/access", summary="Access rules for a document")
async def list_access_rules(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )
    rules = await store.rules_for(document_id)
    return success(
        {
            "documentId": str(document_id),
            "accessTier": document.access_tier,
            "isPublished": document.is_published,
            "rules": [
                {
                    "id": str(rule.id),
                    "scope": rule.scope,
                    "effect": rule.effect,
                    "role": rule.role,
                    "userId": str(rule.user_id) if rule.user_id else None,
                    "courseId": str(rule.course_id) if rule.course_id else None,
                    "planCode": rule.plan_code,
                    "tier": rule.tier,
                    "reason": rule.reason,
                    "expiresAt": rule.expires_at,
                    "createdAt": rule.created_at,
                }
                for rule in rules
            ],
        },
        request_id=get_request_id(),
    )


@router.post(
    "/admin/content/documents/{document_id}/access",
    status_code=status.HTTP_201_CREATED,
    summary="Grant or deny access to a document",
)
async def add_access_rule(
    document_id: uuid.UUID,
    payload: AccessRuleIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    """Add one rule. The CHECK constraint refuses a rule with no target."""
    from sqlalchemy.exc import IntegrityError

    store = SqlContentStore(session)
    document = await store.get_document(document_id)
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )
    try:
        rule = await store.add_rule(
            document_id=document_id,
            scope=payload.scope,
            effect=payload.effect,
            role=payload.role,
            user_id=payload.user_id,
            course_id=payload.course_id,
            plan_code=payload.plan_code,
            tier=payload.tier,
            reason=payload.reason,
            expires_at=payload.expires_at,
            granted_by=actor.id,
        )
    except IntegrityError:
        # The unique constraint caught a duplicate target; the CHECK caught a rule with
        # no target. Both are the admin's mistake rather than the server's, and both
        # deserve a sentence rather than a 500.
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Rule not valid",
            detail="A rule like this already exists, or its target is missing.",
            type_slug="content",
        )

    # The summary names the target, because "ACCESS granted" with no "to whom" is an
    # audit line that answers nothing when it is read six months later.
    target = (
        payload.role or payload.tier or payload.plan_code or payload.user_id or payload.course_id
    )
    await record_audit(
        session,
        AuditAction.ACCESS_GRANTED if payload.effect == "ALLOW" else AuditAction.ACCESS_REVOKED,
        actor=actor,
        summary=f"{payload.effect} {payload.scope} access to '{document.title}' for {target}",
        target_type="document",
        target_id=document_id,
        changes={"scope": payload.scope, "effect": payload.effect},
        request=request,
    )
    await session.commit()
    return success({"id": str(rule.id)}, request_id=get_request_id())


@router.delete("/admin/content/access/{rule_id}", summary="Remove an access rule")
async def delete_access_rule(
    rule_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    store = SqlContentStore(session)
    if not await store.delete_rule(rule_id):
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Rule not found",
            detail="No such access rule.",
            type_slug="content",
        )
    await record_audit(
        session,
        AuditAction.ACCESS_REVOKED,
        actor=actor,
        summary="Removed an access rule",
        target_type="access_rule",
        target_id=rule_id,
        request=request,
    )
    await session.commit()
    return success({"id": str(rule_id), "deleted": True}, request_id=get_request_id())
