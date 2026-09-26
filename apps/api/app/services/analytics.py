"""Product analytics: the canonical event names and the one write path.

VERIFIED STATE BEFORE THIS MODULE EXISTED

``analytics_events`` did not exist - not as a table, not as a model, not as a single
write. The blueprint listed it (§9.1, "Optional warehouse-lite event log") and the
audit recorded it as "not built". This module and its table are new; nothing here
replaces existing instrumentation because there was none.

DESIGN RULE THAT MATTERS MORE THAN THE EVENT LIST

``record_event`` NEVER FAILS A REQUEST. Instrumentation that can break a student's
answer submission is worse than no instrumentation, and the failure is
self-inflicted: an event write is the least important statement in any transaction
that contains it. So the caller commits its own work first, and a failure here is
logged and swallowed.

That has a consequence worth stating: a test cannot assert "the answer endpoint wrote
an event" by checking the response. It has to read the table. The integration tests
do exactly that, which is also how the absence of these events was proven in the
first place.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import AnalyticsEvent

logger = logging.getLogger(__name__)


class Event:
    """Every event name the platform emits, in one place.

    A STRING CONSTANT, NOT AN ENUM, and the distinction is deliberate: the column is
    a String(80) so that adding an event is a product decision rather than a
    migration, and a Python enum would tempt someone into a DB enum. Names are
    dotted and past tense - ``practice.question_attempted`` reads correctly in a
    dashboard and sorts next to its siblings.
    """

    # --- identity and access
    USER_SIGNED_UP = "auth.user_signed_up"
    USER_SIGNED_IN = "auth.user_signed_in"
    USER_SIGNED_OUT = "auth.user_signed_out"
    ROLE_CHANGED = "admin.role_changed"
    USER_SUSPENDED = "admin.user_suspended"
    USER_REACTIVATED = "admin.user_reactivated"

    # --- learning
    COURSE_OPENED = "learning.course_opened"
    CHAPTER_OPENED = "learning.chapter_opened"
    DOCUMENT_OPENED = "content.document_opened"
    DOCUMENT_DOWNLOADED = "content.document_downloaded"
    DOCUMENT_COMPLETED = "content.document_completed"
    DOCUMENT_SEARCHED = "content.searched"
    QUESTION_ATTEMPTED = "practice.question_attempted"
    PRACTICE_SESSION_STARTED = "practice.session_started"
    MOCK_STARTED = "mock.started"
    MOCK_COMPLETED = "mock.completed"
    MOCK_ABANDONED = "mock.abandoned"
    REVISION_REVIEWED = "revision.card_reviewed"
    DOUBT_ASKED = "doubts.question_asked"
    COLLECTION_CREATED = "collections.created"

    # --- money
    PAYMENT_INITIATED = "payments.initiated"
    PAYMENT_COMPLETED = "payments.completed"
    PAYMENT_FAILED = "payments.failed"
    PLAN_VIEWED = "payments.plans_viewed"

    # --- admin operations
    DOCUMENT_UPLOADED = "admin.document_uploaded"
    DOCUMENT_PROCESSED = "admin.document_processed"
    DOCUMENT_FAILED = "admin.document_failed"
    BULK_UPLOAD_STARTED = "admin.bulk_upload_started"
    ACCESS_RULE_CHANGED = "admin.access_rule_changed"
    SETTING_CHANGED = "admin.setting_changed"
    NOTIFICATION_SENT = "admin.notification_sent"
    BADGE_AWARDED = "gamification.badge_awarded"

    # --- AI (emitted only when a provider is configured; see features.ai_assistant)
    AI_ASSISTANT_QUERIED = "ai.assistant_queried"
    AI_CONTENT_GENERATED = "ai.content_generated"

    #: The set of names the API will accept from a client-side `track` call.
    #:
    #: An explicit allowlist, because the alternative - accepting any string - turns
    #: an analytics table into an unbounded write surface controlled by the browser.
    #: Only events that genuinely originate in the UI and carry no trusted data are
    #: here; anything the server can observe itself is emitted server-side instead.
    CLIENT_EMITTABLE: frozenset[str] = frozenset(
        {
            USER_SIGNED_OUT,
            COURSE_OPENED,
            CHAPTER_OPENED,
            DOCUMENT_OPENED,
            DOCUMENT_DOWNLOADED,
            DOCUMENT_COMPLETED,
            DOCUMENT_SEARCHED,
            PRACTICE_SESSION_STARTED,
            REVISION_REVIEWED,
            PLAN_VIEWED,
            AI_ASSISTANT_QUERIED,
        }
    )


async def record_event(
    session: AsyncSession,
    name: str,
    *,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    properties: dict[str, Any] | None = None,
    commit: bool = False,
) -> AnalyticsEvent | None:
    """Write one event. Returns None (and logs) rather than raising.

    ``commit=False`` by default: the caller is usually inside a transaction of its
    own, and committing here would commit that work as a side effect - the classic
    way an "analytics" helper ends up deciding the fate of a student's answer.
    """
    try:
        event = AnalyticsEvent(
            id=uuid.uuid4(),
            name=name,
            user_id=user_id,
            role=role,
            properties=properties or None,
        )
        session.add(event)
        if commit:
            await session.commit()
        else:
            await session.flush()
        return event
    except Exception:  # noqa: BLE001 - instrumentation must not break the request
        logger.warning("analytics event %s could not be recorded", name, exc_info=True)
        return None
