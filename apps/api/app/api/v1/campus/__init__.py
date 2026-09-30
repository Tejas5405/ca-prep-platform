"""Campus: groups, forum, reference data, support, experiments, calendar, the
daily challenge, referrals and the publish review queue.

Split from the former single-file app/api/v1/campus.py. `router` carries the same
paths, methods, tags, names and status codes as before, in the same order. Every
permission check and payload is where it was."""

from __future__ import annotations

from fastapi import APIRouter

from . import calendar as _calendar
from . import challenge as _challenge
from . import experiments as _experiments
from . import forum as _forum
from . import groups as _groups
from . import reference as _reference
from . import review as _review
from . import support as _support
from .calendar import (
    CalendarIn,
    ExamModeIn,
    PomodoroIn,
    ProctorIn,
    create_calendar_event,
    list_calendar,
    list_pomodoro,
    record_pomodoro,
    record_proctor_event,
    start_exam_mode,
)
from .challenge import (
    ChallengeIn,
    RedeemIn,
    _ensure_code,
    _todays_question,
    answer_challenge,
    daily_challenge,
    redeem_referral,
    referral_status,
)
from .experiments import (
    ExperimentActiveIn,
    ExperimentIn,
    ListingIn,
    create_experiment,
    create_listing,
    experiment_variant,
    list_marketplace,
    set_experiment,
)
from .forum import (
    PostIn,
    ThreadIn,
    create_thread,
    list_threads,
    read_thread,
    reply_thread,
)
from .groups import (
    GroupIn,
    JoinIn,
    MentorAssignIn,
    MentorshipIn,
    admin_mentorship,
    assign_mentor,
    create_group,
    group_members,
    join_group,
    list_groups,
    my_mentorship,
    request_mentor,
)
from .reference import (
    FormulaIn,
    GlossaryIn,
    create_formula,
    create_glossary,
    list_formulas,
    list_glossary,
)
from .review import ReviewStateIn, reverify, review_queue, set_review_state
from .support import (
    TicketIn,
    TicketStatusIn,
    admin_tickets,
    create_ticket,
    my_tickets,
    update_ticket,
)

router = APIRouter()
router.routes.extend(_groups.router.routes)
router.routes.extend(_forum.router.routes)
router.routes.extend(_reference.router.routes)
router.routes.extend(_support.router.routes)
router.routes.extend(_experiments.router.routes)
router.routes.extend(_calendar.router.routes)
router.routes.extend(_challenge.router.routes)
router.routes.extend(_review.router.routes)

__all__ = [
    "CalendarIn",
    "ChallengeIn",
    "ExamModeIn",
    "ExperimentActiveIn",
    "ExperimentIn",
    "FormulaIn",
    "GlossaryIn",
    "GroupIn",
    "JoinIn",
    "ListingIn",
    "MentorAssignIn",
    "MentorshipIn",
    "PomodoroIn",
    "PostIn",
    "ProctorIn",
    "RedeemIn",
    "ReviewStateIn",
    "ThreadIn",
    "TicketIn",
    "TicketStatusIn",
    "_ensure_code",
    "_todays_question",
    "admin_mentorship",
    "admin_tickets",
    "answer_challenge",
    "assign_mentor",
    "create_calendar_event",
    "create_experiment",
    "create_formula",
    "create_glossary",
    "create_group",
    "create_listing",
    "create_thread",
    "create_ticket",
    "daily_challenge",
    "experiment_variant",
    "group_members",
    "join_group",
    "list_calendar",
    "list_formulas",
    "list_glossary",
    "list_groups",
    "list_marketplace",
    "list_pomodoro",
    "list_threads",
    "my_mentorship",
    "my_tickets",
    "read_thread",
    "record_pomodoro",
    "record_proctor_event",
    "redeem_referral",
    "referral_status",
    "reply_thread",
    "request_mentor",
    "reverify",
    "review_queue",
    "router",
    "set_experiment",
    "set_review_state",
    "start_exam_mode",
    "update_ticket",
]
