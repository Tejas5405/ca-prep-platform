"""Mock exam request and response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.base import ResponseModel, StrictRequest, TimestampRef

MockKind = Literal["CHAPTER", "SUBJECT", "FULL_LENGTH", "PREVIOUS_PAPER", "CUSTOM"]
SyllabusScheme = Literal["OLD_2016", "NEW_2024", "UNMAPPED"]


class AnswerIn(StrictRequest):
    """One submitted answer.

    THE CLIENT DOES NOT SUPPLY THE ANSWER KEY.

    Earlier versions of this schema required ``correct_option`` and ``marks`` from
    the client, and the scoring service used them verbatim. That made the score
    whatever the browser said it was: a student could POST
    ``correct_option = chosen_option`` for every question and score 100%, and the
    same payload could award themselves any number of marks. The mark and the key
    both come from the question row now, and ``chosen_option`` is the only thing
    the client controls - which is the only thing a client legitimately knows.
    """

    question_id: str = Field(min_length=1, max_length=64)
    #: Option index the student picked. None means left blank.
    chosen_option: int | None = Field(default=None, ge=0, le=9)


class StartAttemptIn(StrictRequest):
    mock_test_id: str = Field(min_length=1, max_length=64)


class SubmitAttemptIn(StrictRequest):
    started_at: TimestampRef
    duration_min: int = Field(ge=1, le=600)
    answers: list[AnswerIn] = Field(default_factory=list, max_length=500)
    other_scores: list[int] = Field(default_factory=list, max_length=10_000)

    @field_validator("answers")
    @classmethod
    def reject_duplicate_questions(cls, v: list[AnswerIn]) -> list[AnswerIn]:
        """Duplicate question ids in one payload are a client bug.

        Silently collapsing them would hide a retry logic error, and the
        database unique constraint would raise later anyway. Rejecting early
        gives the client an actionable 422.
        """
        seen = {a.question_id for a in v}
        if len(seen) != len(v):
            raise ValueError("answers contains duplicate question_id values")
        return v


class ScoreOut(ResponseModel):
    attempt_id: str
    score: int
    max_score: int
    correct: int
    wrong: int
    unattempted: int
    pending_review: int
    rank: int
    total_attempts: int
    percentile: float
    auto_submitted: bool


class MockTestOut(ResponseModel):
    id: str
    title: str
    kind: MockKind
    duration_min: int
    total_marks: int
    syllabus_scheme: SyllabusScheme = "NEW_2024"
    is_premium: bool = False


class FocusAreaOut(ResponseModel):
    chapter_id: str
    chapter_name: str
    accuracy: float
    attempted: int
    weightage: int


class MockReportOut(ResponseModel):
    attempt_id: str
    mock_test_id: str
    score: int
    max_score: int
    percentile: float
    rank: int
    time_taken_seconds: int
    focus_areas: list[FocusAreaOut] = Field(default_factory=list)
    strong_areas: list[FocusAreaOut] = Field(default_factory=list)
