"""Mock papers and mock attempts.

Split from the former single-file app/api/v1/mocks.py. `router` carries the same
paths, methods, tags, names and status codes as before, in the same order. Bodies
moved verbatim. `_correct_index` is re-exported because a test imports it."""

from __future__ import annotations

from fastapi import APIRouter

from . import attempts as _attempts
from . import publishing as _publishing
from . import reports as _reports
from ._shared import logger
from .attempts import (
    _correct_index,
    list_attempts,
    list_mocks,
    read_attempt,
    start_attempt,
    submit_attempt,
)
from .publishing import publish_mock
from .reports import attempt_report

router = APIRouter()
router.routes.extend(_attempts.router.routes)
router.routes.extend(_reports.router.routes)
router.routes.extend(_publishing.router.routes)

__all__ = [
    "_correct_index",
    "attempt_report",
    "list_attempts",
    "list_mocks",
    "logger",
    "publish_mock",
    "read_attempt",
    "router",
    "start_attempt",
    "submit_attempt",
]
