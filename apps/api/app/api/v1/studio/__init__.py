"""The owner studio: question bank, flags, mock papers, curriculum, plans,
operations, AI triage and law notices.

Split from the former single-file app/api/v1/studio.py. `router` carries the same
paths, methods, tags, names and status codes as before, in the same order - note
that the original router is tagged ["admin"], not ["studio"], and that tag is
part of the published OpenAPI document, so it is reproduced here unchanged."""

from __future__ import annotations

from fastapi import APIRouter

from . import curriculum as _curriculum
from . import flags as _flags
from . import mocks as _mocks
from . import notices as _notices
from . import operations as _operations
from . import plans as _plans
from . import questions as _questions
from . import triage as _triage
from ._shared import FlagResolve, OptionIn
from .curriculum import (
    ChapterWrite,
    CoursePatch,
    CourseWrite,
    NamedPatch,
    SubjectWrite,
    TopicWrite,
    admin_curriculum,
    create_chapter,
    create_course,
    create_subject,
    create_topic,
    update_chapter,
    update_course,
    update_subject,
    update_topic,
)
from .flags import list_flags, resolve_flag
from .mocks import MockCreate, MockPatch, create_mock, list_mocks, update_mock
from .notices import LawNoticeIn, list_law_notices, record_law_notice
from .operations import ai_configuration, storage_status
from .plans import PlanPriceIn, list_plans, save_plan_price
from .questions import (
    QuestionCreate,
    QuestionPatch,
    QuestionRevise,
    create_question,
    get_question,
    list_questions,
    question_versions,
    revise_question,
    update_question,
)
from .triage import ClassifyIn, classify_question, unclassified_questions

router = APIRouter()
router.routes.extend(_questions.router.routes)
router.routes.extend(_flags.router.routes)
router.routes.extend(_mocks.router.routes)
router.routes.extend(_curriculum.router.routes)
router.routes.extend(_plans.router.routes)
router.routes.extend(_operations.router.routes)
router.routes.extend(_triage.router.routes)
router.routes.extend(_notices.router.routes)

__all__ = [
    "ChapterWrite",
    "ClassifyIn",
    "CoursePatch",
    "CourseWrite",
    "FlagResolve",
    "LawNoticeIn",
    "MockCreate",
    "MockPatch",
    "NamedPatch",
    "OptionIn",
    "PlanPriceIn",
    "QuestionCreate",
    "QuestionPatch",
    "QuestionRevise",
    "SubjectWrite",
    "TopicWrite",
    "admin_curriculum",
    "ai_configuration",
    "classify_question",
    "create_chapter",
    "create_course",
    "create_mock",
    "create_question",
    "create_subject",
    "create_topic",
    "get_question",
    "list_flags",
    "list_law_notices",
    "list_mocks",
    "list_plans",
    "list_questions",
    "question_versions",
    "record_law_notice",
    "resolve_flag",
    "revise_question",
    "router",
    "save_plan_price",
    "storage_status",
    "unclassified_questions",
    "update_chapter",
    "update_course",
    "update_mock",
    "update_question",
    "update_subject",
    "update_topic",
]
