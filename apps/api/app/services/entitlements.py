"""One place that answers "what may this account do right now".

WHY THIS IS NOT JUST A CALL TO ``entitlement_for``

The rule itself lives in ``app.services.billing`` and is pure. Two things around
it are easy to get wrong when each route does them itself:

  1. Reading the subscription. A route that reads the ACTIVE row directly will
     happily return a subscription whose expiry has passed - the expiry check is in
     ``entitlement_for``, not in the SQL. Going through here means the clock is
     consulted every time.
  2. Deciding what a free account gets. The comparison is
     "granted != what FREE grants", so adding an entitlement to the FREE tier
     cannot silently leave every premium account flagged as premium for the wrong
     reason.

Every caller gets the same answer, including the two that must agree: the billing
route that reports entitlements to the client, and the mock route that decides
whether a paper is locked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.billing import SqlBillingStore
from app.services import billing
from app.services.billing import SubStatus, Tier

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from app.services.content_library import Viewer


@dataclass(frozen=True)
class EntitlementSnapshot:
    tier: Tier
    status: SubStatus
    expires_at: datetime | None
    granted: frozenset[str]
    is_premium: bool

    def allows(self, slug: str) -> bool:
        """Is this entitlement granted? The ONLY way a route may ask.

        A route that compares tiers directly ("tier == PREMIUM") breaks the moment
        a third tier is added, and breaks silently: the new tier's users simply
        lose a feature nobody notices is missing until they complain.
        """
        return slug in self.granted

    def as_payload(self) -> dict[str, object]:
        return {
            "tier": self.tier.value,
            "status": self.status.value,
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
            "entitlements": sorted(self.granted),
            "isPremium": self.is_premium,
        }


async def entitlements_for(session: AsyncSession, user_id: uuid.UUID) -> EntitlementSnapshot:
    subscription = await SqlBillingStore(session).active_subscription(user_id)
    now = billing.utcnow()
    if subscription is None:
        tier, sub_status, expires_at = Tier.FREE, SubStatus.ACTIVE, None
    else:
        tier = Tier(subscription.tier)
        sub_status = SubStatus(subscription.status)
        expires_at = subscription.expires_at

    granted = billing.entitlement_for(tier=tier, status=sub_status, expires_at=expires_at, now=now)
    free = billing.entitlement_for(
        tier=Tier.FREE, status=SubStatus.ACTIVE, expires_at=None, now=now
    )
    # EFFECTIVE, not stored. An expired PREMIUM row still says PREMIUM in the
    # database, and reporting that to a client renders a dashboard full of
    # unlocked features that every endpoint then refuses. The stored tier is
    # billing history; what this snapshot answers is "what can this account do".
    effective = tier if granted != free else Tier.FREE
    return EntitlementSnapshot(
        tier=effective,
        status=sub_status,
        expires_at=expires_at,
        granted=granted,
        is_premium=granted != free,
    )


# ---------------------------------------------------------------- viewer bridge


async def resolve_viewer(session: AsyncSession, user: Any) -> Viewer:
    """Build the content-access viewer from the two systems that already exist.

    WHY A BRIDGE RATHER THAN A NEW SYSTEM. This platform already answers two
    questions and must not answer them twice:

      * "what is this account's role?"          -> ``core.security`` (the token claim,
        corrected by ``users.role``)
      * "what has this account paid for?"       -> ``billing.entitlement_for`` via
        ``entitlements_for`` above, which reads the clock rather than trusting a
        status column.

    Content access is the intersection of a role, a tier, an optional plan and a set
    of enrolled courses. Building a fourth concept - "content permissions" - would
    duplicate the subscription decision, and the duplication would be discovered the
    day someone's Premium lapsed and the library still opened for them.

    Courses are read from the MOCK TESTS the student has attempted, which is the only
    enrolment signal the schema currently carries. That is a real limitation and it is
    stated here rather than hidden: a proper enrolments table is the right next step
    if per-course selling is ever enabled. Until then a COURSE-scoped access rule
    matches students who have actually sat a test in that course, which is a superset
    of "none" and a subset of "everyone".
    """
    from sqlalchemy import select

    from app.core.security import Role
    from app.models.progress import MockAttempt, MockTest
    from app.services.content_library import Viewer

    snapshot = await entitlements_for(session, user.id)

    role = str(getattr(user, "role", None) or Role.STUDENT)
    is_staff = role != Role.STUDENT.value

    course_ids: set[uuid.UUID] = set()
    if not is_staff:
        rows = await session.execute(
            select(MockTest.course_id)
            .join(MockAttempt, MockAttempt.mock_test_id == MockTest.id)
            .where(MockAttempt.user_id == user.id, MockTest.course_id.is_not(None))
            .distinct()
        )
        course_ids = {row[0] for row in rows if row[0] is not None}

    return Viewer(
        user_id=user.id,
        role=role,
        tier=snapshot.tier.value,
        # No plan code: PLAN-scoped content rules are inert until plans are sold from
        # a catalogue the subscription records, and inventing one here would grant
        # access on the strength of a guess. Stated as a limitation, not hidden.
        plan_code=None,
        course_ids=frozenset(course_ids),
        is_staff=is_staff,
    )
