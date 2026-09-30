"""The remaining console writes: questions, papers, syllabus, and the honest gaps.

WHAT THIS FILE IS FOR

Six sidebar rows were grey because a screen behind them would have been a lie.
This file is the part that can be true:

  * a question bank an editor can list and correct, including historical taxation;
  * a mock paper that can be composed, not only published;
  * a syllabus that can be extended, not only seeded;
  * the plan catalogue checkout actually charges, including a saved price;
  * the assistant's real configuration, including the fact that it does not invent;
  * storage, which reports the missing credential instead of a made-up usage chart.

WHAT IT WILL NOT DO

It will not publish a question. That route already exists and records a verifier.
A patch that set ``status=PUBLISHED`` would either violate the database constraint
or skip the person on the record. Archive is the only status change offered here.

A saved price is what ``POST /payments/order`` reads. The tier and the
entitlement list stay in code, so a form cannot invent a feature.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.envelope import problem
from app.models.engagement import PlatformSetting
from app.models.question import Question
from app.schemas.base import StrictRequest

router = APIRouter(tags=["admin"])

"""Constants, id/flag helpers, the LIKE-escape helper, FlagResolve and the
OptionIn model shared by more than one sub-module. Unchanged; only relocated."""

_LEVELS = frozenset({"FOUNDATION", "INTERMEDIATE", "FINAL"})


_SCHEMES = frozenset({"OLD_2016", "NEW_2024", "UNMAPPED"})


_GROUPS = frozenset({"GROUP_I", "GROUP_II"})


_QUESTION_TYPES = frozenset({"MCQ", "MSQ", "TRUE_FALSE", "NUMERICAL", "DESCRIPTIVE", "CASE_STUDY"})


_DIFFICULTIES = frozenset({"EASY", "MEDIUM", "HARD"})


_OBJECTIVE = frozenset({"MCQ", "TRUE_FALSE", "NUMERICAL"})


_MOCK_KINDS = frozenset({"CHAPTER", "SUBJECT", "FULL_LENGTH", "PREVIOUS_PAPER", "CUSTOM"})


_QUESTION_STATUSES = frozenset({"DRAFT", "IN_REVIEW", "APPROVED", "PUBLISHED", "ARCHIVED"})


def _bad_id(name: str) -> Any:
    return problem(
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        title="Invalid id",
        detail=f"{name} must be a UUID.",
        type_slug="validation",
    )


def _parse_uuid(value: str, name: str) -> uuid.UUID | Any:
    try:
        return uuid.UUID(value)
    except ValueError:
        return _bad_id(name)


def _ilike_contains(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _flag(session: AsyncSession, key: str, default: bool = False) -> bool:
    row = (
        await session.execute(select(PlatformSetting).where(PlatformSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        return default
    if isinstance(row.value, dict):
        return bool(row.value.get("value", default))
    if isinstance(row.value, bool):
        return row.value
    return default


def _question_item(row: Question, *, full: bool) -> dict[str, Any]:
    text = row.text if full else row.text[:280]
    return {
        "id": str(row.id),
        "text": text,
        "truncated": not full and len(row.text) > 280,
        "explanation": row.explanation if full else None,
        "questionType": row.question_type,
        "difficulty": row.difficulty,
        "marks": row.marks,
        "negativeMarks": float(row.negative_marks),
        "correctAnswer": row.correct_answer if full else None,
        "modelAnswer": row.model_answer if full else None,
        "status": row.status,
        "isHistorical": row.is_historical,
        "financeActYear": row.finance_act_year,
        "disclaimerText": row.disclaimer_text if full else None,
        "courseId": str(row.course_id),
        "subjectId": str(row.subject_id),
        "chapterId": str(row.chapter_id) if row.chapter_id else None,
        "topicId": str(row.topic_id) if row.topic_id else None,
        "year": row.year,
        "isPremium": row.is_premium,
        "source": row.source,
        "createdAt": row.created_at,
    }


class OptionIn(StrictRequest):
    label: str = Field(min_length=1, max_length=4)
    text: str = Field(min_length=1, max_length=4000)


class FlagResolve(StrictRequest):
    status: str
    resolution_note: str | None = Field(default=None, max_length=2000)


_FLAG_STATUSES = frozenset({"OPEN", "TRIAGED", "FIXED", "REJECTED"})


def _like_literal(value: str) -> str:
    """Escape a citation so a typed percent sign is not a wildcard."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
