"""Content access: one function decides whether a student may read a document.

THE RULE THIS MODULE EXISTS TO ENFORCE

A student must not be able to read material their access rules do not permit, and the
decision must be made on the server, in one place, for every read path. Hiding a
button is not a control; the API is the control. Every route that returns document
content - the library list, the detail view, a page of extracted text, a signed
preview URL, full-text search hits - resolves access through ``document_filter`` or
``can_read`` in this module. There is no second implementation to drift from this one.

THE PRECEDENCE, STATED ONCE

1. An unpublished document is invisible to a non-staff viewer. Always. This is
   checked first because it is the content pipeline's QA gate as well as an access
   rule: a file still being reviewed must not be readable because someone guessed its
   id.
2. An explicit DENY rule wins over everything else. It is how a leaked or
   rights-restricted document is withdrawn from one person without unpublishing it
   from everyone.
3. An explicit ALLOW rule grants, whatever the tier says. It is how a comp, a
   scholarship or a bundle sold over the phone is honoured.
4. Otherwise the document's ``access_tier`` is compared against the viewer's
   entitlements, using the SAME vocabulary the billing service already uses:
   FREE < PREMIUM < PREMIUM_PLUS. One ladder, not two.

STEPS 2 AND 3 READ TWO TABLES, AND THAT IS NOT A SECOND RULE

``content_access_rules`` holds per-document overrides; ``content_grants`` holds
library-wide ones (a course, a subject, a content kind, or everything at once). Both are
evaluated HERE, in one pass, in the order above - a DENY from either source beats an
ALLOW from either source. Two sources of fact, one engine. Adding the grants table did
not change the precedence, and there is no code path that decides access anywhere else.

Staff are not special-cased by role name here; they hold ``VIEW_CONTENT`` and the
admin routes require it. Keeping the student rule free of role exceptions means the
"does this student see it" question has exactly one answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Select, and_, exists, or_, select
from sqlalchemy.sql import ColumnElement

from app.models.content import ContentAccessRule, ContentDocument, ContentGrant

#: The entitlement ladder. MUST stay in step with ``app.services.billing.Tier``:
#: a document tier that is not on this ladder would compare as "unknown" and fall to
#: the most restrictive branch, which is the safe direction but a bug worth avoiding.
TIER_ORDER: dict[str, int] = {"FREE": 0, "PREMIUM": 1, "PREMIUM_PLUS": 2}

#: Statuses a document can carry and still be readable. ``INDEXED`` and ``COMPLETED``
#: are both "the text is there"; the distinction matters to the admin, not the reader.
READABLE_STATUSES: frozenset[str] = frozenset({"INDEXED", "COMPLETED"})


@dataclass(frozen=True)
class Viewer:
    """Everything the access decision needs about the person asking.

    Built ONCE per request from the verified token plus the database row, then passed
    to the query builder. Bundling it means the predicate cannot accidentally read a
    role from one source and a tier from another.
    """

    user_id: uuid.UUID
    role: str
    #: Highest tier the viewer's subscription grants, from ``billing.entitlement_for``.
    tier: str = "FREE"
    plan_code: str | None = None
    #: Courses the viewer is enrolled in. Empty for a student who has only a
    #: subscription - which is the normal case, and the reason COURSE rules are
    #: additive rather than required.
    course_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    is_staff: bool = False


def _rule_matches(viewer: Viewer) -> ColumnElement[bool]:
    """SQL predicate: does ANY rule row match this viewer's attributes?

    Built as a query fragment rather than by loading rules into Python, because the
    list endpoint has to filter inside the database. The precedence rules are applied
    by the CALLER (see ``document_filter``), not here.
    """
    course_ids = list(viewer.course_ids)
    course_match = ContentAccessRule.course_id.in_(course_ids) if course_ids else None
    clauses = [
        and_(ContentAccessRule.scope == "ROLE", ContentAccessRule.role == viewer.role),
        and_(ContentAccessRule.scope == "USER", ContentAccessRule.user_id == viewer.user_id),
        and_(ContentAccessRule.scope == "TIER", ContentAccessRule.tier == viewer.tier),
    ]
    if viewer.plan_code:
        clauses.append(
            and_(
                ContentAccessRule.scope == "PLAN",
                ContentAccessRule.plan_code == viewer.plan_code,
            )
        )
    if course_match is not None:
        clauses.append(and_(ContentAccessRule.scope == "COURSE", course_match))
    return or_(*clauses)


def grant_matches(viewer: Viewer) -> ColumnElement[bool]:
    """SQL predicate: does this grant's AUDIENCE include the viewer?

    Only the "who" axis. The "what" axis is ``grant_covers_document`` below, because a
    grant that names no document can only be matched against a document column.
    """
    clauses = [
        and_(ContentGrant.who_scope == "USER", ContentGrant.user_id == viewer.user_id),
        and_(ContentGrant.who_scope == "ROLE", ContentGrant.role == viewer.role),
        and_(ContentGrant.who_scope == "TIER", ContentGrant.tier == viewer.tier),
    ]
    if viewer.plan_code:
        clauses.append(
            and_(
                ContentGrant.who_scope == "PLAN",
                ContentGrant.plan_code == viewer.plan_code,
            )
        )
    return or_(*clauses)


def grant_covers_document() -> ColumnElement[bool]:
    """SQL predicate: does this grant's SCOPE include the document being listed?

    All three selectors null means the whole library. Any non-null selector must match,
    so the axes combine with AND: a grant for one course and one kind covers only
    documents that are both, and a grant with no selectors covers everything.
    """
    return and_(
        or_(ContentGrant.course_id.is_(None), ContentGrant.course_id == ContentDocument.course_id),
        or_(
            ContentGrant.subject_id.is_(None),
            ContentGrant.subject_id == ContentDocument.subject_id,
        ),
        or_(ContentGrant.kind.is_(None), ContentGrant.kind == ContentDocument.kind),
    )


def _grant_live() -> ColumnElement[bool]:
    """A grant applies while it is neither revoked nor expired."""
    now = datetime.now(UTC)
    return and_(
        ContentGrant.revoked_at.is_(None),
        or_(ContentGrant.expires_at.is_(None), ContentGrant.expires_at > now),
    )


def _rule_live() -> ColumnElement[bool]:
    """A rule applies only until it expires."""
    return or_(
        ContentAccessRule.expires_at.is_(None),
        ContentAccessRule.expires_at > datetime.now(UTC),
    )


def document_filter(viewer: Viewer) -> ColumnElement[bool]:
    """The WHERE clause that limits a document query to what ``viewer`` may read.

    USE THIS, NOT A PYTHON CHECK, for anything that lists documents. A list that
    loads rows and filters them afterwards still ships the row ids to the client in
    pagination metadata, and still runs the query - which is how "hidden" content
    ends up readable through a count.
    """
    if viewer.is_staff:
        # Staff see everything except soft-deleted rows. The admin list has its own
        # route and its own filters; this keeps the student path free of role
        # exceptions.
        return ContentDocument.deleted_at.is_(None)

    tier_rank = TIER_ORDER.get(viewer.tier, 0)
    allowed_tiers = [name for name, rank in TIER_ORDER.items() if rank <= tier_rank]

    allow_rule = exists(
        select(ContentAccessRule.id).where(
            ContentAccessRule.document_id == ContentDocument.id,
            ContentAccessRule.effect == "ALLOW",
            _rule_live(),
            _rule_matches(viewer),
        )
    )
    deny_rule = exists(
        select(ContentAccessRule.id).where(
            ContentAccessRule.document_id == ContentDocument.id,
            ContentAccessRule.effect == "DENY",
            _rule_live(),
            _rule_matches(viewer),
        )
    )
    # The same three questions asked of the library-wide grants.
    allow_grant = exists(
        select(ContentGrant.id).where(
            ContentGrant.effect == "ALLOW",
            _grant_live(),
            grant_matches(viewer),
            grant_covers_document(),
        )
    )
    deny_grant = exists(
        select(ContentGrant.id).where(
            ContentGrant.effect == "DENY",
            _grant_live(),
            grant_matches(viewer),
            grant_covers_document(),
        )
    )

    return and_(
        ContentDocument.deleted_at.is_(None),
        ContentDocument.is_published.is_(True),
        ContentDocument.status.in_(READABLE_STATUSES),
        ~deny_rule,  # (2) deny beats everything below - from either source
        ~deny_grant,
        or_(  # (3) explicit allowance, or (4) the tier ladder
            allow_rule,
            allow_grant,
            ContentDocument.access_tier.in_(allowed_tiers),
        ),
    )


class AccessDecision:
    GRANTED = "GRANTED"
    NEEDS_UPGRADE = "NEEDS_UPGRADE"
    DENIED = "DENIED"
    NOT_READY = "NOT_READY"


def can_read(
    document: ContentDocument,
    viewer: Viewer,
    rules: list[ContentAccessRule] | None = None,
    grants: list[ContentGrant] | None = None,
) -> str:
    """The same decision as ``document_filter``, for a single already-loaded document.

    Kept as pure Python (with the rules passed in) so it is directly testable without
    a database, and so the detail route can explain WHY access was refused - a 403
    that says "this needs Premium" is a different screen from one that says "not
    found", and both are different from "still being processed".
    """
    if viewer.is_staff:
        return AccessDecision.GRANTED if document.deleted_at is None else AccessDecision.DENIED

    now = datetime.now(UTC)
    if document.deleted_at is not None or not document.is_published:
        return AccessDecision.DENIED
    if document.status not in READABLE_STATUSES:
        # Distinguishable on purpose: a student who was *told* a document exists by
        # a link should not be shown "not found" while OCR is still running.
        return AccessDecision.NOT_READY

    applicable = [
        rule for rule in (rules or []) if rule.expires_at is None or rule.expires_at > now
    ]
    # Library-wide grants, live ones only, matched on both axes against THIS document.
    live_grants = [
        grant
        for grant in (grants or [])
        if grant.revoked_at is None and (grant.expires_at is None or grant.expires_at > now)
    ]
    applicable_grants = [grant for grant in live_grants if _grant_applies(grant, document, viewer)]

    for rule in applicable:
        if rule.effect == "DENY" and _applies(rule, viewer):
            return AccessDecision.DENIED
    for grant in applicable_grants:
        if grant.effect == "DENY":
            return AccessDecision.DENIED
    for rule in applicable:
        if rule.effect == "ALLOW" and _applies(rule, viewer):
            return AccessDecision.GRANTED
    for grant in applicable_grants:
        if grant.effect == "ALLOW":
            return AccessDecision.GRANTED

    if TIER_ORDER.get(document.access_tier, 0) <= TIER_ORDER.get(viewer.tier, 0):
        return AccessDecision.GRANTED
    return AccessDecision.NEEDS_UPGRADE


def _applies(rule: ContentAccessRule, viewer: Viewer) -> bool:
    """Does one rule target this viewer? Mirrors ``_rule_matches`` in Python."""
    if rule.scope == "ROLE":
        return rule.role == viewer.role
    if rule.scope == "USER":
        return rule.user_id == viewer.user_id
    if rule.scope == "TIER":
        return rule.tier == viewer.tier
    if rule.scope == "PLAN":
        return bool(viewer.plan_code) and rule.plan_code == viewer.plan_code
    if rule.scope == "COURSE":
        return rule.course_id in viewer.course_ids
    return False


def _grant_applies(grant: ContentGrant, document: ContentDocument, viewer: Viewer) -> bool:
    """Does one library-wide grant cover this document for this viewer?

    Mirrors ``grant_matches`` and ``grant_covers_document`` in Python. Two axes: the
    audience must include the viewer, and every selector the grant sets must match the
    document. A mismatch on either axis means the grant is silent, not that it denies -
    absence of a grant is what the tier ladder is for.
    """
    if grant.who_scope == "USER" and grant.user_id != viewer.user_id:
        return False
    if grant.who_scope == "ROLE" and grant.role != viewer.role:
        return False
    if grant.who_scope == "TIER" and grant.tier != viewer.tier:
        return False
    if grant.who_scope == "PLAN" and (not viewer.plan_code or grant.plan_code != viewer.plan_code):
        return False
    if grant.course_id is not None and grant.course_id != document.course_id:
        return False
    if grant.subject_id is not None and grant.subject_id != document.subject_id:
        return False
    return not (grant.kind is not None and grant.kind != document.kind)


def explain(document: ContentDocument, viewer: Viewer, rules=None, grants=None) -> str:
    """A sentence saying WHY the decision came out the way it did.

    The admin access screen needs this: "Student X cannot read document Y" is not
    actionable, while "a DENY grant for this student on this course" is. It deliberately
    returns prose rather than another enum, because it is rendered to a human.
    """
    decision = can_read(document, viewer, rules, grants)
    if viewer.is_staff:
        return "Staff can read every document."
    if document.deleted_at is not None:
        return "The document is deleted."
    if not document.is_published:
        return "The document is not published."
    if document.status not in READABLE_STATUSES:
        return f"The document is still being processed (status {document.status})."

    now = datetime.now(UTC)
    live_rules = [r for r in (rules or []) if r.expires_at is None or r.expires_at > now]
    live_grants = [
        g
        for g in (grants or [])
        if g.revoked_at is None and (g.expires_at is None or g.expires_at > now)
    ]
    applicable_grants = [g for g in live_grants if _grant_applies(g, document, viewer)]
    if decision == AccessDecision.DENIED:
        for rule in live_rules:
            if rule.effect == "DENY" and _applies(rule, viewer):
                return f"Denied by a rule on this document (scope {rule.scope})."
        for grant in applicable_grants:
            if grant.effect == "DENY":
                return f"Denied by a library-wide grant (scope {grant.who_scope})."
        return "Denied."
    if decision == AccessDecision.GRANTED:
        for rule in live_rules:
            if rule.effect == "ALLOW" and _applies(rule, viewer):
                return f"Allowed by a rule on this document (scope {rule.scope})."
        for grant in applicable_grants:
            if grant.effect == "ALLOW":
                return f"Allowed by a library-wide grant (scope {grant.who_scope})."
        return f"Allowed by the subscription tier ({document.access_tier})."
    return f"The document needs {document.access_tier} and the viewer has {viewer.tier}."


def document_select(viewer: Viewer) -> Select:
    """A student-facing document query, already filtered. The only supported entry."""
    return select(ContentDocument).where(document_filter(viewer))
