"""Study planner endpoints - blueprint v3 §18.1 (Planner is P0)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, status
from pydantic import Field, field_validator

from app.core.dependencies import get_request_id
from app.core.envelope import problem, success
from app.core.security import Principal, get_current_principal
from app.schemas.base import DateRef, StrictRequest
from app.services import study_planner
from app.services.study_planner import (
    MAX_DAILY_HOURS,
    ChapterInput,
    PlanInput,
    SubjectInput,
    TaskType,
)

router = APIRouter(tags=["planner"])


class ChapterIn(StrictRequest):
    chapter_id: str = Field(min_length=1, max_length=64)
    weight: float = Field(gt=0, le=1000)


class SubjectIn(StrictRequest):
    subject_id: str = Field(min_length=1, max_length=64)
    syllabus_weight: float = Field(gt=0, le=1000)
    accuracy: float | None = Field(default=None, ge=0, le=1)
    chapters: list[ChapterIn] = Field(min_length=1, max_length=500)


class GeneratePlanIn(StrictRequest):
    exam_date: DateRef
    daily_hours: float = Field(gt=0, le=MAX_DAILY_HOURS)
    level: str = Field(default="INTERMEDIATE", max_length=20)
    subjects: list[SubjectIn] = Field(min_length=1, max_length=30)
    revision_tail_days: int = Field(default=21, ge=0, le=120)
    mock_every_n_days: int = Field(default=21, ge=1, le=90)

    @field_validator("exam_date")
    @classmethod
    def exam_must_be_future(cls, v: date) -> date:
        if v <= date.today():
            raise ValueError("exam_date must be in the future")
        return v

    @field_validator("level")
    @classmethod
    def known_level(cls, v: str) -> str:
        allowed = {"BEGINNER", "INTERMEDIATE", "ADVANCED"}
        if v not in allowed:
            raise ValueError(f"level must be one of {sorted(allowed)}")
        return v

    @field_validator("subjects")
    @classmethod
    def reject_duplicate_subjects(cls, v: list[SubjectIn]) -> list[SubjectIn]:
        ids = [s.subject_id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("subjects contains duplicate subject_id values")
        return v


class ReplanIn(GeneratePlanIn):
    completed_by_chapter: dict[str, int] = Field(default_factory=dict, max_length=2000)


def _to_plan_input(payload: GeneratePlanIn) -> PlanInput:
    return PlanInput(
        today=date.today(),
        exam_date=payload.exam_date,
        daily_minutes=round(payload.daily_hours * 60),
        revision_tail_days=payload.revision_tail_days,
        mock_every_n_days=payload.mock_every_n_days,
        subjects=[
            SubjectInput(
                subject_id=s.subject_id,
                syllabus_weight=s.syllabus_weight,
                accuracy=s.accuracy,
                chapters=[ChapterInput(c.chapter_id, c.weight) for c in s.chapters],
            )
            for s in payload.subjects
        ],
    )


def _task_payload(task: study_planner.PlannedTask) -> dict[str, object]:
    return {
        "date": task.plan_date.isoformat(),
        "subjectId": task.subject_id,
        "chapterId": task.chapter_id,
        "taskType": task.task_type.value,
        "plannedMin": task.planned_min,
    }


@router.post("/planner/generate", status_code=status.HTTP_201_CREATED)
async def generate_plan(
    payload: GeneratePlanIn,
    principal: Principal = Depends(get_current_principal),
):
    """Generate a study plan.

    The response ALWAYS includes ``coverageWarning`` when the syllabus cannot fit.
    The superseded implementation returned a plan allocating minutes beyond real
    calendar capacity and reported no warning, so a student received an
    impossible plan with false assurance. Honest shortfalls are the product
    requirement here, not an error path.
    """
    try:
        plan = study_planner.generate_plan(_to_plan_input(payload))
    except ValueError as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Cannot generate a plan from these inputs",
            detail=str(exc),
            type_slug="planner",
        )

    return success(
        {
            "generatedBy": plan.generated_by,
            "userId": principal.auth_user_id,
            "daysRemaining": plan.days_remaining,
            "coverageDays": plan.coverage_days,
            "bufferDays": plan.buffer_days,
            "scheduledMocks": plan.scheduled_mocks,
            "totalPlannedMinutes": sum(t.planned_min for t in plan.tasks),
            "coverageWarning": (
                {
                    "coverableFraction": plan.coverage_warning.coverable_fraction,
                    "droppedChapterIds": plan.coverage_warning.dropped_chapter_ids,
                    "message": plan.coverage_warning.message,
                }
                if plan.coverage_warning
                else None
            ),
            "days": _group_by_day(plan.tasks),
        },
        request_id=get_request_id(),
    )


@router.post("/planner/replan")
async def replan(
    payload: ReplanIn,
    principal: Principal = Depends(get_current_principal),
):
    """Re-plan forward from today.

    Forward-only: completed chapters are dropped from the remaining schedule and
    historical task rows are never rewritten, so adherence stays measurable.
    """
    try:
        plan = study_planner.replan(_to_plan_input(payload), payload.completed_by_chapter)
    except ValueError as exc:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Cannot re-plan from these inputs",
            detail=str(exc),
            type_slug="planner",
        )

    return success(
        {
            "generatedBy": plan.generated_by,
            "daysRemaining": plan.days_remaining,
            "coverageWarning": (
                {
                    "coverableFraction": plan.coverage_warning.coverable_fraction,
                    "droppedChapterIds": plan.coverage_warning.dropped_chapter_ids,
                    "message": plan.coverage_warning.message,
                }
                if plan.coverage_warning
                else None
            ),
            "days": _group_by_day(plan.tasks),
        },
        request_id=get_request_id(),
    )


def _group_by_day(tasks: list[study_planner.PlannedTask]) -> list[dict[str, object]]:
    grouped: dict[date, list[study_planner.PlannedTask]] = {}
    for t in tasks:
        grouped.setdefault(t.plan_date, []).append(t)

    out: list[dict[str, object]] = []
    for day in sorted(grouped):
        day_tasks = grouped[day]
        out.append(
            {
                "date": day.isoformat(),
                "isBuffer": any(t.task_type is TaskType.BUFFER for t in day_tasks),
                "isRevision": any(t.task_type is TaskType.REVISE for t in day_tasks),
                "plannedMin": sum(t.planned_min for t in day_tasks),
                "tasks": [_task_payload(t) for t in day_tasks],
            }
        )
    return out
