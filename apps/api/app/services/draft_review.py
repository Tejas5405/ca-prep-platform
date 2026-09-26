"""Draft review: turning an extracted draft into a real question.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE

Approving a draft does NOT publish a question. Promotion creates a row with
status DRAFT and no verifier. Publication is a separate act with its own
constraint (``ck_questions_published_requires_verifier``). An editor clearing an
OCR backlog is doing triage, not signing off content for students - conflating
the two would let a mis-OCR'd marks value reach a student's scorecard on the
strength of one click.

WHY VALIDATION IS DUPLICATED FROM THE DATABASE

Every rule here mirrors a CHECK constraint in ``app/models/question.py``. That
duplication is deliberate: without it, a bad approval surfaces as an
IntegrityError, which reaches the editor as a 500 with a Postgres constraint name.
With it, the editor gets "correct_answer is required for MCQ" and can fix it. The
two must be kept in step - ``tests/test_draft_review.py`` asserts the rule set
against the constraints it mirrors, so drift fails the suite rather than
production.

WHY TYPE AND MARKS ARE REQUIRED, NOT INFERRED

The draft table stores ``detected_question_type`` and ``detected_marks``. They are
advisory, and this module refuses to use them as defaults. ``marks`` drives
scoring: a regex that reads "(10 Marks)" when the paper says "(1 mark)" marks
every student wrong who attempts it, silently, forever. Requiring the editor to
state the value costs one click in a UI that prefills from the detection - and
what gets stored is what a human confirmed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.models.enums import ContentStatus, Difficulty, QuestionType, SyllabusScheme

logger = logging.getLogger(__name__)

#: Mirrors ck_questions_objective_requires_answer. Note that MSQ is absent from
#: the database constraint, so it is absent here. That is not an oversight to
#: "fix" in one place only: changing this set without changing the constraint
#: means the API accepts what the database then rejects.
OBJECTIVE_TYPES_REQUIRING_ANSWER = frozenset(
    {
        QuestionType.MCQ.value,
        QuestionType.TRUE_FALSE.value,
        QuestionType.NUMERICAL.value,
    }
)

#: Types that carry choices.
OPTION_TYPES = frozenset({QuestionType.MCQ.value, QuestionType.MSQ.value})

#: The only review status an approval or rejection may act on.
PROMOTABLE_STATUS = "PENDING"

#: Statuses that mean a human has already decided. Used to produce a clear
#: conflict rather than a second question.
DECIDED_STATUSES = frozenset({"APPROVED", "REJECTED", "DUPLICATE", "MERGED"})

#: Every value the review_status CHECK constraint permits, derived from the two
#: sets above rather than written out a third time. The queue filter validates
#: against this, so a status can never be accepted by the API and then rejected by
#: the database.
ALL_REVIEW_STATUSES = DECIDED_STATUSES | {PROMOTABLE_STATUS}

#: ck_questions_year
MIN_YEAR, MAX_YEAR = 1990, 2100


# ===================================================================== errors


class PromotionError(Exception):
    """A promotion that cannot proceed, with a message safe to show an editor."""


class DraftNotFound(PromotionError):
    pass


class DraftAlreadyDecided(PromotionError):
    """Raised when the draft was approved or rejected already, including by a
    concurrent request that won the race."""


class PlacementNotFound(PromotionError):
    pass


# ====================================================================== value types


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class OptionInput:
    label: str
    text: str
    is_correct: bool = False


@dataclass(frozen=True, slots=True)
class PromotionInput:
    """What the reviewing editor supplied.

    Placement is taken from the editor, not from the extraction. The segmenter
    deliberately never guesses a subject (nothing student-facing reads a guess),
    so this is the point where placement is decided.
    """

    subject_id: UUID
    question_type: str
    marks: int
    chapter_id: UUID | None = None
    topic_id: UUID | None = None
    difficulty: str = Difficulty.MEDIUM.value
    negative_marks: float = 0.0
    correct_answer: str | None = None
    model_answer: str | None = None
    explanation: str | None = None
    options: tuple[OptionInput, ...] = ()
    year: int | None = None
    attempt_id: UUID | None = None
    is_historical: bool = False
    finance_act_year: str | None = None
    disclaimer_text: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DraftState:
    """The draft as stored, plus the provenance of the job that produced it."""

    draft_id: UUID
    job_id: UUID
    text: str
    review_status: str
    source_page: int | None = None
    detection_confidence: float | None = None
    detected_marks: int | None = None
    detected_question_type: str | None = None
    detected_year: int | None = None
    storage_path: str | None = None
    bucket: str | None = None


@dataclass(frozen=True, slots=True)
class DraftSummary:
    """A row in the QA worklist.

    Deliberately WITHOUT the full draft text. The list is a queue of decisions, and
    a queue that ships every candidate question in full is a payload that grows
    with the backlog; the editor opens one draft to read it. ``preview`` is a
    truncation for scanning, and it is generated on the server so every client
    truncates at the same place.
    """

    draft_id: UUID
    job_id: UUID
    review_status: str
    preview: str
    source_page: int | None
    detected_year: int | None
    detected_attempt: str | None
    detected_marks: int | None
    detected_question_type: str | None
    detection_confidence: float | None
    created_at: datetime


#: How much of a draft the worklist shows. Long enough to recognise a question,
#: short enough that a page of twenty stays one screen of text.
PREVIEW_CHARS = 160


def preview_of(text: str, limit: int = PREVIEW_CHARS) -> str:
    """A one-line preview, truncated on a word boundary where there is one.

    Collapses whitespace because extraction produces hard-wrapped lines sized for
    a printed PDF column, not for a table cell - without this every preview is a
    few words followed by an ellipsis.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    clipped = collapsed[:limit]
    # Do not cut mid-word, but do not search so far back that a long token leaves
    # the preview empty.
    space = clipped.rfind(" ")
    if space > limit // 2:
        clipped = clipped[:space]
    return clipped + "\u2026"


@dataclass(frozen=True, slots=True)
class ResolvedPlacement:
    """Placement after checking it exists and is internally consistent.

    The chapter is RESOLVED, not echoed back. An editor may file a question under
    a topic without restating its chapter; the store derives the chapter from the
    topic's own chain. Returning the input instead would leave the question with a
    topic and no chapter, so it would appear under a topic but be missing from
    every chapter-level report and filter.
    """

    course_id: UUID
    chapter_id: UUID | None = None
    #: Inherited from the course. Without this the promoted question would fall
    #: back to UNMAPPED, and the scheme audit would flag every ingested question
    #: as unclassified - the SA-01 problem, reintroduced by the back door.
    syllabus_scheme: str = SyllabusScheme.UNMAPPED.value


@dataclass(frozen=True, slots=True)
class PromoteResult:
    question_id: UUID
    draft_id: UUID
    application_marks: int = field(default=0)


class DraftReviewStore(Protocol):
    """Persistence for review decisions.

    ``promote`` is required to be ATOMIC: the question insert, its options, and
    the draft status update must commit together or not at all. A question row
    with a still-PENDING draft appears twice in the review queue and races a
    second approval; a decided draft with no question loses the content.
    """

    async def load_draft(self, draft_id: UUID) -> DraftState | None: ...

    async def list_drafts(
        self,
        *,
        review_status: str = "PENDING",
        job_id: UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DraftSummary], int]: ...

    async def resolve_placement(
        self,
        subject_id: UUID,
        chapter_id: UUID | None,
        topic_id: UUID | None,
    ) -> ResolvedPlacement | None: ...

    async def promote(
        self,
        draft_id: UUID,
        *,
        question: dict[str, Any],
        options: list[dict[str, Any]],
        review: dict[str, Any],
    ) -> UUID: ...

    async def record_decision(
        self,
        draft_id: UUID,
        *,
        status: str,
        review: dict[str, Any],
    ) -> None: ...


# =================================================================== validation


def validate_promotion(payload: PromotionInput) -> list[FieldError]:
    """Check the payload against the database's own rules.

    Returns every problem rather than the first, so an editor fixing a rejected
    submission sees all of them at once instead of discovering them one per round
    trip.
    """
    errors: list[FieldError] = []

    if payload.question_type not in {t.value for t in QuestionType}:
        errors.append(
            FieldError(
                "question_type",
                f"must be one of {', '.join(sorted(t.value for t in QuestionType))}",
            )
        )
    if payload.difficulty not in {d.value for d in Difficulty}:
        errors.append(
            FieldError("difficulty", f"must be one of {', '.join(d.value for d in Difficulty)}")
        )

    if payload.marks < 1:
        errors.append(FieldError("marks", "must be at least 1"))

    if payload.negative_marks < 0:
        errors.append(FieldError("negative_marks", "cannot be negative"))
    elif payload.negative_marks > payload.marks:
        # Not a database constraint, but a deduction larger than the question is
        # worth is a data-entry error that quietly distorts every score.
        errors.append(
            FieldError(
                "negative_marks",
                f"cannot exceed the question's marks ({payload.marks})",
            )
        )

    if payload.year is not None and not (MIN_YEAR <= payload.year <= MAX_YEAR):
        # ck_questions_year allows NULL but constrains a present value.
        errors.append(FieldError("year", f"must be between {MIN_YEAR} and {MAX_YEAR}, or omitted"))

    # ---- objective questions need an answer -----------------------------
    if (
        payload.question_type in OBJECTIVE_TYPES_REQUIRING_ANSWER
        and not (payload.correct_answer or "").strip()
    ):
        errors.append(
            FieldError(
                "correct_answer",
                f"is required for {payload.question_type} questions",
            )
        )

    # ---- choice questions need usable choices ---------------------------
    if payload.question_type in OPTION_TYPES:
        errors += _validate_options(payload)

    # ---- historical taxation content must carry its disclaimer ----------
    if payload.is_historical and not (payload.disclaimer_text or "").strip():
        errors.append(
            FieldError(
                "disclaimer_text",
                "is required for historical content; a student must be told the "
                "provision may have changed",
            )
        )

    return errors


def _validate_options(payload: PromotionInput) -> list[FieldError]:
    errors: list[FieldError] = []
    options = payload.options

    if len(options) < 2:
        errors.append(
            FieldError("options", "at least two options are required for a choice question")
        )
        return errors

    labels = [opt.label.strip().upper() for opt in options]
    if len(set(labels)) != len(labels):
        # Also enforced by uq_option_question_label.
        errors.append(FieldError("options", "option labels must be unique"))
    if any(not label for label in labels):
        errors.append(FieldError("options", "every option needs a label such as A, B, C"))
    if any(not opt.text.strip() for opt in options):
        errors.append(FieldError("options", "every option needs text"))
    if any(len(label) > 4 for label in labels):
        errors.append(FieldError("options", "option labels are limited to 4 characters"))

    correct = [label for label, opt in zip(labels, options, strict=True) if opt.is_correct]
    if not correct:
        errors.append(FieldError("options", "at least one option must be marked correct"))
    elif payload.question_type == QuestionType.MCQ.value and len(correct) > 1:
        errors.append(
            FieldError(
                "options",
                "a single-answer MCQ cannot have more than one correct option; "
                "use MSQ for multiple selections",
            )
        )

    # The stored answer must be the label of a real option, otherwise the
    # grader compares a student's choice against a string nothing can match.
    answer = (payload.correct_answer or "").strip().upper()
    if answer and answer not in labels and payload.question_type == QuestionType.MCQ.value:
        errors.append(
            FieldError(
                "correct_answer",
                f"must be one of the option labels ({', '.join(labels)})",
            )
        )

    return errors


# ==================================================================== builders


def build_question_row(
    draft: DraftState,
    payload: PromotionInput,
    placement: ResolvedPlacement,
    *,
    actor_id: UUID | None,
) -> dict[str, Any]:
    """Assemble the question row.

    Every NOT NULL column is set explicitly. Relying on a Python-side default
    would work here and fail for anyone inserting through raw SQL or a future
    bulk-import path; the defaults are a convenience, not the contract.

    ``status`` is DRAFT and ``verified_by`` is None ON PURPOSE - see the module
    docstring. ``search_text`` is left None: the FTS index is over
    ``coalesce(search_text, text)``, so the question is searchable immediately
    without duplicating its text into a second column.
    """
    source_path = (
        f"{draft.bucket}/{draft.storage_path}"
        if draft.bucket and draft.storage_path
        else draft.storage_path
    )

    return {
        # placement
        "course_id": placement.course_id,
        "subject_id": payload.subject_id,
        # placement.chapter_id, not payload.chapter_id: the store derives the
        # chapter when only a topic was supplied.
        "chapter_id": placement.chapter_id,
        "topic_id": payload.topic_id,
        # content
        "text": draft.text,
        # Left NULL on purpose: the FTS index is over
        # coalesce(search_text, text), so the question is searchable straight
        # away. Setting it to a copy of `text` would create a second copy that
        # can drift out of step with the first.
        "search_text": None,
        "question_type": payload.question_type,
        "difficulty": payload.difficulty,
        "marks": payload.marks,
        "negative_marks": payload.negative_marks,
        "correct_answer": payload.correct_answer,
        "model_answer": payload.model_answer,
        "explanation": payload.explanation,
        # provenance - this is what makes a published question traceable back to
        # the page of the PDF it came from, which is what a correction or a
        # takedown needs.
        "year": payload.year,
        "attempt_id": payload.attempt_id,
        "source": "ICAI question paper (ingested)",
        "source_page": draft.source_page,
        "source_pdf_path": source_path,
        "extraction_confidence": draft.detection_confidence,
        # taxation currency
        "is_historical": payload.is_historical,
        "finance_act_year": payload.finance_act_year,
        "disclaimer_text": payload.disclaimer_text,
        # publication - DRAFT, deliberately
        "status": ContentStatus.DRAFT.value,
        "syllabus_scheme": placement.syllabus_scheme,
        "is_premium": False,
        "created_by": actor_id,
        "verified_by": None,
        # analytics counters, so a fresh row is not NULL-adjacent
        "times_attempted": 0,
        "times_correct": 0,
    }


def build_option_rows(payload: PromotionInput) -> list[dict[str, Any]]:
    """Options in sequence order, normalised for storage.

    Labels are upper-cased and the sequence is taken from position, so the
    ordering a student sees matches the order the editor entered rather than
    whatever order a dictionary happened to iterate in.
    """
    rows = []
    for index, option in enumerate(payload.options):
        label = option.label.strip().upper()
        rows.append(
            {
                "label": label,
                "text": option.text.strip(),
                "is_correct": bool(option.is_correct),
                "sequence": index,
            }
        )
    return rows


# ================================================================ orchestration


async def promote_draft(
    store: DraftReviewStore,
    draft_id: UUID,
    payload: PromotionInput,
    *,
    actor_id: UUID | None,
) -> PromoteResult:
    """Promote a PENDING draft into a DRAFT question.

    The read here is for validation and error reporting. The authoritative
    re-check happens inside ``store.promote`` under a row lock, because two
    editors can open the same draft simultaneously and this read cannot see the
    other's commit.
    """
    draft = await store.load_draft(draft_id)
    if draft is None:
        raise DraftNotFound(f"No ingestion draft with id {draft_id}")

    if draft.review_status != PROMOTABLE_STATUS:
        raise DraftAlreadyDecided(
            f"Draft {draft_id} is already {draft.review_status}; "
            "a decided draft cannot be promoted again"
        )

    errors = validate_promotion(payload)
    if errors:
        raise PromotionValidationError(errors)

    placement = await store.resolve_placement(
        payload.subject_id, payload.chapter_id, payload.topic_id
    )
    if placement is None:
        raise PlacementNotFound(
            "The subject, chapter or topic does not exist, or the chapter/topic "
            "does not belong to the chosen subject"
        )

    question = build_question_row(draft, payload, placement, actor_id=actor_id)
    options = build_option_rows(payload) if payload.question_type in OPTION_TYPES else []

    question_id = await store.promote(
        draft_id,
        question=question,
        options=options,
        review={
            "status": "APPROVED",
            "reviewed_by": actor_id,
            "review_note": payload.note,
        },
    )

    logger.info("Draft %s promoted to question %s by %s", draft_id, question_id, actor_id)
    return PromoteResult(question_id=question_id, draft_id=draft_id)


class PromotionValidationError(PromotionError):
    """One or more database-mirroring rules were violated.

    Carries ``errors`` so the API can emit RFC 7807 field errors the frontend
    already knows how to render inline.
    """

    def __init__(self, errors: list[FieldError]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{e.field}: {e.message}" for e in errors))


async def reject_draft(
    store: DraftReviewStore,
    draft_id: UUID,
    *,
    actor_id: UUID | None,
    status: str,
    note: str | None = None,
) -> None:
    """Record a rejection or duplicate marking.

    No validation beyond the status itself: removing work from the queue has no
    content constraints, and an editor clearing 200 OCR artifacts should not have
    to satisfy a form to say "no".
    """
    if status not in {"REJECTED", "DUPLICATE"}:
        raise PromotionError(f"{status} is not a rejection status")

    draft = await store.load_draft(draft_id)
    if draft is None:
        raise DraftNotFound(f"No ingestion draft with id {draft_id}")
    if draft.review_status != PROMOTABLE_STATUS:
        raise DraftAlreadyDecided(f"Draft {draft_id} is already {draft.review_status}")

    await store.record_decision(
        draft_id,
        status=status,
        review={"reviewed_by": actor_id, "review_note": note},
    )
    logger.info("Draft %s marked %s by %s", draft_id, status, actor_id)
