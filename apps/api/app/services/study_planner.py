"""Study plan generation - RULES_V1, deterministic.

Deliberately not machine learning. Blueprint v3 §18 marks the planner "P0 ...
Implement initially with deterministic scheduling + spaced repetition; add ML
later", and at MVP there is no attempt history to train on. A student who asks
"why four hours of Tax today?" must get a real answer:
syllabus_weight x weakness_multiplier is an answer, a model score is not.

Ported from the superseded TypeScript implementation, retaining the capacity-gate
fix found by that package's test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

REVISION_OFFSETS = (1, 3, 7, 21)
DEFAULT_REVISION_TAIL_DAYS = 21
DEFAULT_MOCK_EVERY_N_DAYS = 21
MIN_REVISION_MINUTES = 30
MIN_CHAPTER_MINUTES = 15
MAX_DAILY_HOURS = 16


class TaskType(str, Enum):
    STUDY = "STUDY"
    REVISE = "REVISE"
    MOCK_TEST = "MOCK_TEST"
    FLASHCARDS = "FLASHCARDS"
    BUFFER = "BUFFER"


@dataclass(frozen=True)
class ChapterInput:
    chapter_id: str
    weight: float


@dataclass(frozen=True)
class SubjectInput:
    subject_id: str
    syllabus_weight: float
    chapters: list[ChapterInput]
    accuracy: float | None = None


@dataclass(frozen=True)
class PlanInput:
    today: date
    exam_date: date
    daily_minutes: int
    subjects: list[SubjectInput]
    mock_every_n_days: int = DEFAULT_MOCK_EVERY_N_DAYS
    revision_tail_days: int = DEFAULT_REVISION_TAIL_DAYS


@dataclass(frozen=True)
class PlannedTask:
    plan_date: date
    subject_id: str | None
    chapter_id: str | None
    task_type: TaskType
    planned_min: int


@dataclass(frozen=True)
class CoverageWarning:
    coverable_fraction: float
    dropped_chapter_ids: list[str]
    message: str
    preserved_history: bool = True


@dataclass(frozen=True)
class PlanResult:
    generated_by: str
    days_remaining: int
    coverage_days: int
    buffer_days: int
    tasks: list[PlannedTask]
    scheduled_mocks: int
    coverage_warning: CoverageWarning | None = None
    dropped_chapter_ids: list[str] = field(default_factory=list)


def weakness_multiplier(accuracy: float | None) -> float:
    """clamp(1 + (1 - accuracy) * 0.8, 0.8, 1.8)."""
    if accuracy is None:
        return 1.0
    clamped = min(1.0, max(0.0, accuracy))
    return min(1.8, max(0.8, 1 + (1 - clamped) * 0.8))


def allocate(total: int, ratios: list[float]) -> list[int]:
    """Largest-remainder allocation.

    Guarantees the parts sum EXACTLY to ``total``. Naive rounding loses or gains
    minutes, which in a study plan means telling a student they have more time
    than they do.
    """
    if not ratios:
        return []
    ratio_sum = sum(ratios)
    if ratio_sum <= 0:
        return [0] * len(ratios)

    raw = [(r / ratio_sum) * total for r in ratios]
    floors = [int(r) for r in raw]
    remainder = total - sum(floors)

    order = sorted(
        range(len(raw)),
        key=lambda i: (-(raw[i] - floors[i]), i),
    )
    out = list(floors)
    for i in order:
        if remainder <= 0:
            break
        out[i] += 1
        remainder -= 1
    return out


def generate_plan(plan_input: PlanInput) -> PlanResult:
    today = plan_input.today
    exam_date = plan_input.exam_date
    daily_minutes = plan_input.daily_minutes
    subjects = plan_input.subjects

    days_remaining = (exam_date - today).days
    if days_remaining <= 0:
        raise ValueError("exam_date must be in the future")
    if daily_minutes <= 0:
        raise ValueError("daily_minutes must be positive")
    if not subjects:
        raise ValueError("at least one subject is required")

    buffer_days = days_remaining // 7
    study_days = days_remaining - buffer_days
    coverage_days = max(0, study_days - plan_input.revision_tail_days)
    capacity_minutes = coverage_days * daily_minutes

    weighted = [(s, s.syllabus_weight * weakness_multiplier(s.accuracy)) for s in subjects]
    total_weight = sum(w for _, w in weighted)
    if total_weight <= 0:
        raise ValueError("subjects must carry positive weight")

    # Chapter-level demand for each subject, summing exactly to its allocation.
    demand: list[tuple[SubjectInput, list[tuple[str, int]]]] = []
    for subject, weight in weighted:
        subject_minutes = round((weight / total_weight) * capacity_minutes)
        chapters = subject.chapters
        chapter_sum = sum(c.weight for c in chapters) or 1.0
        parts = allocate(subject_minutes, [c.weight / chapter_sum for c in chapters])
        demand.append((subject, [(c.chapter_id, parts[i]) for i, c in enumerate(chapters)]))

    # Capacity gate. A chapter is coverable only if it can receive at least
    # MIN_CHAPTER_MINUTES. Anything beyond that is dropped explicitly and
    # reported. Without this gate the planner happily emits a 12-minute slice
    # per chapter and claims the whole syllabus is covered.
    coverable: list[tuple[SubjectInput, list[tuple[str, int]]]] = []
    dropped_chapter_ids: list[str] = []
    total_chapters = 0
    coverable_chapters = 0

    for subject, chapter_parts in demand:
        total_chapters += len(chapter_parts)
        kept = [(cid, m) for cid, m in chapter_parts if m >= MIN_CHAPTER_MINUTES]
        dropped = [cid for cid, m in chapter_parts if m < MIN_CHAPTER_MINUTES]
        if kept:
            coverable.append((subject, kept))
            coverable_chapters += len(kept)
        dropped_chapter_ids.extend(dropped)

    coverage_warning: CoverageWarning | None = None
    if dropped_chapter_ids:
        fraction = 1.0 if total_chapters == 0 else coverable_chapters / total_chapters
        coverage_warning = CoverageWarning(
            coverable_fraction=round(fraction, 3),
            dropped_chapter_ids=dropped_chapter_ids,
            message=(
                f"At {round(daily_minutes / 60, 1)} h/day you can cover "
                f"{round(fraction * 100)}% of the syllabus before the revision "
                f"tail. {len(dropped_chapter_ids)} of {total_chapters} chapters do "
                "not fit; they are listed so you can extend hours deliberately "
                "rather than discover the shortfall in the exam."
            ),
        )

    all_tasks = _non_study_tasks(today, exam_date, days_remaining, plan_input.mock_every_n_days)
    scheduled_mocks = _count_mocks(days_remaining, plan_input.mock_every_n_days)

    if not coverable:
        return PlanResult(
            generated_by="RULES_V1",
            days_remaining=days_remaining,
            coverage_days=coverage_days,
            buffer_days=buffer_days,
            tasks=sorted(all_tasks, key=lambda t: (t.plan_date, t.task_type.value)),
            scheduled_mocks=scheduled_mocks,
            coverage_warning=coverage_warning,
            dropped_chapter_ids=dropped_chapter_ids,
        )

    study_dates = _build_study_dates(today, coverage_days, buffer_days)
    tasks: list[PlannedTask] = []

    day_index = 0
    day_remaining = daily_minutes
    for subject, chapter_parts in coverable:
        for chapter_id, minutes in chapter_parts:
            remaining = minutes
            while remaining > 0:
                if day_index >= len(study_dates):
                    break
                slice_minutes = min(remaining, day_remaining)
                tasks.append(
                    PlannedTask(
                        plan_date=study_dates[day_index],
                        subject_id=subject.subject_id,
                        chapter_id=chapter_id,
                        task_type=TaskType.STUDY,
                        planned_min=slice_minutes,
                    )
                )
                remaining -= slice_minutes
                day_remaining -= slice_minutes
                if day_remaining == 0:
                    day_index += 1
                    day_remaining = daily_minutes

    # Revision at +1/+3/+7/+21 days after each chapter's first study date.
    first_study: dict[str, date] = {}
    subject_of_chapter: dict[str, str] = {}
    for t in tasks:
        if t.task_type is not TaskType.STUDY or t.chapter_id is None:
            continue
        if t.subject_id:
            subject_of_chapter[t.chapter_id] = t.subject_id
        first_study.setdefault(t.chapter_id, t.plan_date)

    for chapter_id, first_date in first_study.items():
        for offset in REVISION_OFFSETS:
            d = first_date + timedelta(days=offset)
            if (d - today).days <= 0 or (exam_date - d).days < 0:
                continue
            tasks.append(
                PlannedTask(
                    plan_date=d,
                    subject_id=subject_of_chapter.get(chapter_id),
                    chapter_id=chapter_id,
                    task_type=TaskType.REVISE,
                    planned_min=MIN_REVISION_MINUTES,
                )
            )

    tasks.extend(all_tasks)

    return PlanResult(
        generated_by="RULES_V1",
        days_remaining=days_remaining,
        coverage_days=coverage_days,
        buffer_days=buffer_days,
        tasks=sorted(tasks, key=lambda t: (t.plan_date, t.task_type.value)),
        scheduled_mocks=scheduled_mocks,
        coverage_warning=coverage_warning,
        dropped_chapter_ids=dropped_chapter_ids,
    )


def replan(plan_input: PlanInput, completed_by_chapter: dict[str, int]) -> PlanResult:
    """Forward-only re-plan.

    Completed history is immutable; only remaining chapters are rescheduled.
    Attempt records are never rewritten, so adherence history stays measurable.
    """
    reduced = [
        SubjectInput(
            subject_id=s.subject_id,
            syllabus_weight=s.syllabus_weight,
            accuracy=s.accuracy,
            chapters=[c for c in s.chapters if completed_by_chapter.get(c.chapter_id, 0) <= 0],
        )
        for s in plan_input.subjects
    ]
    reduced = [s for s in reduced if s.chapters]

    if not reduced:
        days_remaining = (plan_input.exam_date - plan_input.today).days
        return PlanResult(
            generated_by="RULES_V1",
            days_remaining=days_remaining,
            coverage_days=0,
            buffer_days=days_remaining // 7,
            tasks=[],
            scheduled_mocks=0,
            coverage_warning=None,
        )

    return generate_plan(
        PlanInput(
            today=plan_input.today,
            exam_date=plan_input.exam_date,
            daily_minutes=plan_input.daily_minutes,
            subjects=reduced,
            mock_every_n_days=plan_input.mock_every_n_days,
            revision_tail_days=plan_input.revision_tail_days,
        )
    )


def _non_study_tasks(
    today: date, exam_date: date, days_remaining: int, mock_every: int
) -> list[PlannedTask]:
    tasks: list[PlannedTask] = []

    d = 7
    while d < days_remaining:
        tasks.append(
            PlannedTask(
                plan_date=today + timedelta(days=d),
                subject_id=None,
                chapter_id=None,
                task_type=TaskType.MOCK_TEST,
                planned_min=180,
            )
        )
        d += mock_every

    # Buffer as the 7th day of each 7-day block: a predictable recovery day.
    i = 0
    while i < days_remaining:
        d_date = today + timedelta(days=i + 6)
        if (exam_date - d_date).days >= 0:
            tasks.append(
                PlannedTask(
                    plan_date=d_date,
                    subject_id=None,
                    chapter_id=None,
                    task_type=TaskType.BUFFER,
                    planned_min=0,
                )
            )
        i += 7

    return tasks


def _count_mocks(days_remaining: int, mock_every: int) -> int:
    count = 0
    d = 7
    while d < days_remaining:
        count += 1
        d += mock_every
    return count


def _build_study_dates(today: date, coverage_days: int, buffer_days: int) -> list[date]:
    dates: list[date] = []
    taken = 0
    for i in range(coverage_days + buffer_days):
        if taken >= coverage_days:
            break
        if (i + 1) % 7 == 0:  # buffer slot
            continue
        dates.append(today + timedelta(days=i))
        taken += 1
    return dates
