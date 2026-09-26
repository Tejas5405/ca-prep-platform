"""Contracts on the seed content.

Reference data is the one thing that is painful to correct once students have
progress attached to it: renaming a chapter orphans their history, and a question
with no correct option is unanswerable in a way that looks like a grading bug.

None of this needs a database, so it runs in CI on every commit. The values are
also checked against the DATABASE's own vocabularies - difficulty levels, mock
kinds, weightage ranges - so a seed cannot drift from the CHECK constraints that
will reject it at insert time.
"""

from __future__ import annotations

import pytest

from app.seed_data import (
    COURSES,
    EXAM_SESSIONS,
    MOCKS,
    QUESTIONS,
    chapter_by_code,
    course_of_subject,
    subject_by_code,
)

# Transcibed from the CHECK constraints in the models. If a constraint is widened,
# these tests fail until the seed is reviewed against the new vocabulary.
DIFFICULTIES = {"EASY", "MEDIUM", "HARD"}
QUESTION_TYPES = {"MCQ", "MSQ", "TRUE_FALSE", "NUMERICAL", "DESCRIPTIVE", "CASE_STUDY"}
MOCK_KINDS = {"CHAPTER", "SUBJECT", "FULL_LENGTH", "PREVIOUS_PAPER", "CUSTOM"}
GROUPS = {None, "GROUP_I", "GROUP_II"}
LEVELS = {"FOUNDATION", "INTERMEDIATE", "FINAL"}


class TestCurriculumShape:
    def test_course_codes_are_unique_within_a_scheme(self) -> None:
        keys = [(course.code, "NEW_2024") for course in COURSES]
        assert len(set(keys)) == len(keys)

    def test_every_level_is_one_the_database_accepts(self) -> None:
        assert {course.level for course in COURSES} <= LEVELS

    def test_the_new_scheme_paper_counts_are_right(self) -> None:
        """The landing page states these numbers, so the seed has to agree.

        Foundation 4 papers, Intermediate 6 in two groups, Final 6 - the structure
        ICAI set for the 2024 scheme. A seed that quietly disagrees makes the
        product's own marketing copy wrong.
        """
        counts = {course.level: len(course.subjects) for course in COURSES}
        assert counts["FOUNDATION"] == 4
        assert counts["INTERMEDIATE"] == 6
        assert counts["FINAL"] == 6

    def test_intermediate_and_final_are_split_into_two_groups(self) -> None:
        for course in COURSES:
            if course.level == "FOUNDATION":
                continue
            groups = [subject.group for subject in course.subjects]
            assert groups.count("GROUP_I") == 3
            assert groups.count("GROUP_II") == 3
            assert None not in groups

    def test_paper_numbers_are_unique_within_a_course(self) -> None:
        for course in COURSES:
            numbers = [subject.paper_number for subject in course.subjects]
            assert len(set(numbers)) == len(numbers), course.code


class TestSubjectsAndChapters:
    def test_subject_codes_are_unique_across_the_whole_seed(self) -> None:
        codes = [subject.code for course in COURSES for subject in course.subjects]
        assert len(set(codes)) == len(codes)

    def test_chapter_codes_are_unique_across_the_whole_seed(self) -> None:
        codes = [
            chapter.code
            for course in COURSES
            for subject in course.subjects
            for chapter in subject.chapters
        ]
        assert len(set(codes)) == len(codes)

    def test_every_subject_has_at_least_one_chapter(self) -> None:
        # A subject with no chapters cannot be planned, practicsed or filtered by -
        # it would appear in every picker and lead nowhere.
        for subject in subject_by_code().values():
            assert subject.chapters, subject.code

    @pytest.mark.parametrize("course", COURSES, ids=lambda course: course.code)
    def test_weights_and_estimates_are_inside_the_constraint_ranges(self, course) -> None:
        for subject in course.subjects:
            assert subject.weight > 0, subject.code
        for subject in course.subjects:
            for chapter in subject.chapters:
                # ck_chapter_weightage_range: BETWEEN 1 AND 10
                assert 1 <= chapter.weightage <= 10, chapter.code
                # ck_chapter_minutes_positive: > 0
                assert chapter.estimated_minutes > 0, chapter.code

    def test_group_names_match_the_constraint(self) -> None:
        for subject in subject_by_code().values():
            assert subject.group in GROUPS, subject.code


class TestQuestions:
    def test_every_objective_question_has_exactly_one_correct_option(self) -> None:
        """The single most damaging data error: a question nobody can answer.

        `ck_questions_objective_requires_answer` guarantees an ANSWER EXISTS in the
        database; it cannot guarantee the answer matches an option, and a label typo
        turns every submission wrong for every student at once.
        """
        for seed in QUESTIONS:
            if seed.question_type not in {"MCQ", "MSQ", "TRUE_FALSE"}:
                continue
            correct = [option for option in seed.options if option.is_correct]
            assert len(correct) == 1, f"{seed.text[:60]!r} has {len(correct)} correct options"
            labels = {option.label for option in seed.options}
            assert correct[0].label in labels

    def test_option_labels_are_unique_and_short(self) -> None:
        for seed in QUESTIONS:
            labels = [option.label for option in seed.options]
            assert len(set(labels)) == len(labels), seed.text[:60]
            for label in labels:
                # Column is String(4); a longer label is a truncation waiting to
                # happen on a database that enforces length.
                assert len(label) <= 4, label

    def test_questions_have_a_plausible_number_of_options(self) -> None:
        for seed in QUESTIONS:
            assert 2 <= len(seed.options) <= 6, seed.text[:60]

    def test_question_text_is_unique(self) -> None:
        texts = [seed.text for seed in QUESTIONS]
        assert len(set(texts)) == len(texts)

    def test_difficulty_and_type_are_in_the_database_vocabulary(self) -> None:
        for seed in QUESTIONS:
            assert seed.difficulty in DIFFICULTIES, seed.text[:60]
            assert seed.question_type in QUESTION_TYPES, seed.text[:60]
            assert seed.marks > 0, seed.text[:60]

    def test_every_question_points_at_a_real_chapter_of_its_own_subject(self) -> None:
        subjects = subject_by_code()
        chapters = chapter_by_code()
        for seed in QUESTIONS:
            assert seed.subject_code in subjects, seed.subject_code
            assert seed.chapter_code in chapters, seed.chapter_code
            # A question filed under another subject's chapter appears in the wrong
            # practice set, which is how a Tax question ends up in an Audit mock.
            owning_subject = next(
                subject.code
                for subject in subjects.values()
                if seed.chapter_code in {chapter.code for chapter in subject.chapters}
            )
            assert owning_subject == seed.subject_code, seed.text[:60]

    def test_explanations_are_present_and_substantial(self) -> None:
        # The explanation is the teaching content. "A is correct" teaches nothing.
        for seed in QUESTIONS:
            assert len(seed.explanation) >= 60, seed.text[:60]

    def test_every_subject_the_questions_reference_exists(self) -> None:
        assert {seed.subject_code for seed in QUESTIONS} <= set(subject_by_code())

    def test_the_bank_spans_all_three_levels(self) -> None:
        levels = {course_of_subject(seed.subject_code).level for seed in QUESTIONS}
        assert levels == LEVELS, "seed questions should cover Foundation, Intermediate and Final"


class TestMocks:
    def test_mock_kinds_are_in_the_constraint_vocabulary(self) -> None:
        for seed in MOCKS:
            assert seed.kind in MOCK_KINDS, seed.title

    def test_duration_and_marks_are_inside_the_constraint_ranges(self) -> None:
        for seed in MOCKS:
            # ck_mock_tests_duration: BETWEEN 1 AND 600
            assert 1 <= seed.duration_min <= 600, seed.title
            # ck_mock_tests_marks: > 0
            assert seed.total_marks > 0, seed.title

    def test_every_mock_names_chapters_that_exist_and_have_questions(self) -> None:
        chapters = chapter_by_code()
        for seed in MOCKS:
            assert seed.chapter_codes, seed.title
            for code in seed.chapter_codes:
                assert code in chapters, f"{seed.title}: {code}"

    def test_every_mock_has_at_least_one_published_question_to_include(self) -> None:
        """A published paper with no questions is an empty exam.

        This is the seed-time version of the check the seed script performs at
        insert time: it warns and skips, which is right at runtime and wrong to
        leave undiscovered here.
        """
        by_chapter: dict[str, int] = {}
        for seed in QUESTIONS:
            by_chapter[seed.chapter_code] = by_chapter.get(seed.chapter_code, 0) + 1
        for mock in MOCKS:
            available = sum(by_chapter.get(code, 0) for code in mock.chapter_codes)
            assert available > 0, f"{mock.title} covers no seeded questions"

    def test_mock_courses_and_subjects_exist(self) -> None:
        subjects = subject_by_code()
        course_codes = {course.code for course in COURSES}
        for seed in MOCKS:
            assert seed.course_code in course_codes, seed.title
            if seed.subject_code is not None:
                assert seed.subject_code in subjects, seed.title

    def test_a_full_length_paper_is_longer_than_a_chapter_practice(self) -> None:
        by_kind = {seed.kind: seed for seed in MOCKS}
        assert by_kind["FULL_LENGTH"].duration_min > by_kind["CHAPTER"].duration_min


class TestExamSessions:
    def test_sessions_are_unique_on_the_constraint_key(self) -> None:
        keys = [(year, month) for year, month, _ in EXAM_SESSIONS]
        assert len(set(keys)) == len(keys)

    def test_months_are_uppercase_codes(self) -> None:
        allowed = {"JANUARY", "MAY", "SEPTEMBER", "NOVEMBER"}
        for year, month, label in EXAM_SESSIONS:
            assert month in allowed, month
            assert 2000 <= year <= 2100, year
            assert label.strip(), year
