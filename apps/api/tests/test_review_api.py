"""HTTP-level tests for the draft review endpoint.

The service rules are covered in ``test_draft_review.py``. What is checked here is
the layer above: who is allowed to do what, and whether the failure modes come back
as documented RFC 7807 responses rather than 500s.

The route constructs its store inline (``SqlDraftReviewStore()``), so these tests
monkeypatch that class rather than needing PostgreSQL. That is a deliberate choice
over dependency injection on the route: the store has no per-request state worth
threading through FastAPI's DI, and a test double gives exact control over the
conflict cases - losing a race to a concurrent reviewer is not something you can
reliably provoke against a real database anyway.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.identity import get_current_user
from app.core.security import Role
from app.main import app
from app.models.enums import SyllabusScheme
from app.models.user import User
from app.services.draft_review import (
    DraftAlreadyDecided,
    DraftNotFound,
    DraftState,
    ResolvedPlacement,
)

DRAFT_ID = uuid.uuid4()
SUBJECT_ID = uuid.uuid4()
CHAPTER_ID = uuid.uuid4()
COURSE_ID = uuid.uuid4()
QUESTION_ID = uuid.uuid4()
PATH = f"/api/v1/ingestion/drafts/{DRAFT_ID}/review"


# ============================================================== test doubles


class FakeStore:
    """Stand-in for SqlDraftReviewStore.

    Records the payload it was handed so tests can assert on what would have been
    written, and can be told to raise so the route's error mapping is exercised.
    """

    draft: DraftState | None = None
    placement: ResolvedPlacement | None = ResolvedPlacement(
        course_id=COURSE_ID,
        chapter_id=CHAPTER_ID,
        syllabus_scheme=SyllabusScheme.NEW_2024.value,
    )
    raises: Exception | None = None
    promoted: dict[str, Any] | None = None
    decided: dict[str, Any] | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    async def load_draft(self, draft_id: uuid.UUID) -> DraftState | None:
        if self.raises is not None:
            raise self.raises
        if FakeStore.draft is not None:
            return FakeStore.draft
        return DraftState(
            draft_id=DRAFT_ID,
            job_id=uuid.uuid4(),
            text="Explain the treatment of deductions under section 80C.",
            review_status="PENDING",
            source_page=3,
            detection_confidence=0.91,
            detected_marks=10,
            detected_question_type="DESCRIPTIVE",
            storage_path="originals/u/paper.pdf",
            bucket="question-pdfs",
        )

    async def resolve_placement(self, *args: Any, **kwargs: Any) -> ResolvedPlacement | None:
        return FakeStore.placement

    async def promote(
        self,
        draft_id: uuid.UUID,
        *,
        question: dict[str, Any],
        options: list[dict[str, Any]],
        review: dict[str, Any],
    ) -> uuid.UUID:
        FakeStore.promoted = {
            "draft_id": draft_id,
            "question": question,
            "options": options,
            "review": review,
        }
        return QUESTION_ID

    async def record_decision(
        self, draft_id: uuid.UUID, *, status: str, review: dict[str, Any]
    ) -> None:
        FakeStore.decided = {"draft_id": draft_id, "status": status, "review": review}


@pytest.fixture(autouse=True)
def reset_store():
    FakeStore.promoted = None
    FakeStore.decided = None
    FakeStore.draft = None
    FakeStore.raises = None
    FakeStore.placement = ResolvedPlacement(
        course_id=COURSE_ID, chapter_id=CHAPTER_ID, syllabus_scheme=SyllabusScheme.NEW_2024.value
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def patch_store(monkeypatch):
    import app.repositories.draft_review as module

    monkeypatch.setattr(module, "SqlDraftReviewStore", FakeStore)


ACTOR_ID = uuid.uuid4()


def as_role(role: Role) -> TestClient:
    """A client whose caller is a real `User` row carrying this role.

    OVERRIDDEN AT `get_current_user`, WHICH IS WHAT THE ROUTE NOW READS.

    The route used to guard itself with ``require_role`` on the token principal and
    resolve the acting user with a second query that returned ``None`` on failure.
    It now takes one dependency, ``require_permission(MANAGE_QUESTIONS)``, which loads
    the user row - so the role here has to be the ROW's role, exactly as it is in
    production, and ``ACTOR_ID`` is the id that will be recorded as the question's
    author. Overriding the principal instead would leave the guard reading a role the
    test never set, which is the 403-on-everything shape these tests failed with.
    """
    caller = User(
        id=ACTOR_ID,
        auth_user_id="test-auth-uid-123",
        email="editor@caprep.in",
        role=role.value,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: caller
    return TestClient(app)


def approval_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "decision": "APPROVE",
        "subject_id": str(SUBJECT_ID),
        "question_type": "DESCRIPTIVE",
        "marks": 10,
    }
    body.update(overrides)
    return body


# ================================================================ rejection


class TestRejection:
    def test_an_editor_can_reject(self):
        """Removing work from a queue needs no content authority."""
        resp = as_role(Role.EDITOR).post(
            PATH, json={"decision": "REJECT", "note": "Not a question"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["reviewStatus"] == "REJECT"
        assert FakeStore.decided is not None
        assert FakeStore.promoted is None

    def test_a_duplicate_can_be_marked(self):
        resp = as_role(Role.EDITOR).post(PATH, json={"decision": "DUPLICATE"})
        assert resp.status_code == 200
        assert FakeStore.decided is not None
        assert FakeStore.decided["status"] == "DUPLICATE"

    def test_rejection_stores_no_placement_requirement(self):
        """An editor clearing 200 OCR artifacts should not have to fill a form."""
        resp = as_role(Role.EDITOR).post(PATH, json={"decision": "REJECT"})
        assert resp.status_code == 200

    def test_a_rejection_returns_no_question_id(self):
        resp = as_role(Role.EDITOR).post(PATH, json={"decision": "REJECT"})
        assert "questionId" not in resp.json()["data"]


# ================================================================ authorization


class TestApprovalAuthorization:
    def test_an_editor_cannot_approve(self):
        """Approval creates a question row. An Editor's remit is triage."""
        resp = as_role(Role.EDITOR).post(PATH, json=approval_body())
        assert resp.status_code == 403
        assert FakeStore.promoted is None

    def test_a_student_cannot_approve(self):
        resp = as_role(Role.STUDENT).post(PATH, json=approval_body())
        assert resp.status_code == 403
        assert FakeStore.promoted is None

    def test_a_content_manager_can_approve(self):
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert resp.status_code == 201

    def test_an_admin_can_approve(self):
        resp = as_role(Role.ADMIN).post(PATH, json=approval_body())
        assert resp.status_code == 201

    def test_an_anonymous_caller_is_refused(self):
        """No override: the real dependency runs and finds no token."""
        resp = TestClient(app).post(PATH, json=approval_body())
        assert resp.status_code in (401, 403)
        assert FakeStore.promoted is None


# ============================================================ approval success


class TestApprovalSuccess:
    def test_approval_returns_201_with_the_created_question(self):
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["questionId"] == str(QUESTION_ID)
        assert data["reviewStatus"] == "APPROVED"

    def test_the_response_says_the_question_is_not_published(self):
        """A client must not be able to mistake approval for publication."""
        data = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body()).json()["data"]
        assert data["questionStatus"] == "DRAFT"
        assert data["published"] is False

    def test_the_envelope_shape_holds_for_a_201(self):
        body = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body()).json()
        assert set(body) == {"data", "meta"}
        assert "requestId" in body["meta"]

    def test_the_editor_confirms_the_marks(self):
        as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body(marks=4))
        assert FakeStore.promoted is not None
        assert FakeStore.promoted["question"]["marks"] == 4

    def test_mcq_options_reach_the_store(self):
        resp = as_role(Role.CONTENT_MANAGER).post(
            PATH,
            json=approval_body(
                question_type="MCQ",
                marks=1,
                correct_answer="B",
                options=[
                    {"label": "A", "text": "Section 80C", "is_correct": False},
                    {"label": "B", "text": "Section 80D", "is_correct": True},
                ],
            ),
        )
        assert resp.status_code == 201
        assert FakeStore.promoted is not None
        assert len(FakeStore.promoted["options"]) == 2
        assert [o["label"] for o in FakeStore.promoted["options"]] == ["A", "B"]

    def test_a_descriptive_approval_writes_no_options(self):
        as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert FakeStore.promoted is not None
        assert FakeStore.promoted["options"] == []


# ================================================================== failures


class TestApprovalFailures:
    def test_a_missing_subject_is_a_field_error_not_a_crash(self):
        resp = as_role(Role.CONTENT_MANAGER).post(
            PATH, json={"decision": "APPROVE", "question_type": "DESCRIPTIVE", "marks": 10}
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["status"] == 422
        assert any(e["field"] == "subject_id" for e in body["errors"])

    def test_every_missing_required_field_is_listed(self):
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json={"decision": "APPROVE"})
        assert resp.status_code == 422
        fields = {e["field"] for e in resp.json()["errors"]}
        assert {"subject_id", "question_type", "marks"} <= fields

    def test_a_null_marks_value_cannot_sneak_through(self):
        """marks is nullable in the request shape (it is optional for a rejection)
        but not for an approval. A coerced 0 would also violate marks > 0."""
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body(marks=None))
        assert resp.status_code == 422

    def test_zero_marks_is_rejected(self):
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body(marks=0))
        assert resp.status_code == 422

    def test_an_mcq_without_a_correct_answer_is_a_field_error(self):
        """The database would raise IntegrityError; the editor gets a field name."""
        resp = as_role(Role.CONTENT_MANAGER).post(
            PATH, json=approval_body(question_type="MCQ", marks=1)
        )
        assert resp.status_code == 422
        assert any(e["field"] == "correct_answer" for e in resp.json()["errors"])

    def test_an_mcq_without_options_is_a_field_error(self):
        resp = as_role(Role.CONTENT_MANAGER).post(
            PATH, json=approval_body(question_type="MCQ", marks=1, correct_answer="A")
        )
        assert resp.status_code == 422
        assert any(e["field"] == "options" for e in resp.json()["errors"])

    def test_historical_content_without_a_disclaimer_is_refused(self):
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body(is_historical=True))
        assert resp.status_code == 422
        assert any(e["field"] == "disclaimer_text" for e in resp.json()["errors"])

    def test_a_promotion_never_happens_when_validation_fails(self):
        as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body(marks=0))
        assert FakeStore.promoted is None

    def test_an_unknown_draft_is_a_404(self):
        FakeStore.draft = None
        FakeStore.raises = DraftNotFound("No ingestion draft with id x")
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert resp.status_code == 404
        assert resp.json()["title"] == "Draft not found"

    def test_an_already_decided_draft_is_a_409(self):
        """Not a 200: the caller's intent was not carried out.

        Reporting success would tell an editor their approval landed when another
        reviewer had already rejected the draft.
        """
        FakeStore.draft = DraftState(
            draft_id=DRAFT_ID,
            job_id=uuid.uuid4(),
            text="x",
            review_status="REJECTED",
        )
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert resp.status_code == 409
        assert FakeStore.promoted is None

    def test_a_lost_race_to_a_concurrent_reviewer_is_a_409(self):
        """The store raises this from inside a row lock. It must not surface as a
        500 - two editors on one draft is an ordinary Tuesday, not a server fault."""
        FakeStore.raises = DraftAlreadyDecided("Draft was already APPROVED")
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert resp.status_code == 409

    def test_bad_placement_is_a_field_error(self):
        FakeStore.placement = None
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert resp.status_code == 422
        assert any(e["field"] == "subject_id" for e in resp.json()["errors"])

    def test_placement_not_found_never_reaches_the_store(self):
        FakeStore.placement = None
        as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body())
        assert FakeStore.promoted is None

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        """Strict inbound validation: a typo'd field must not be silently dropped,
        or a client believes it set something that was never read."""
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body(numbr_of_marks=10))
        assert resp.status_code == 422

    def test_a_string_mark_is_refused_rather_than_coerced(self):
        """Pydantic's lax mode coerces "10" to 10. strict=True must not."""
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json=approval_body(marks="10"))
        assert resp.status_code == 422

    def test_an_invalid_decision_is_refused(self):
        resp = as_role(Role.CONTENT_MANAGER).post(PATH, json={"decision": "MAYBE"})
        assert resp.status_code == 422
