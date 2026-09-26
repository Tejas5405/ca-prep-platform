"""Study planner tests.

Includes the regression suite for the capacity bug found in the earlier
implementation: the planner allocated a slot to every chapter while summing above
real calendar capacity, silently producing an unexecutable plan and reporting no
warning. The tests below assert the invariant directly.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.study_planner import (
    ChapterInput,
    PlanInput,
    SubjectInput,
    TaskType,
    allocate,
    generate_plan,
    replan,
    weakness_multiplier,
)

TODAY = date(2026, 9, 24)
FAR_EXAM = date(2027, 5, 1)  # 219 days


def subject(sid: str, weight: float, accuracy: float | None, chapters: int) -> SubjectInput:
    return SubjectInput(
        subject_id=sid,
        syllabus_weight=weight,
        accuracy=accuracy,
        chapters=[ChapterInput(f"{sid}-c{i + 1}", 1.0) for i in range(chapters)],
    )


class TestWeaknessMultiplier:
    def test_defaults_to_one_without_history(self):
        assert weakness_multiplier(None) == 1.0

    def test_boosts_weak_and_dampens_strong(self):
        assert weakness_multiplier(0.0) == pytest.approx(1.8)
        assert weakness_multiplier(0.5) == pytest.approx(1.4)
        assert weakness_multiplier(1.0) == pytest.approx(1.0)

    def test_clamps_out_of_range_accuracy(self):
        assert weakness_multiplier(-0.5) == 1.8
        assert weakness_multiplier(1.5) == 1.0


class TestLargestRemainderAllocation:
    def test_parts_sum_exactly_to_total(self):
        parts = allocate(1000, [1, 1, 1])
        assert sum(parts) == 1000
        assert parts == [334, 333, 333]

    @pytest.mark.parametrize("total", [7, 100, 999, 12_345])
    def test_no_rounding_drift(self, total):
        parts = allocate(total, [0.31, 0.29, 0.4])
        assert sum(parts) == total
        assert all(isinstance(p, int) for p in parts)

    def test_safe_for_zero_weights(self):
        assert allocate(100, [0, 0]) == [0, 0]
        assert allocate(0, [1, 2]) == [0, 0]

    def test_empty_ratios(self):
        assert allocate(100, []) == []


class TestPlanGeneration:
    def test_rejects_a_past_exam_date(self):
        with pytest.raises(ValueError, match="future"):
            generate_plan(PlanInput(TODAY, date(2026, 1, 1), 240, [subject("TAX", 100, 0.5, 8)]))

    def test_rejects_non_positive_daily_minutes(self):
        with pytest.raises(ValueError, match="positive"):
            generate_plan(PlanInput(TODAY, FAR_EXAM, 0, [subject("TAX", 100, 0.5, 8)]))

    def test_rejects_zero_weight_subjects(self):
        with pytest.raises(ValueError, match="positive weight"):
            generate_plan(PlanInput(TODAY, FAR_EXAM, 240, [subject("X", 0, 0.5, 2)]))

    def test_rejects_an_empty_subject_list(self):
        with pytest.raises(ValueError, match="at least one subject"):
            generate_plan(PlanInput(TODAY, FAR_EXAM, 240, []))

    def test_reserves_roughly_one_buffer_day_in_seven(self):
        plan = generate_plan(PlanInput(TODAY, FAR_EXAM, 240, [subject("TAX", 100, 0.5, 8)]))
        assert plan.buffer_days == plan.days_remaining // 7

    def test_allocates_more_time_to_the_weaker_subject(self):
        plan = generate_plan(
            PlanInput(
                TODAY,
                FAR_EXAM,
                240,
                [subject("TAX", 100, 0.5, 8), subject("FR", 75, 0.9, 6)],
            )
        )
        tax = sum(
            t.planned_min
            for t in plan.tasks
            if t.task_type is TaskType.STUDY and t.subject_id == "TAX"
        )
        fr = sum(
            t.planned_min
            for t in plan.tasks
            if t.task_type is TaskType.STUDY and t.subject_id == "FR"
        )
        assert tax > fr

    def test_schedules_revision_after_first_study(self):
        plan = generate_plan(PlanInput(TODAY, FAR_EXAM, 240, [subject("TAX", 100, 0.5, 8)]))
        first_study = next(t for t in plan.tasks if t.task_type is TaskType.STUDY)
        revisions = [
            t
            for t in plan.tasks
            if t.task_type is TaskType.REVISE and t.chapter_id == first_study.chapter_id
        ]
        assert 0 < len(revisions) <= 4

    def test_schedules_mocks_and_reports_the_count(self):
        plan = generate_plan(PlanInput(TODAY, FAR_EXAM, 240, [subject("TAX", 100, 0.5, 8)]))
        mocks = [t for t in plan.tasks if t.task_type is TaskType.MOCK_TEST]
        assert len(mocks) == plan.scheduled_mocks
        assert plan.scheduled_mocks > 0

    def test_all_tasks_fall_inside_the_window(self):
        plan = generate_plan(PlanInput(TODAY, FAR_EXAM, 240, [subject("TAX", 100, 0.5, 8)]))
        for t in plan.tasks:
            assert TODAY <= t.plan_date <= FAR_EXAM

    def test_no_warning_when_the_syllabus_fits(self):
        plan = generate_plan(
            PlanInput(
                TODAY,
                FAR_EXAM,
                240,
                [subject("TAX", 100, 0.5, 8), subject("FR", 75, 0.9, 6)],
            )
        )
        assert plan.coverage_warning is None


class TestCapacityGate:
    """Regression suite for the unexecutable-plan bug."""

    TIGHT = PlanInput(
        TODAY,
        date(2026, 11, 1),  # 38 days
        60,
        [subject("ALL", 100, 0.5, 120)],
    )

    def test_warns_when_the_syllabus_does_not_fit(self):
        plan = generate_plan(self.TIGHT)
        assert plan.coverage_warning is not None
        assert plan.coverage_warning.coverable_fraction < 1.0
        assert plan.coverage_warning.dropped_chapter_ids
        assert "can cover" in plan.coverage_warning.message

    def test_never_schedules_more_study_minutes_than_capacity(self):
        plan = generate_plan(self.TIGHT)
        study_minutes = sum(t.planned_min for t in plan.tasks if t.task_type is TaskType.STUDY)
        assert study_minutes <= plan.coverage_days * self.TIGHT.daily_minutes

    def test_no_single_day_exceeds_its_budget(self):
        plan = generate_plan(self.TIGHT)
        per_day: dict[date, int] = {}
        for t in plan.tasks:
            if t.task_type is TaskType.STUDY:
                per_day[t.plan_date] = per_day.get(t.plan_date, 0) + t.planned_min
        for d, minutes in per_day.items():
            assert minutes <= self.TIGHT.daily_minutes, f"overloaded on {d}"

    def test_drops_chapters_rather_than_scheduling_an_unusable_slice(self):
        plan = generate_plan(self.TIGHT)
        for t in plan.tasks:
            if t.task_type is TaskType.STUDY:
                assert t.planned_min >= 15

    def test_reports_an_honest_zero_progress_plan_when_nothing_fits(self):
        impossible = PlanInput(
            TODAY,
            date(2026, 10, 20),  # 26 days, swallowed by the revision tail
            30,
            [subject("ALL", 100, 0.5, 40)],
        )
        plan = generate_plan(impossible)
        assert plan.coverage_warning is not None
        assert [t for t in plan.tasks if t.task_type is TaskType.STUDY] == []
        # Mock and buffer scheduling is still useful information.
        assert any(t.task_type is TaskType.BUFFER for t in plan.tasks)

    def test_dropped_chapters_are_listed_for_the_student(self):
        plan = generate_plan(self.TIGHT)
        assert plan.dropped_chapter_ids
        assert set(plan.dropped_chapter_ids) == set(plan.coverage_warning.dropped_chapter_ids)


class TestReplanIsForwardOnly:
    def test_drops_completed_chapters(self):
        plan = replan(
            PlanInput(TODAY, FAR_EXAM, 240, [subject("TAX", 100, 0.5, 4)]),
            {"TAX-c1": 120, "TAX-c2": 60},
        )
        remaining = {t.chapter_id for t in plan.tasks}
        assert "TAX-c1" not in remaining
        assert "TAX-c2" not in remaining
        assert "TAX-c3" in remaining

    def test_returns_an_empty_plan_when_everything_is_done(self):
        plan = replan(
            PlanInput(TODAY, FAR_EXAM, 240, [subject("TAX", 100, 0.5, 2)]),
            {"TAX-c1": 100, "TAX-c2": 100},
        )
        assert plan.tasks == []
        assert plan.coverage_warning is None

    def test_is_deterministic(self):
        inp = PlanInput(TODAY, FAR_EXAM, 240, [subject("TAX", 100, 0.5, 4)])
        a = replan(inp, {})
        b = replan(inp, {})
        assert a.tasks == b.tasks
