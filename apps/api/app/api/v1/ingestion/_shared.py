"""Ingestion and QA endpoints - blueprint v3 §11.1.

The flow, and why it is split into two requests:

    1. POST /ingestion/uploads
         validate metadata -> mint a SHORT-LIVED signed upload URL
         -> create the job row in QUEUED (nothing queued yet)

    2. client PUTs the bytes directly to Supabase

    3. POST /ingestion/jobs/{id}/start
         confirm the object exists -> enqueue the RQ job

The split exists because the upload happens BETWEEN the two calls. The API never
sees the file, which is the point: a 50 MB scanned paper would otherwise hit
Render's request-size limit and occupy a worker for the whole transfer.

WHY /start VERIFIES THE OBJECT

Without a check, a client could call /start without ever uploading and the worker
would fail minutes later on a download error that looks like a storage outage.
Confirming existence first turns a confusing async failure into an immediate 409.

WHY ROLE CHECKS ARE ON EVERY ROUTE

The backend holds the Supabase service-role key and Supabase Storage RLS cannot
evaluate Supabase tokens, so these checks are the only authorization
boundary. Uploading source papers is editorial work, not student work.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, status
from pydantic import Field

from app.core.envelope import problem
from app.models.enums import IngestionStage
from app.models.user import User
from app.schemas.base import StrictRequest

router = APIRouter(tags=["ingestion"])

"""Helpers, constants and OptionIn shared by more than one sub-module.
Unchanged; only relocated."""


logger = logging.getLogger(__name__)


class OptionIn(StrictRequest):
    label: str = Field(min_length=1, max_length=4)
    text: str = Field(min_length=1, max_length=2000)
    is_correct: bool = False


#: Stages accepted by the queue filter. Validated against the enum rather than
#: passed through, so a typo returns 422 with the allowed list instead of a page
#: that is empty for a reason the caller cannot see.
JOB_STAGES = {stage.value for stage in IngestionStage}


def _actor_id(caller: User) -> uuid.UUID:
    """The acting user's database id, from the row the permission check already loaded.

    This used to take the token principal and look the row up a second time, with a
    ``None`` return for "could not resolve" - which is how an approval could be recorded
    with no author. The permission dependency has already loaded the user row (that is
    what makes a role change take effect immediately), so the id is in hand and
    ``questions.created_by`` is never left null on a path that required a real person.
    """
    return caller.id


def _not_found(exc: Exception):
    return problem(
        status=status.HTTP_404_NOT_FOUND,
        title="Draft not found",
        detail=str(exc),
        type_slug="not-found",
    )


def _conflict(exc: Exception):
    """409 rather than 200: the caller's intent was not carried out.

    Returning success would tell an editor their approval landed when a
    concurrent reviewer had already decided the draft differently.
    """
    return problem(
        status=status.HTTP_409_CONFLICT,
        title="Draft already reviewed",
        detail=str(exc),
        type_slug="conflict",
    )
