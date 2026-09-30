"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter(tags=["campus"])

"""Constants and small helpers shared by more than one sub-module.
Unchanged; only relocated."""

_CHALLENGE = "campus.challenge_answered"


_REVIEW_STATES = {
    "CURRENT",
    "VERIFICATION_REQUIRED",
    "NEEDS_UPDATE",
    "PENDING_ADMIN_REVIEW",
}


_PROCTOR = {"TAB_HIDDEN", "TAB_VISIBLE", "WINDOW_BLUR", "COPY_ATTEMPT", "PASTE_ATTEMPT"}


_DISCUSSION = "Student discussion. Not an ICAI ruling and not a model answer."


_NOTE = "An editor's study note. Not an ICAI extract and not a statute."


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _day_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
