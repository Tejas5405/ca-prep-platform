"""Access control: the screen that answers "why can this student see that?".

THE ADMIN'S AUTHORITY, AND WHY IT NEEDED ITS OWN MODULE

Until now an owner could grant access to exactly one thing: one document, through the
per-document rules in ``content.py``. Doing that for a course meant one row per
document, which is not an administrative action, it is data entry. This module exposes
the library-wide grants table (``content_grants``) added in migration 0010:

    POST   /admin/access/grants          create a grant for a person, a role, a tier
                                         or a plan, optionally narrowed to a course,
                                         a subject or a content kind
    GET    /admin/access/grants          list them, filtered, including revoked ones
    DELETE /admin/access/grants/{id}     revoke one
    GET    /admin/access/users/{id}      what this student can reach right now, and why
    GET    /admin/access/explain         one document, one student, one sentence

WHY "EXPLAIN" IS PART OF THE API AND NOT A UI CONCERN

An access screen without it is a list of switches: an operator who ticks a box and sees
no change has no way to tell whether the grant failed, an older DENY is outranking it,
the document is unpublished, or the student's subscription lapsed. ``explain`` runs the
SAME precedence engine the student's own requests run and returns the sentence that
engine reached, so what the admin sees cannot drift from what the student experiences.
That is the difference between a panel that reflects access and a panel that only
reflects intent.

EVERY ROUTE HERE REQUIRES ``MANAGE_ACCESS``, AND ONE OF THEM IS REVOCATION

Revoking is a write, so it is reported rather than enforced by hiding: a CONTENT_MANAGER
holds MANAGE_ACCESS and may grant and revoke; an EDITOR does not and gets 403 from the
dependency, not from a hidden button. Grants are never deleted - revocation is a
timestamp - because "who could read this in March, and who took it away" is the first
question asked after a leak.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import Permission, require_permission
from app.models.content import ContentAccessRule, ContentDocument, ContentGrant
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services.audit import AuditAction, record_audit
from app.services.content_library import AccessDecision, can_read, explain
from app.services.entitlements import resolve_viewer

router = APIRouter(tags=["access"])

#: The scopes a grant's audience may use, and the column that must carry the value.
WHO_SELECTOR = {"USER": "user_id", "ROLE": "role", "TIER": "tier", "PLAN": "plan_code"}


class GrantIn(StrictRequest):
    """A library-wide grant.

    The shape forces the decision the CHECK constraint also enforces: name an audience
    SCOPE, and fill exactly the one field it refers to. A grant with no audience would
    silently mean "everybody", which is the single most damaging typo this table can
    hold, so it is refused at the edge as well as in the database.
    """

    who_scope: str = Field(pattern="^(USER|ROLE|TIER|PLAN)$")
    effect: str = Field(default="ALLOW", pattern="^(ALLOW|DENY)$")
    user_id: UuidRef | None = None
    role: str | None = Field(default=None, max_length=20)
    tier: str | None = Field(default=None, max_length=20)
    plan_code: str | None = Field(default=None, max_length=20)

    #: The "what" axis. All three null means the whole library.
    course_id: UuidRef | None = None
    subject_id: UuidRef | None = None
    kind: str | None = Field(default=None, max_length=30)

    reason: str | None = Field(default=None, max_length=300)
    #: When set, the grant stops applying after this instant. Left null it never expires.
    expires_at: datetime | None = None


def _grant_payload(grant: ContentGrant) -> dict[str, Any]:
    """One grant, as the screen renders it.

    ``isLive`` is computed here rather than left to the client: the expiry comparison
    uses the server's clock, and a browser with a wrong clock would otherwise show an
    expired grant as active.
    """
    now = datetime.now(UTC)
    expires_at = grant.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return {
        "id": str(grant.id),
        "whoScope": grant.who_scope,
        "effect": grant.effect,
        "userId": str(grant.user_id) if grant.user_id else None,
        "role": grant.role,
        "tier": grant.tier,
        "planCode": grant.plan_code,
        "courseId": str(grant.course_id) if grant.course_id else None,
        "subjectId": str(grant.subject_id) if grant.subject_id else None,
        "kind": grant.kind,
        "reason": grant.reason,
        "grantedBy": str(grant.granted_by) if grant.granted_by else None,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "revokedAt": grant.revoked_at.isoformat() if grant.revoked_at else None,
        "createdAt": grant.created_at.isoformat() if grant.created_at else None,
        "isLive": grant.revoked_at is None and (expires_at is None or expires_at > now),
        # A grant with no "what" selectors covers the entire library, which is worth
        # spelling out on the screen rather than leaving as three dashes.
        "covers": {
            "courseId": str(grant.course_id) if grant.course_id else None,
            "subjectId": str(grant.subject_id) if grant.subject_id else None,
            "kind": grant.kind,
            "wholeLibrary": grant.course_id is None
            and grant.subject_id is None
            and grant.kind is None,
        },
    }


@router.get("/admin/access/grants", summary="List access grants")
async def list_grants(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
    user_id: uuid.UUID | None = Query(default=None),
    course_id: uuid.UUID | None = Query(default=None),
    subject_id: uuid.UUID | None = Query(default=None),
    who_scope: str | None = Query(default=None, pattern="^(USER|ROLE|TIER|PLAN)$"),
    effect: str | None = Query(default=None, pattern="^(ALLOW|DENY)$"),
    include_revoked: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> Any:
    """Every grant, newest first, with the filters the screen actually offers.

    ``include_revoked`` defaults to false so the default view is the CURRENT state of
    access: a list that mixes live and revoked rows is one an operator will misread.
    """
    clause = select(ContentGrant)
    if user_id is not None:
        clause = clause.where(ContentGrant.user_id == user_id)
    if course_id is not None:
        clause = clause.where(ContentGrant.course_id == course_id)
    if subject_id is not None:
        clause = clause.where(ContentGrant.subject_id == subject_id)
    if who_scope is not None:
        clause = clause.where(ContentGrant.who_scope == who_scope)
    if effect is not None:
        clause = clause.where(ContentGrant.effect == effect)
    if not include_revoked:
        clause = clause.where(ContentGrant.revoked_at.is_(None))

    total = (
        await session.execute(select(func.count()).select_from(clause.subquery()))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                clause.order_by(ContentGrant.created_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return paginated(
        [_grant_payload(row) for row in rows],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.post(
    "/admin/access/grants",
    status_code=status.HTTP_201_CREATED,
    summary="Grant or deny access across the library",
)
async def create_grant(
    payload: GrantIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    """Create a grant.

    Written as one row rather than a loop over documents: the query-time filter reads
    it on every student request, so a course-wide grant costs the same as a
    document-wide one and there is nothing to keep in sync when a document is added to
    the course later. That last property is why this is the right model - a
    per-document fan-out would have to be re-run every time the library grows, and the
    material added after the grant would silently not be covered.
    """
    selector_column = WHO_SELECTOR[payload.who_scope]
    provided = {
        "user_id": payload.user_id,
        "role": payload.role,
        "tier": payload.tier,
        "plan_code": payload.plan_code,
    }
    chosen = provided[selector_column]
    if chosen is None:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Grant has no audience",
            detail=f"A {payload.who_scope} grant must set {selector_column}.",
            type_slug="access",
        )
    extra = {
        name: value
        for name, value in provided.items()
        if value is not None and name != selector_column
    }
    if extra:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Grant names two audiences",
            detail=(
                f"A {payload.who_scope} grant must not also set "
                f"{', '.join(sorted(extra))}. Two selectors would be read by one code "
                "path and ignored by another."
            ),
            type_slug="access",
        )

    if payload.expires_at is not None and payload.expires_at <= datetime.now(UTC):
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Grant already expired",
            detail="expires_at is in the past, so this grant would never apply.",
            type_slug="access",
        )

    grant = ContentGrant(
        id=uuid.uuid4(),
        who_scope=payload.who_scope,
        effect=payload.effect,
        user_id=payload.user_id,
        role=payload.role,
        tier=payload.tier,
        plan_code=payload.plan_code,
        course_id=payload.course_id,
        subject_id=payload.subject_id,
        kind=payload.kind,
        granted_by=actor.id,
        reason=payload.reason,
        expires_at=payload.expires_at,
    )
    session.add(grant)
    try:
        await session.flush()
    except Exception as exc:
        await session.rollback()
        if "uq_grant_target" in str(exc):
            return problem(
                status=status.HTTP_409_CONFLICT,
                title="Grant already exists",
                detail="That audience already has this exact grant.",
                type_slug="access",
            )
        raise

    target = f"{payload.who_scope}:{chosen}"
    # The scope of the grant, in the words an operator would use when reading the log
    # back six months later.
    if grant.course_id is not None:
        scope_text = f"course {grant.course_id}"
    elif grant.subject_id is not None:
        scope_text = f"subject {grant.subject_id}"
    elif grant.kind is not None:
        scope_text = f"all {grant.kind} material"
    else:
        scope_text = "the whole library"
    await record_audit(
        session,
        AuditAction.ACCESS_GRANTED if payload.effect == "ALLOW" else AuditAction.ACCESS_REVOKED,
        actor=actor,
        summary=(
            f"{'Allowed' if payload.effect == 'ALLOW' else 'Denied'} {scope_text} for {target}"
        ),
        target_type="grant",
        target_id=grant.id,
        request=request,
    )
    await session.commit()
    return success(_grant_payload(grant), request_id=get_request_id())


@router.delete("/admin/access/grants/{grant_id}", summary="Revoke an access grant")
async def revoke_grant(
    grant_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    """Revoke by timestamp, never by deleting the row.

    The row IS the record that the access existed. Deleting it would make last month's
    access unanswerable, which is the wrong trade for a table whose whole purpose is to
    be the authority on who may read what.
    """
    grant = (
        await session.execute(select(ContentGrant).where(ContentGrant.id == grant_id))
    ).scalar_one_or_none()
    if grant is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Grant not found",
            detail="No such grant.",
            type_slug="access",
        )
    if grant.revoked_at is not None:
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Already revoked",
            detail="That grant was revoked earlier.",
            type_slug="access",
        )

    grant.revoked_at = datetime.now(UTC)
    await record_audit(
        session,
        AuditAction.ACCESS_REVOKED,
        actor=actor,
        summary=f"Revoked a {grant.who_scope} grant ({grant.effect})",
        target_type="grant",
        target_id=grant.id,
        request=request,
    )
    await session.commit()
    return success(_grant_payload(grant), request_id=get_request_id())


@router.get("/admin/access/users/{user_id}", summary="What one student can reach")
async def student_access(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    """The student's side of the screen: their entitlements, courses and live grants.

    Counted in the database rather than by listing documents, so it stays a fixed cost
    as the library grows, and reported as counts per reason because that is what an
    operator is checking ("is this student on Premium, and does anything extra
    override it?").
    """
    student = (
        await session.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if student is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="User not found",
            detail="No such account.",
            type_slug="user",
        )

    viewer = await resolve_viewer(session, student)
    grants = (
        (
            await session.execute(
                select(ContentGrant)
                .where(ContentGrant.revoked_at.is_(None))
                .where(
                    or_(
                        ContentGrant.user_id == user_id,
                        ContentGrant.role == student.role,
                        ContentGrant.tier == viewer.tier,
                    )
                )
                .order_by(ContentGrant.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    live = [
        grant
        for grant in grants
        if grant.expires_at is None
        or grant.expires_at.replace(tzinfo=grant.expires_at.tzinfo or UTC) > datetime.now(UTC)
    ]

    return success(
        {
            "userId": str(student.id),
            "email": student.email,
            "displayName": student.display_name,
            "role": student.role,
            "isActive": student.is_active,
            "tier": viewer.tier,
            "planCode": viewer.plan_code,
            "courseIds": sorted(str(cid) for cid in viewer.course_ids),
            "grants": [_grant_payload(grant) for grant in live],
            "allowGrants": sum(1 for grant in live if grant.effect == "ALLOW"),
            "denyGrants": sum(1 for grant in live if grant.effect == "DENY"),
        },
        request_id=get_request_id(),
    )


@router.get("/admin/access/explain", summary="Why one student can or cannot read one document")
async def explain_access(
    user_id: uuid.UUID = Query(...),
    document_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_ACCESS)),
) -> Any:
    """Run the student's own access decision and hand back the reason.

    This is the endpoint that makes the panel trustworthy: it does not describe intent,
    it reports the outcome of the same function that will answer the student's request
    a second later, including the case where an old DENY is quietly outranking a new
    ALLOW.
    """
    student = (
        await session.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if student is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="User not found",
            detail="No such account.",
            type_slug="user",
        )
    document = (
        await session.execute(select(ContentDocument).where(ContentDocument.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Document not found",
            detail="No such document.",
            type_slug="content",
        )

    rules = (
        (
            await session.execute(
                select(ContentAccessRule).where(ContentAccessRule.document_id == document_id)
            )
        )
        .scalars()
        .all()
    )
    grants = (await session.execute(select(ContentGrant))).scalars().all()

    viewer = await resolve_viewer(session, student)
    decision = can_read(document, viewer, list(rules), list(grants))
    return success(
        {
            "userId": str(student.id),
            "documentId": str(document.id),
            "documentTitle": document.title,
            "documentTier": document.access_tier,
            "documentStatus": document.status,
            "isPublished": document.is_published,
            "viewerTier": viewer.tier,
            "decision": decision,
            "canRead": decision == AccessDecision.GRANTED,
            "reason": explain(document, viewer, list(rules), list(grants)),
        },
        request_id=get_request_id(),
    )
