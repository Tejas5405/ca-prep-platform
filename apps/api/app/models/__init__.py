"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata``. That matters
more than it looks: Alembic's autogenerate and the schema-contract tests both
walk ``Base.metadata``, so a model file that nothing imports is a table that
silently does not exist as far as migrations are concerned. The symptom is a
model that works in the test suite and is missing in production.

So every model module is imported here, in one place, and
``tests/test_schema_contract.py::TestSchemaSize`` asserts that every
``app/models/*.py`` file is covered - a new file that forgets to register here
fails the suite instead of failing the deploy.
"""

from app.models.base import Base
from app.models.campus import (
    CalendarEvent,
    ExamModeSitting,
    Experiment,
    ExperimentAssignment,
    FormulaEntry,
    ForumPost,
    ForumThread,
    GlossaryEntry,
    MarketplaceListing,
    MentorshipRequest,
    PomodoroSession,
    ProctorEvent,
    StudyGroup,
    StudyGroupMember,
    SupportTicket,
)
from app.models.collection import Collection, CollectionQuestion
from app.models.content import (
    ContentAccessRule,
    ContentDocument,
    ContentGrant,
    DocumentPage,
)
from app.models.curriculum import Chapter, Course, Subject, SubjectComponent, Topic
from app.models.doubt import Doubt, DoubtReply
from app.models.engagement import (
    AnalyticsEvent,
    AuditLog,
    Badge,
    Notification,
    PlatformSetting,
)
from app.models.enums import SyllabusScheme
from app.models.ingestion import IngestionDraft, IngestionJob, RawExtraction
from app.models.planner import StudyPlan, StudyPlanItem
from app.models.progress import (
    DailyActivity,
    MockAttempt,
    MockTest,
    PointsLedger,
    PracticeAttempt,
    Referral,
    SpacedRepetitionCard,
    UserBadge,
    UserQuestionProgress,
)
from app.models.question import (
    ExamSession,
    LawNotice,
    Question,
    QuestionFlag,
    QuestionOption,
    QuestionVersion,
)
from app.models.user import (
    PaymentEvent,
    PaymentGatewayConfig,
    PaymentOrder,
    Subscription,
    User,
    UserProfile,
)

__all__ = [
    "AnalyticsEvent",
    "AuditLog",
    "Badge",
    "Base",
    "CalendarEvent",
    "Chapter",
    "Collection",
    "CollectionQuestion",
    "ContentAccessRule",
    "ContentDocument",
    "ContentGrant",
    "Course",
    "DailyActivity",
    "DocumentPage",
    "Doubt",
    "DoubtReply",
    "ExamModeSitting",
    "ExamSession",
    "Experiment",
    "ExperimentAssignment",
    "FormulaEntry",
    "ForumPost",
    "ForumThread",
    "GlossaryEntry",
    "IngestionDraft",
    "IngestionJob",
    "LawNotice",
    "MarketplaceListing",
    "MentorshipRequest",
    "MockAttempt",
    "MockTest",
    "Notification",
    "PaymentEvent",
    "PaymentGatewayConfig",
    "PaymentOrder",
    "PlatformSetting",
    "PointsLedger",
    "PomodoroSession",
    "PracticeAttempt",
    "ProctorEvent",
    "Question",
    "QuestionFlag",
    "QuestionOption",
    "QuestionVersion",
    "RawExtraction",
    "Referral",
    "SpacedRepetitionCard",
    "StudyGroup",
    "StudyGroupMember",
    "StudyPlan",
    "StudyPlanItem",
    "Subject",
    "SubjectComponent",
    "Subscription",
    "SupportTicket",
    "SyllabusScheme",
    "Topic",
    "User",
    "UserBadge",
    "UserProfile",
    "UserQuestionProgress",
]
