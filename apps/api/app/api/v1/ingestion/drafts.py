"""Ingestion and QA endpoints - blueprint v3 §11.1.

The flow, and why it is split into two requests:

    1. POST /ingestion/uploads
         validate metadata -> mint a SHORT-LIVED signed upload URL
         -> create the job row in QUEUED (nothing queued yet)

    2. client PUTs the bytes directly to Supabase

    3. POST /ingestion/jobs/{id}/start
         confirm the object exists -> enqueue the RQ job

The split exists because the upload happens BETWEEN the two calls. The API never
sees the file, which is the point: a 50 MB scanned paper would otherwise hit
Render's request-size limit and occupy a worker for the whole transfer.

WHY /start VERIFIES THE OBJECT

Without a check, a client could call /start without ever uploading and the worker
would fail minutes later on a download error that looks like a storage outage.
Confirming existence first turns a confusing async failure into an immediate 409.

WHY ROLE CHECKS ARE ON EVERY ROUTE

The backend holds the Supabase service-role key and Supabase Storage RLS cannot
evaluate Supabase tokens, so these checks are the only authorization
boundary. Uploading source papers is editorial work, not student work.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import Permission, has_permission, require_permission
from app.models.user import User
from app.schemas.base import StrictRequest, UuidRef
from app.services.draft_review import (
    ALL_REVIEW_STATUSES as DRAFT_STATUSES,
)
from app.services.draft_review import (
    DraftAlreadyDecided,
    DraftNotFound,
    OptionInput,
    PlacementNotFound,
    PromotionInput,
    PromotionValidationError,
    promote_draft,
    reject_draft,
)

from ._shared import (
    OptionIn,
    _actor_id,
    _conflict,
    _not_found,
)

router = APIRouter(tags=["ingestion"])

"""The draft review queue and its decision endpoint."""


class ReviewDraftIn(StrictRequest):
    """Approve or reject a draft question.

    Type and marks are REQUIRED on approval, not taken from the extraction's
    ``detected_*`` values. Those are regex reads of OCR text, and ``marks`` drives
    scoring: a wrong value marks every student who attempts the question wrong,
    silently and permanently. The UI prefills both from the detection, so
    confirming them is one click - and what gets stored is what a human confirmed.
    """

    decision: str = Field(pattern="^(APPROVE|REJECT|DUPLICATE)$")

    # ---- required to approve -------------------------------------------
    subject_id: UuidRef | None = None
    question_type: str | None = None
    marks: int | None = Field(default=None, ge=1, le=100)
    chapter_id: UuidRef | None = None
    topic_id: UuidRef | None = None
    year: int | None = Field(default=None, ge=1990, le=2100)
    attempt_id: UuidRef | None = None
    # Optional: widen or correct the extracted quote while reviewing. Omit it
    # and the draft's own extracted quote is promoted unchanged.
    source_quote: str | None = Field(default=None, max_length=2000)

    # ---- content -------------------------------------------------------
    difficulty: str = "MEDIUM"
    negative_marks: float = Field(default=0.0, ge=0)
    correct_answer: str | None = Field(default=None, max_length=500)
    model_answer: str | None = Field(default=None, max_length=10_000)
    explanation: str | None = Field(default=None, max_length=10_000)
    options: list[OptionIn] = Field(default_factory=list)

    # ---- taxation currency ---------------------------------------------
    is_historical: bool = False
    finance_act_year: str | None = Field(default=None, max_length=20)
    disclaimer_text: str | None = Field(default=None, max_length=2000)

    note: str | None = Field(default=None, max_length=1000)

    def to_promotion_input(self) -> PromotionInput:
        """Convert to the service's value type.

        Required-for-approval fields are asserted rather than coerced. The route
        checks them first and returns a 422 listing what is missing, so reaching
        this method with a None means the route's guard was bypassed - a bug worth
        an exception, not a silently-defaulted question.
        """
        assert self.subject_id is not None
        assert self.question_type is not None
        assert self.marks is not None
        return PromotionInput(
            source_quote=self.source_quote,
            subject_id=self.subject_id,
            question_type=self.question_type,
            marks=self.marks,
            chapter_id=self.chapter_id,
            topic_id=self.topic_id,
            difficulty=self.difficulty,
            negative_marks=self.negative_marks,
            correct_answer=self.correct_answer,
            model_answer=self.model_answer,
            explanation=self.explanation,
            options=tuple(
                OptionInput(label=o.label, text=o.text, is_correct=o.is_correct)
                for o in self.options
            ),
            year=self.year,
            attempt_id=self.attempt_id,
            is_historical=self.is_historical,
            finance_act_year=self.finance_act_year,
            disclaimer_text=self.disclaimer_text,
            note=self.note,
        )


@router.get("/ingestion/drafts", summary="Draft questions awaiting review")
async def list_drafts(
    review_status: str = Query(default="PENDING"),
    job_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _caller: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
):
    """QA worklist, oldest first.

    Only editors and above can read drafts. Nothing student-facing reads the
    drafts table at all, which is what makes an unreviewed OCR artifact
    structurally unable to reach a student.

    Each row carries a truncated preview rather than the full draft text: this is
    a queue of decisions, and shipping every candidate question in full makes the
    payload grow with the backlog. The editor opens one draft to read it.
    """
    if review_status.upper() not in DRAFT_STATUSES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown review status",
            detail=f"reviewStatus must be one of {sorted(DRAFT_STATUSES)}.",
            type_slug="validation",
            errors=[{"field": "reviewStatus", "message": "unknown review status"}],
        )

    from app.repositories.draft_review import SqlDraftReviewStore

    rows, total = await SqlDraftReviewStore().list_drafts(
        review_status=review_status.upper(),
        job_id=job_id,
        limit=limit,
        offset=(page - 1) * limit,
        session=session,
    )

    return paginated(
        [
            {
                "draftId": str(row.draft_id),
                "jobId": str(row.job_id),
                "reviewStatus": row.review_status,
                "preview": row.preview,
                "sourcePage": row.source_page,
                # The verbatim excerpt the extraction claims this draft came from.
                # The review UI puts it beside the question: approval is a
                # comparison against the source, not a judgement call. Sent in
                # the LIST response because the review queue is where it is read
                # - a reviewer should not have to fetch each draft to see it.
                "sourceQuote": row.source_quote,
                "detectedYear": row.detected_year,
                "detectedAttempt": row.detected_attempt,
                "detectedMarks": row.detected_marks,
                "detectedQuestionType": row.detected_question_type,
                # Advisory, and labelled as such in the UI: it is what the
                # extractor guessed, which an editor confirms or corrects.
                "detectionConfidence": row.detection_confidence,
                "createdAt": row.created_at,
            }
            for row in rows
        ],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.post("/ingestion/drafts/{draft_id}/review", summary="Approve or reject a draft")
async def review_draft(
    draft_id: uuid.UUID,
    payload: ReviewDraftIn,
    session: AsyncSession = Depends(get_db),
    caller: User = Depends(require_permission(Permission.MANAGE_QUESTIONS)),
):
    """Record a QA decision, and on approval create a real question.

    APPROVE requires the publish permission (Content Manager or above): it creates a
    question row. REJECT and DUPLICATE only remove work from the queue, so an Editor
    may do them - blueprint §8.2, "Edit draft: Yes, Publish: No".

    The created question is DRAFT and has no verifier. Publishing is a separate
    act, gated by ``ck_questions_published_requires_verifier`` - clearing an OCR
    backlog is triage, not content sign-off.
    """
    from app.repositories.draft_review import SqlDraftReviewStore

    actor_id = _actor_id(caller)
    store = SqlDraftReviewStore()

    if payload.decision != "APPROVE":
        try:
            await reject_draft(
                store,
                draft_id,
                actor_id=actor_id,
                status="DUPLICATE" if payload.decision == "DUPLICATE" else "REJECTED",
                note=payload.note,
            )
        except DraftNotFound as exc:
            return _not_found(exc)
        except DraftAlreadyDecided as exc:
            return _conflict(exc)
        return success(
            {"draftId": str(draft_id), "reviewStatus": payload.decision},
            request_id=get_request_id(),
        )

    # ---- approval -------------------------------------------------------
    #
    # Checked HERE rather than as a route dependency because the same route is the
    # editor's reject button, and the two decisions carry different authority in the
    # blueprint's matrix. The role is read from the DATABASE (``caller.role``), not from
    # the token claim the way this used to be: a demotion must take effect on the next
    # request, and a claim minted an hour ago would still say Content Manager.
    if not has_permission(caller.role, Permission.PUBLISH_CONTENT):
        return problem(
            status=status.HTTP_403_FORBIDDEN,
            title="Insufficient permission",
            detail=(
                "Approving a draft creates a question; that is the publish sign-off and "
                "needs PUBLISH_CONTENT (Content Manager or above)."
            ),
            type_slug="forbidden",
        )

    missing = [
        field
        for field, value in (
            ("subject_id", payload.subject_id),
            ("question_type", payload.question_type),
            ("marks", payload.marks),
        )
        if value is None
    ]
    if missing:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Incomplete approval",
            detail=(
                "Approving creates a question, and a question cannot be stored "
                "without placement and marks."
            ),
            type_slug="validation",
            errors=[{"field": name, "message": "required when approving"} for name in missing],
        )

    try:
        result = await promote_draft(
            store, draft_id, payload.to_promotion_input(), actor_id=actor_id
        )
    except DraftNotFound as exc:
        return _not_found(exc)
    except DraftAlreadyDecided as exc:
        # Includes losing a race to a concurrent approval: the store re-checks
        # under a row lock, so this is a real conflict rather than a stale read.
        return _conflict(exc)
    except PlacementNotFound as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown placement",
            detail=str(exc),
            type_slug="validation",
            errors=[{"field": "subject_id", "message": str(exc)}],
        )
    except PromotionValidationError as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid question",
            detail="The draft could not be promoted as submitted.",
            type_slug="validation",
            errors=[{"field": e.field, "message": e.message} for e in exc.errors],
        )

    # 201 via an explicit JSONResponse rather than the decorator: this one route
    # returns 200 for a rejection and 201 for an approval, and the decorator can
    # only carry a single success code. The envelope is built by the same helper
    # either way, so the body shape does not depend on which branch ran.
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=success(
            {
                "draftId": str(result.draft_id),
                "questionId": str(result.question_id),
                "reviewStatus": "APPROVED",
                # Spelled out so a client cannot mistake approval for publication.
                "questionStatus": "DRAFT",
                "published": False,
            },
            request_id=get_request_id(),
        ),
    )
