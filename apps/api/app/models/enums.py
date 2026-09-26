"""Enumerations shared by models and schemas.

These are declared as Python enums and stored as ``VARCHAR`` with a CHECK
constraint rather than native PostgreSQL ``ENUM`` types. The reason is
operational: adding a value to a native PG enum requires an ``ALTER TYPE`` that
cannot run inside a transaction on older PostgreSQL, so a simple new badge kind
becomes a risky migration. A VARCHAR + CHECK is altered by dropping and
recreating the constraint, which is transactional and safe on a live table.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enum whose value is the member name, for clean JSON output.

    NOTE ON ``UP042``. The linter suggests replacing ``(str, Enum)`` with the
    stdlib ``enum.StrEnum`` added in Python 3.11. The suggestion is declined
    deliberately, in this module and everywhere it is flagged.

    ``StrEnum`` changes two behaviours this codebase depends on:

    1. The string form of a member. Under ``str, Enum`` with an explicit
       ``__str__`` override, ``str(member)`` is the VALUE. ``StrEnum`` hardcodes
       that behaviour instead, so the two spellings look equivalent while the
       intent lives in different places.
    2. ``format()`` and f-string interpolation. SQLAlchemy, Pydantic and the
       ``json`` module each reach the string form by a different route, and a
       silent change in any one of them writes a different value into the
       database than the one that validation approved.

    The churn is not worth the risk on a schema whose CHECK constraints compare
    these exact strings. If it is ever revisited: the full test suite must be
    green AND every affected column needs a migration with a data check.
    """

    def __str__(self) -> str:
        return self.value


class UserRole(StrEnum):
    STUDENT = "STUDENT"
    EDITOR = "EDITOR"
    CONTENT_MANAGER = "CONTENT_MANAGER"
    MODERATOR = "MODERATOR"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class SubscriptionTier(StrEnum):
    FREE = "FREE"
    PREMIUM = "PREMIUM"
    PREMIUM_PLUS = "PREMIUM_PLUS"


class SubscriptionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    PAST_DUE = "PAST_DUE"
    TRIAL = "TRIAL"


class CourseLevel(StrEnum):
    """CA course levels. Foundation matters: v3 §21 selects it as the launch level."""

    FOUNDATION = "FOUNDATION"
    INTERMEDIATE = "INTERMEDIATE"
    FINAL = "FINAL"


class SyllabusScheme(StrEnum):
    """ICAI syllabus scheme a content row belongs to.

    THIS ENUM EXISTS BECAUSE OF A REAL CONTENT-INTEGRITY RISK.

    ICAI replaced the earlier scheme with the New Scheme of Education and
    Training, and CA Final moved from eight papers (including electives) to six
    compulsory papers across two groups. A question bank that mixes papers across
    schemes without labelling them will recommend "Integrated Business Solutions"
    practice to a student sitting the old syllabus, or omit a paper the new
    scheme requires - and neither student nor editor will see anything wrong.

    ``UNMAPPED`` is the default for migrated/legacy rows and is what the admin
    dashboard filters on to find content still needing classification.
    """

    OLD_2016 = "OLD_2016"
    NEW_2024 = "NEW_2024"
    UNMAPPED = "UNMAPPED"


class DocumentKind(StrEnum):
    """What a library document IS, for filtering and for the admin list.

    THE LIST IS THE DATABASE'S CHECK CONSTRAINT, and both come from here.

    It was a bare string with ``max_length=30`` on the API and a hand-written CHECK in
    the migration, and the two had already drifted: the web console offered "Past paper"
    and "Syllabus" while the constraint accepted neither, so an operator choosing the
    most natural kind for a CA preparation library wrote a row the database refused. The
    API did not catch it either - it validated only the length - so the failure surfaced
    as an IntegrityError, i.e. a 500 with a database message in the operator's face.

    PAST_PAPER and SYLLABUS are therefore ADDITIONS to the vocabulary, not mistakes to
    remove: past papers are a primary content type here (§9.2 indexes questions by
    ``year`` for exactly that reason), and a syllabus mapping is a document the library
    genuinely holds. TEST_SERIES and REFERENCE stay: mock papers assembled from the
    question bank, and the bare Acts and standards.
    """

    STUDY_MATERIAL = "STUDY_MATERIAL"
    NOTES = "NOTES"
    PAST_PAPER = "PAST_PAPER"
    QUESTION_BANK = "QUESTION_BANK"
    TEST_SERIES = "TEST_SERIES"
    SYLLABUS = "SYLLABUS"
    REFERENCE = "REFERENCE"
    OTHER = "OTHER"


def sql_in_list(enum_class: type) -> str:
    """``'A','B','C'`` for an enum, so a CHECK constraint and the enum cannot drift.

    Quoted the SQL way (single quotes) and sorted by DECLARATION order, which is the
    order that reads best in a constraint error: the kinds an operator is most likely to
    have meant come first.
    """
    return ",".join(f"'{member.value}'" for member in enum_class)


class ContentStatus(StrEnum):
    """Editorial workflow state (v3 §9.2)."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class QuestionType(StrEnum):
    MCQ = "MCQ"
    MSQ = "MSQ"  # multiple select
    TRUE_FALSE = "TRUE_FALSE"
    NUMERICAL = "NUMERICAL"
    DESCRIPTIVE = "DESCRIPTIVE"
    CASE_STUDY = "CASE_STUDY"


class Difficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class TaskType(StrEnum):
    STUDY = "STUDY"
    REVISE = "REVISE"
    PRACTICE = "PRACTICE"
    MOCK_TEST = "MOCK_TEST"
    BUFFER = "BUFFER"


class MockKind(StrEnum):
    CHAPTER = "CHAPTER"
    SUBJECT = "SUBJECT"
    FULL_LENGTH = "FULL_LENGTH"
    PREVIOUS_PAPER = "PREVIOUS_PAPER"
    CUSTOM = "CUSTOM"


class AttemptStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    AUTO_SUBMITTED = "AUTO_SUBMITTED"
    ABANDONED = "ABANDONED"


class IngestionStage(StrEnum):
    """Pipeline stages from v3 §11.1."""

    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    EXTRACTING = "EXTRACTING"
    QUALITY_GATE = "QUALITY_GATE"
    OCR_FALLBACK = "OCR_FALLBACK"
    SEGMENTING = "SEGMENTING"
    DETECTING_METADATA = "DETECTING_METADATA"
    AWAITING_QA = "AWAITING_QA"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class PointsReason(StrEnum):
    QUESTION_CORRECT = "QUESTION_CORRECT"
    CHAPTER_COMPLETE = "CHAPTER_COMPLETE"
    MOCK_COMPLETE = "MOCK_COMPLETE"
    MOCK_HIGH_SCORE = "MOCK_HIGH_SCORE"
    STREAK_DAY = "STREAK_DAY"
    STREAK_MILESTONE = "STREAK_MILESTONE"
    DAILY_CHALLENGE = "DAILY_CHALLENGE"
    BADGE_AWARDED = "BADGE_AWARDED"
    REFERRAL_SIGNUP = "REFERRAL_SIGNUP"
    REFERRAL_CONVERSION = "REFERRAL_CONVERSION"
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"


class BadgeKind(StrEnum):
    STREAK_7 = "STREAK_7"
    STREAK_30 = "STREAK_30"
    STREAK_100 = "STREAK_100"
    MOCK_MASTER = "MOCK_MASTER"
    SUBJECT_SCHOLAR = "SUBJECT_SCHOLAR"
    EARLY_BIRD = "EARLY_BIRD"
    NIGHT_OWL = "NIGHT_OWL"
    REFERRER = "REFERRER"


class DoubtStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class DoubtSource(StrEnum):
    STUDENT = "STUDENT"
    AI_DRAFT = "AI_DRAFT"


class HistoricalFlag(StrEnum):
    """Taxation content currency helpers (see Question model for the rationale)."""

    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
