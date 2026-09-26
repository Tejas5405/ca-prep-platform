"""Publishing: the end of the editorial chain, and the only way content ships.

WHY THIS MODULE EXISTS

Everything upstream stops one step short of a student on purpose. Extraction never
publishes, and approving a draft creates a question in DRAFT with no verifier -
"clearing an OCR backlog is triage, not content sign-off". That design is only
sound if the final step EXISTS, and until now it did not: `POST
/admin/questions/{id}/publish` was missing entirely, and the mock equivalent was a
canned 200 that wrote nothing. The pipeline could produce a question and no route
could publish it, so the honest description was not "content requires review" but
"content can never ship".

WHAT "PUBLISH" HAS TO MEAN

  * **A named human is recorded.** ``verified_by`` is set to the CALLER'S platform
    row, resolved from their token. Not a body field, and not nullable: the
    database enforces ``status <> 'PUBLISHED' OR verified_by IS NOT NULL``, and a
    verifier the client could name would be an audit trail that proves nothing.
  * **The content is complete enough to mark.** An objective question with no
    correct option cannot be published, because it cannot be scored - shipping it
    would produce a question every student answers correctly and a report that
    calls them wrong. The repository re-checks this against the database rather
    than trusting the draft that produced it.
  * **Publishing twice is a conflict, not a no-op.** A second publish silently
    succeeding would overwrite the verifier's name with whoever clicked second. The
    separation matters on a content team: "who signed this off" must have one
    answer.
  * **A mock cannot publish empty.** A paper whose questions are unpublished, or
    which has none, would open for a student and score zero out of zero.

Transactional shape: the row update, the verifier stamp and the state change commit
together, because a published row with no verifier violates a CHECK and would fail
at commit anyway - better to make that impossible by construction than to rely on
the constraint firing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.models.enums import ContentStatus

logger = logging.getLogger(__name__)

#: Statuses a publish may act on. PUBLISHED is excluded so the second click is a
#: conflict; ARCHIVED is excluded so republishing retracted content is a deliberate
#: act (restore first) rather than a slip.
PUBLISHABLE_STATUSES = frozenset(
    {ContentStatus.DRAFT.value, ContentStatus.IN_REVIEW.value, ContentStatus.APPROVED.value}
)


class PublishError(Exception):
    """Base class, so a route can catch one type and map it to a status."""


class AlreadyPublished(PublishError):
    """The content is already live, with a verifier already recorded."""

    def __init__(self, *, published_by: str | None = None) -> None:
        self.published_by = published_by
        super().__init__(
            "This is already published"
            + (f" (verified by {published_by})" if published_by else "")
            + ". Archiving it first makes a re-publish deliberate."
        )


class NotPublishable(PublishError):
    """The content is in a state that publishing must not overwrite."""

    def __init__(self, *, status: str) -> None:
        self.status = status
        super().__init__(f"A {status} item cannot be published from this route.")


class Incomplete(PublishError):
    """The content cannot be marked, so it cannot be published."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class PublishableQuestion:
    """The slice of a question that decides whether it may go live."""

    question_id: uuid.UUID
    status: str
    question_type: str
    verified_by: uuid.UUID | None
    option_count: int
    correct_option_count: int

    @property
    def is_objective(self) -> bool:
        return self.question_type in {"MCQ", "MSQ", "TRUE_FALSE"}

    def blocker(self) -> str | None:
        """Why this question may not publish, or None.

        Returns the REASON rather than a boolean so the endpoint can tell the
        editor what to fix. \"422 not publishable\" with no explanation leaves the
        only useful next action - set the answer key - invisible.
        """
        if self.status == ContentStatus.PUBLISHED.value:
            return "already published"
        if self.status not in PUBLISHABLE_STATUSES:
            return f"status is {self.status}"
        if self.is_objective:
            if self.option_count == 0:
                return "an objective question with no options cannot be scored"
            if self.correct_option_count == 0:
                return "no option is marked correct, so the question cannot be scored"
            if self.question_type == "MCQ" and self.correct_option_count > 1:
                return (
                    f"{self.correct_option_count} options are marked correct, but a "
                    "single-answer question may have exactly one"
                )
        # A descriptive answer can be published without an option or a key: it is
        # marked by a human, and the report already models that as PENDING_REVIEW.
        return None


@dataclass(frozen=True, slots=True)
class PublishedResult:
    """What a publish did, for the response body."""

    content_id: uuid.UUID
    status: str
    previous_status: str
    verified_by: uuid.UUID
    question_count: int | None = None


class PublishingStore(Protocol):
    """Persistence for the publish step."""

    async def load_publishable_question(
        self, question_id: uuid.UUID
    ) -> PublishableQuestion | None: ...

    async def publish_question(
        self, question_id: uuid.UUID, *, verifier_id: uuid.UUID
    ) -> PublishedResult: ...

    async def publish_mock(
        self, mock_id: uuid.UUID, *, verifier_id: uuid.UUID
    ) -> PublishedResult | None: ...


# ================================================================== validation


def check_publishable(question: PublishableQuestion) -> None:
    """Raise the specific error the route should report.

    A function rather than an inline branch so the rule lives in one place: the unit
    tests assert the reasons, and the endpoint only decides which HTTP status each
    reason becomes.
    """
    if question.status == ContentStatus.PUBLISHED.value:
        raise AlreadyPublished()
    if question.status not in PUBLISHABLE_STATUSES:
        raise NotPublishable(status=question.status)
    blocker = question.blocker()
    if blocker is not None:
        raise Incomplete(blocker)


def describe(question: Any) -> dict[str, Any]:  # pragma: no cover - debugging aid
    """A small dict for logs. Not part of the API contract."""
    return {
        "questionId": str(getattr(question, "question_id", "")),
        "status": getattr(question, "status", None),
        "type": getattr(question, "question_type", None),
    }
