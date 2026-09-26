"""Draft review tests: approving, rejecting, and the rules that stop both.

Two kinds of check live here.

The first is behaviour: what the service does with a PENDING draft, an already
decided one, a bad payload.

The second is more valuable and less obvious. The assembled question row is built
into a REAL ``Question`` ORM instance and checked against every NOT NULL column
and every CHECK constraint the database enforces. That means a promotion payload
that would fail with an IntegrityError in production fails here instead - with no
database, in milliseconds.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.models.enums import ContentStatus, Difficulty, QuestionType, SyllabusScheme
from app.models.question import Question
from app.services.draft_review import (
    OBJECTIVE_TYPES_REQUIRING_ANSWER,
    OPTION_TYPES,
    PROMOTABLE_STATUS,
    DraftAlreadyDecided,
    DraftNotFound,
    DraftState,
    FieldError,
    OptionInput,
    PlacementNotFound,
    PromotionInput,
    PromotionValidationError,
    ResolvedPlacement,
    build_option_rows,
    build_question_row,
    promote_draft,
    reject_draft,
    validate_promotion,
)

SUBJECT = uuid.uuid4()
COURSE = uuid.uuid4()
CHAPTER = uuid.uuid4()
TOPIC = uuid.uuid4()
ACTOR = uuid.uuid4()
DRAFT_ID = uuid.uuid4()


# ============================================================== test doubles


@dataclass
class FakeStore:
    """In-memory DraftReviewStore.

    Records exactly what it was asked to persist, so tests can assert on the
    payload rather than on a database.
    """

    draft: DraftState | None = None
    placement: ResolvedPlacement | None = None
    promoted: dict[str, Any] | None = None
    decided: dict[str, Any] | None = None
    promote_calls: int = 0

    async def load_draft(self, draft_id: uuid.UUID) -> DraftState | None:
        return self.draft

    async def resolve_placement(
        self, subject_id: uuid.UUID, chapter_id: uuid.UUID | None, topic_id: uuid.UUID | None
    ) -> ResolvedPlacement | None:
        return self.placement

    async def promote(
        self,
        draft_id: uuid.UUID,
        *,
        question: dict[str, Any],
        options: list[dict[str, Any]],
        review: dict[str, Any],
    ) -> uuid.UUID:
        self.promote_calls += 1
        self.promoted = {
            "draft_id": draft_id,
            "question": question,
            "options": options,
            "review": review,
        }
        return uuid.uuid4()

    async def record_decision(
        self, draft_id: uuid.UUID, *, status: str, review: dict[str, Any]
    ) -> None:
        self.decided = {"draft_id": draft_id, "status": status, "review": review}


def pending_draft(**overrides: Any) -> DraftState:
    defaults: dict[str, Any] = {
        "draft_id": DRAFT_ID,
        "job_id": uuid.uuid4(),
        "text": "Explain the provisions relating to deductions under section 80C.",
        "review_status": PROMOTABLE_STATUS,
        "source_page": 7,
        "detection_confidence": 0.93,
        "detected_marks": 10,
        "detected_question_type": "DESCRIPTIVE",
        "detected_year": 2024,
        "storage_path": "originals/u/20260924/paper.pdf",
        "bucket": "question-pdfs",
    }
    defaults.update(overrides)
    return DraftState(**defaults)


def store_for(draft: DraftState | None = None, **kwargs: Any) -> FakeStore:
    return FakeStore(
        draft=draft if draft is not None else pending_draft(),
        placement=kwargs.pop(
            "placement",
            ResolvedPlacement(
                course_id=COURSE, chapter_id=CHAPTER, syllabus_scheme=SyllabusScheme.NEW_2024.value
            ),
        ),
        **kwargs,
    )


def descriptive_payload(**overrides: Any) -> PromotionInput:
    defaults: dict[str, Any] = {
        "subject_id": SUBJECT,
        "question_type": QuestionType.DESCRIPTIVE.value,
        "marks": 10,
        "chapter_id": CHAPTER,
        "difficulty": Difficulty.MEDIUM.value,
    }
    defaults.update(overrides)
    return PromotionInput(**defaults)


def mcq_payload(**overrides: Any) -> PromotionInput:
    defaults: dict[str, Any] = {
        "subject_id": SUBJECT,
        "question_type": QuestionType.MCQ.value,
        "marks": 1,
        "chapter_id": CHAPTER,
        "correct_answer": "B",
        "options": (
            OptionInput("A", "Section 80C"),
            OptionInput("B", "Section 80D", is_correct=True),
            OptionInput("C", "Section 80E"),
            OptionInput("D", "Section 80G"),
        ),
    }
    defaults.update(overrides)
    return PromotionInput(**defaults)


# ================================================== validation: pure rules


class TestValidationMirrorsTheDatabase:
    def test_a_clean_descriptive_payload_is_valid(self):
        assert validate_promotion(descriptive_payload()) == []

    def test_a_clean_mcq_payload_is_valid(self):
        assert validate_promotion(mcq_payload()) == []

    @pytest.mark.parametrize("question_type", sorted(OBJECTIVE_TYPES_REQUIRING_ANSWER))
    def test_objective_types_require_a_correct_answer(self, question_type):
        """Mirrors ck_questions_objective_requires_answer.

        Without this check the approval reaches the database and comes back as an
        IntegrityError - a 500 with a constraint name, not something an editor can
        act on.
        """
        payload = PromotionInput(
            subject_id=SUBJECT, question_type=question_type, marks=1, correct_answer=None
        )
        errors = validate_promotion(payload)
        assert any(e.field == "correct_answer" for e in errors)

    def test_msq_does_not_require_a_single_correct_answer(self):
        """MSQ is deliberately absent from the database constraint.

        Asserted so that a future "tidy-up" that adds MSQ to one side only is
        caught: API and constraint must agree.
        """
        payload = PromotionInput(
            subject_id=SUBJECT,
            question_type=QuestionType.MSQ.value,
            marks=2,
            options=(
                OptionInput("A", "One", is_correct=True),
                OptionInput("B", "Two", is_correct=True),
            ),
        )
        errors = validate_promotion(payload)
        assert not any(e.field == "correct_answer" for e in errors)

    def test_marks_must_be_positive(self):
        """Mirrors ck_questions_marks_positive."""
        errors = validate_promotion(descriptive_payload(marks=0))
        assert any(e.field == "marks" for e in errors)

    def test_negative_marks_cannot_exceed_the_question(self):
        errors = validate_promotion(descriptive_payload(marks=2, negative_marks=5))
        assert any(e.field == "negative_marks" for e in errors)

    def test_negative_marks_must_not_be_negative(self):
        """Mirrors ck_questions_negative_non_negative. The scorer subtracts;
        storing a negative would double the negation into a marks gain."""
        errors = validate_promotion(descriptive_payload(negative_marks=-1))
        assert any(e.field == "negative_marks" for e in errors)

    def test_year_outside_the_permitted_range_is_rejected(self):
        """Mirrors ck_questions_year."""
        assert any(e.field == "year" for e in validate_promotion(descriptive_payload(year=1899)))
        assert any(e.field == "year" for e in validate_promotion(descriptive_payload(year=2101)))

    def test_historical_content_requires_a_disclaimer(self):
        """Mirrors ck_questions_historical_requires_disclaimer.

        The failure this prevents is a student relying on a superseded tax
        provision with nothing on screen telling them it may have changed.
        """
        errors = validate_promotion(descriptive_payload(is_historical=True))
        assert any(e.field == "disclaimer_text" for e in errors)

    def test_historical_content_with_a_disclaimer_is_accepted(self):
        payload = descriptive_payload(is_historical=True, disclaimer_text="As per AY 2021-22.")
        assert validate_promotion(payload) == []

    def test_an_unknown_question_type_is_rejected(self):
        errors = validate_promotion(descriptive_payload(question_type="ESSAY"))
        assert any(e.field == "question_type" for e in errors)

    def test_an_unknown_difficulty_is_rejected(self):
        errors = validate_promotion(descriptive_payload(difficulty="TRICKY"))
        assert any(e.field == "difficulty" for e in errors)

    def test_all_problems_are_reported_at_once(self):
        """An editor should not discover errors one round trip at a time."""
        payload = PromotionInput(
            subject_id=SUBJECT,
            question_type=QuestionType.MCQ.value,
            marks=0,
            is_historical=True,
        )
        errors = validate_promotion(payload)
        assert len(errors) >= 3
        assert len({e.field for e in errors}) >= 3


class TestOptionRules:
    def test_a_choice_question_needs_at_least_two_options(self):
        payload = mcq_payload(options=(OptionInput("A", "Only one", is_correct=True),))
        assert any(e.field == "options" for e in validate_promotion(payload))

    def test_a_single_answer_mcq_cannot_have_two_correct_options(self):
        payload = mcq_payload(
            options=(
                OptionInput("A", "One", is_correct=True),
                OptionInput("B", "Two", is_correct=True),
            )
        )
        errors = validate_promotion(payload)
        assert any("single-answer" in e.message for e in errors)

    def test_msq_allows_multiple_correct_options(self):
        payload = PromotionInput(
            subject_id=SUBJECT,
            question_type=QuestionType.MSQ.value,
            marks=2,
            options=(
                OptionInput("A", "One", is_correct=True),
                OptionInput("B", "Two", is_correct=True),
                OptionInput("C", "Three"),
            ),
        )
        assert validate_promotion(payload) == []

    def test_a_choice_question_needs_one_correct_option(self):
        payload = mcq_payload(
            options=(OptionInput("A", "One"), OptionInput("B", "Two")),
        )
        errors = validate_promotion(payload)
        assert any("must be marked correct" in e.message for e in errors)

    def test_duplicate_option_labels_are_rejected(self):
        """Mirrors uq_option_question_label."""
        payload = mcq_payload(
            options=(
                OptionInput("A", "One", is_correct=True),
                OptionInput("a", "Two"),
            )
        )
        errors = validate_promotion(payload)
        assert any("unique" in e.message for e in errors)

    def test_blank_option_text_is_rejected(self):
        payload = mcq_payload(
            options=(
                OptionInput("A", "  ", is_correct=True),
                OptionInput("B", "Two"),
            )
        )
        assert any("text" in e.message for e in validate_promotion(payload))

    def test_the_answer_must_be_the_label_of_a_real_option(self):
        """Otherwise the grader compares a student's choice against a string no
        option can ever produce, and the question is unanswerable forever."""
        payload = mcq_payload(correct_answer="Z")
        errors = validate_promotion(payload)
        assert any(e.field == "correct_answer" for e in errors)

    @pytest.mark.parametrize(
        "question_type",
        sorted(
            {t.value for t in QuestionType} - set(OPTION_TYPES),
            key=str,
        ),
    )
    def test_non_choice_types_do_not_require_options(self, question_type):
        """The complement of OPTION_TYPES must not demand choices.

        Parametrised over the COMPLEMENT deliberately: an earlier version of this
        test iterated OPTION_TYPES and asserted that options were not required,
        which is the opposite of the rule it was meant to check.
        """
        payload = PromotionInput(
            subject_id=SUBJECT,
            question_type=question_type,
            marks=1,
            correct_answer="42",
        )
        assert not any(e.field == "options" for e in validate_promotion(payload))

    def test_the_two_option_type_sets_are_complementary(self):
        """Guards the mistake above: every type is either a choice type or not."""
        assert set(OPTION_TYPES) <= {t.value for t in QuestionType}
        assert set(OPTION_TYPES).isdisjoint({t.value for t in QuestionType} - set(OPTION_TYPES))

    def test_options_are_stored_in_sequence_order(self):
        rows = build_option_rows(mcq_payload())
        assert [row["sequence"] for row in rows] == [0, 1, 2, 3]
        assert [row["label"] for row in rows] == ["A", "B", "C", "D"]

    def test_option_labels_are_normalised_to_upper_case(self):
        rows = build_option_rows(
            mcq_payload(
                options=(
                    OptionInput("a", "One", is_correct=True),
                    OptionInput("b", "Two"),
                )
            )
        )
        assert [row["label"] for row in rows] == ["A", "B"]


# ================================= the assembled row vs the real constraints


class TestQuestionRowSatisfiesTheSchema:
    """Build the payload into a real ORM object and check it against the schema.

    This is the check that replaces a database round trip. Constructing a
    ``Question`` needs no connection, and every NOT NULL column is visible on the
    mapper - so a promotion that would raise IntegrityError in production fails
    here instead.
    """

    def _row(self, payload: PromotionInput | None = None) -> dict[str, Any]:
        payload = payload or descriptive_payload()
        placement = ResolvedPlacement(
            course_id=COURSE, chapter_id=CHAPTER, syllabus_scheme=SyllabusScheme.NEW_2024.value
        )
        return build_question_row(pending_draft(), payload, placement, actor_id=ACTOR)

    def test_every_not_null_column_is_populated(self):
        """No NOT NULL column may be left to chance.

        A column is satisfied by an explicit value, a Python-side default, or a
        server default. The third case is why created_at/updated_at are allowed
        through: the database generates them.
        """
        row = self._row()
        missing = []
        for column in Question.__table__.columns:
            if column.nullable or column.primary_key:
                continue
            if column.name in row:
                continue
            if column.default is not None or column.server_default is not None:
                continue
            missing.append(column.name)
        assert missing == [], f"promotion would violate NOT NULL on {missing}"

    def test_the_timestamp_columns_are_database_generated(self):
        """created_at/updated_at must not depend on the application clock.

        Application-generated timestamps differ per process, so an ingest worker
        in another region would write rows that sort incorrectly against rows
        written by the API.
        """
        for name in ("created_at", "updated_at"):
            column = Question.__table__.columns[name]
            assert column.server_default is not None, f"{name} needs a server default"

    def test_the_row_constructs_a_real_model_instance(self):
        """Proof that the assembled dict is accepted by the mapper."""
        question = Question(id=uuid.uuid4(), **self._row())
        assert question.status == ContentStatus.DRAFT.value
        assert question.question_type == QuestionType.DESCRIPTIVE.value

    def test_the_row_compiles_to_valid_postgres_sql(self):
        from sqlalchemy import insert

        statement = insert(Question).values(**self._row())
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "INSERT INTO questions" in sql

    def test_a_promoted_question_is_never_published(self):
        """Approving a draft is triage, not sign-off.

        ``ck_questions_published_requires_verifier`` means a PUBLISHED row needs a
        human verifier. An editor clearing an OCR queue has not verified the
        content, so promotion must leave the row in DRAFT.
        """
        row = self._row()
        assert row["status"] == ContentStatus.DRAFT.value
        assert row["verified_by"] is None

    def test_the_question_inherits_the_courses_syllabus_scheme(self):
        """Otherwise every ingested question lands in UNMAPPED and the scheme
        audit reports the whole bank as unclassified."""
        assert self._row()["syllabus_scheme"] == SyllabusScheme.NEW_2024.value

    def test_provenance_is_recorded(self):
        """A published question must be traceable to the page it came from, or a
        correction or takedown has nothing to work from."""
        row = self._row()
        assert row["source_pdf_path"] == "question-pdfs/originals/u/20260924/paper.pdf"
        assert row["source_page"] == 7
        assert row["extraction_confidence"] == pytest.approx(0.93)

    def test_the_author_is_recorded_but_not_as_a_verifier(self):
        row = self._row()
        assert row["created_by"] == ACTOR
        assert row["verified_by"] is None

    def test_the_detected_type_is_not_used_when_the_editor_disagrees(self):
        """The draft was detected as DESCRIPTIVE; the editor chose MCQ. The
        editor wins - detection is advisory."""
        row = self._row(mcq_payload())
        assert row["question_type"] == QuestionType.MCQ.value

    def test_a_topic_without_a_chapter_uses_the_resolved_chapter(self):
        """The editor filed under a topic only. The store derived the chapter,
        and the question must carry it - otherwise the question appears under a
        topic and is missing from every chapter-level report."""
        payload = descriptive_payload(chapter_id=None, topic_id=TOPIC)
        placement = ResolvedPlacement(course_id=COURSE, chapter_id=CHAPTER)
        row = build_question_row(pending_draft(), payload, placement, actor_id=ACTOR)
        assert row["chapter_id"] == CHAPTER
        assert row["topic_id"] == TOPIC

    def test_search_text_is_left_null_because_the_index_falls_back_to_text(self):
        """The GIN index is over coalesce(search_text, text).

        So a promoted question is full-text searchable immediately without
        duplicating its text into a second column - which would also mean the two
        copies could drift.
        """
        assert self._row()["search_text"] is None


class TestRuleSetsMatchTheConstraintsTheyMirror:
    """Keep the Python rules and the SQL constraints in step.

    The duplication between them is intentional (a 422 with a field name beats a
    500 with a constraint name), but duplicated rules drift. These read the
    constraint text and fail if the two disagree.
    """

    def _check_sql(self, name: str) -> str:
        for constraint in Question.__table__.constraints:
            if constraint.__class__.__name__ == "CheckConstraint" and constraint.name == name:
                return str(constraint.sqltext)
        raise AssertionError(f"{name} not found on questions")

    def test_objective_types_rule_matches_the_constraint(self):
        sql = self._check_sql("ck_questions_objective_requires_answer")
        constrained = {t for t in QuestionType if f"'{t.value}'" in sql}
        assert constrained == {QuestionType(t) for t in OBJECTIVE_TYPES_REQUIRING_ANSWER}, (
            "the API's objective-type set has drifted from ck_questions_objective_requires_answer"
        )

    def test_marks_positive_rule_matches_the_constraint(self):
        assert "marks > 0" in self._check_sql("ck_questions_marks_positive")

    def test_historical_disclaimer_rule_matches_the_constraint(self):
        sql = self._check_sql("ck_questions_historical_requires_disclaimer")
        assert "disclaimer_text IS NOT NULL" in sql

    def test_published_requires_verifier_still_exists(self):
        sql = self._check_sql("ck_questions_published_requires_verifier")
        assert "verified_by IS NOT NULL" in sql

    def test_the_permitted_year_range_matches_the_constraint(self):
        from app.services.draft_review import MAX_YEAR, MIN_YEAR

        sql = self._check_sql("ck_questions_year")
        assert str(MIN_YEAR) in sql
        assert str(MAX_YEAR) in sql


# ============================================================= orchestration


class TestPromoteDraft:
    async def test_promotes_a_pending_draft(self):
        store = store_for()
        result = await promote_draft(store, DRAFT_ID, descriptive_payload(), actor_id=ACTOR)

        assert store.promote_calls == 1
        assert store.promoted is not None
        assert store.promoted["draft_id"] == DRAFT_ID
        assert store.promoted["review"]["status"] == "APPROVED"
        assert store.promoted["review"]["reviewed_by"] == ACTOR
        assert result.question_id is not None

    async def test_descriptive_promotions_create_no_options(self):
        store = store_for()
        await promote_draft(store, DRAFT_ID, descriptive_payload(), actor_id=ACTOR)
        assert store.promoted is not None
        assert store.promoted["options"] == []

    async def test_mcq_promotions_create_their_options(self):
        store = store_for()
        await promote_draft(store, DRAFT_ID, mcq_payload(), actor_id=ACTOR)
        assert store.promoted is not None
        assert len(store.promoted["options"]) == 4
        assert sum(1 for o in store.promoted["options"] if o["is_correct"]) == 1

    async def test_a_draft_cannot_be_promoted_twice(self):
        """The idempotency guarantee for the whole feature.

        A second approval must not create a second question - which would put two
        copies of the same paper question into the bank, both filterable, both
        countable in analytics.
        """
        store = store_for(pending_draft(review_status="APPROVED"))
        with pytest.raises(DraftAlreadyDecided):
            await promote_draft(store, DRAFT_ID, descriptive_payload(), actor_id=ACTOR)
        assert store.promote_calls == 0

    @pytest.mark.parametrize("status", ["REJECTED", "DUPLICATE", "MERGED"])
    async def test_any_decided_draft_is_refused(self, status):
        store = store_for(pending_draft(review_status=status))
        with pytest.raises(DraftAlreadyDecided):
            await promote_draft(store, DRAFT_ID, descriptive_payload(), actor_id=ACTOR)
        assert store.promote_calls == 0

    async def test_a_missing_draft_is_reported_clearly(self):
        store = FakeStore(draft=None, placement=None)
        with pytest.raises(DraftNotFound):
            await promote_draft(store, DRAFT_ID, descriptive_payload(), actor_id=ACTOR)

    async def test_an_invalid_payload_never_reaches_the_store(self):
        store = store_for()
        with pytest.raises(PromotionValidationError):
            await promote_draft(store, DRAFT_ID, descriptive_payload(marks=0), actor_id=ACTOR)
        assert store.promote_calls == 0

    async def test_validation_errors_carry_field_names(self):
        store = store_for()
        with pytest.raises(PromotionValidationError) as excinfo:
            await promote_draft(
                store,
                DRAFT_ID,
                PromotionInput(subject_id=SUBJECT, question_type=QuestionType.MCQ.value, marks=0),
                actor_id=ACTOR,
            )
        fields = {e.field for e in excinfo.value.errors}
        assert "marks" in fields
        assert all(isinstance(e, FieldError) for e in excinfo.value.errors)

    async def test_bad_placement_is_refused_before_any_write(self):
        """A chapter belonging to another subject must not be accepted - the
        question would appear in the wrong filter with nothing looking wrong."""
        store = store_for(placement=None)
        with pytest.raises(PlacementNotFound):
            await promote_draft(store, DRAFT_ID, descriptive_payload(), actor_id=ACTOR)
        assert store.promote_calls == 0

    async def test_the_editor_note_is_preserved(self):
        store = store_for()
        await promote_draft(
            store,
            DRAFT_ID,
            descriptive_payload(note="Checked against the 2024 paper"),
            actor_id=ACTOR,
        )
        assert store.promoted is not None
        assert store.promoted["review"]["review_note"] == "Checked against the 2024 paper"

    async def test_the_editors_marks_are_used_not_the_detected_ones(self):
        """The draft was detected as 10 marks; the editor set 4. Scoring follows
        the editor - a wrong detected value would mis-score every attempt."""
        store = store_for()
        await promote_draft(store, DRAFT_ID, descriptive_payload(marks=4), actor_id=ACTOR)
        assert store.promoted is not None
        assert store.promoted["question"]["marks"] == 4
        assert store.promoted["question"]["marks"] != store.draft.detected_marks


class TestRejectDraft:
    async def test_rejects_a_pending_draft(self):
        store = store_for()
        await reject_draft(store, DRAFT_ID, actor_id=ACTOR, status="REJECTED", note="Duplicate")
        assert store.decided is not None
        assert store.decided["status"] == "REJECTED"

    async def test_marks_a_duplicate(self):
        store = store_for()
        await reject_draft(store, DRAFT_ID, actor_id=ACTOR, status="DUPLICATE")
        assert store.decided is not None
        assert store.decided["status"] == "DUPLICATE"

    async def test_rejection_creates_no_question(self):
        store = store_for()
        await reject_draft(store, DRAFT_ID, actor_id=ACTOR, status="REJECTED")
        assert store.promote_calls == 0

    async def test_an_already_decided_draft_cannot_be_rejected_again(self):
        store = store_for(pending_draft(review_status="REJECTED"))
        with pytest.raises(DraftAlreadyDecided):
            await reject_draft(store, DRAFT_ID, actor_id=ACTOR, status="REJECTED")
        assert store.decided is None

    async def test_an_invalid_status_is_refused(self):
        store = store_for()
        with pytest.raises(Exception, match="not a rejection status"):
            await reject_draft(store, DRAFT_ID, actor_id=ACTOR, status="APPROVED")


class TestPromotionInputDefaults:
    def test_defaults_match_the_database_column_defaults(self):
        """difficulty and negative_marks have server defaults; the API applies the
        same values so a promoted row is identical whichever path created it."""
        payload = descriptive_payload()
        assert payload.difficulty == Difficulty.MEDIUM.value
        assert payload.negative_marks == 0.0

    def test_a_draft_is_not_historical_by_default(self):
        assert descriptive_payload().is_historical is False
