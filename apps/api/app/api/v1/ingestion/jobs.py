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

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.permissions import Permission, require_permission
from app.integrations.supabase_storage import (
    StorageError,
    SupabaseStorage,
)
from app.models.user import User
from app.repositories.ingestion import SqlIngestionSink

from ._shared import (
    JOB_STAGES,
    logger,
)

router = APIRouter(tags=["ingestion"])

"""Starting and inspecting ingestion jobs."""


@router.post(
    "/ingestion/jobs/{job_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirm the upload landed and queue extraction",
)
async def start_job(
    job_id: str,
    _caller: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
    settings: Settings = Depends(get_settings),
):
    """Confirm and enqueue.

    Returns 202 rather than 200: the work has been accepted, not completed. The
    caller polls the job status endpoint.
    """
    job = await SqlIngestionSink().load_job(job_id)
    if job is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Ingestion job not found",
            detail=f"No ingestion job with id {job_id}",
            type_slug="not-found",
        )

    try:
        storage = SupabaseStorage(settings)
        present = await storage.exists(job.bucket, job.storage_path)
    except StorageError as exc:
        logger.error("Storage check failed for job %s: %s", job_id, exc)
        return problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Storage unavailable",
            detail="Could not confirm the upload. Try again shortly.",
            type_slug="storage",
        )

    if not present:
        # Fail here rather than letting the worker discover it minutes later,
        # where it presents as a storage outage instead of a missing upload.
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Upload not found",
            detail="The file has not been uploaded to the expected path yet.",
            type_slug="conflict",
        )

    try:
        from app.workers.rq_worker import enqueue_ingestion

        enqueue_ingestion(job_id, job.storage_path, job.bucket)
    except Exception as exc:  # noqa: BLE001
        # Redis being unavailable must not silently drop the job. The row stays
        # in QUEUED and can be re-queued, which is recoverable; a 200 here would
        # leave an upload that is never processed and never reported.
        logger.error("Could not enqueue job %s: %s", job_id, exc)
        return problem(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Queue unavailable",
            detail="The job was saved but could not be queued. Retry shortly.",
            type_slug="queue",
        )

    return success(
        {"jobId": job_id, "stage": "QUEUED", "status": "accepted"},
        request_id=get_request_id(),
    )


@router.get("/ingestion/jobs", summary="List ingestion jobs")
async def list_jobs(
    stage: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _caller: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
):
    """The operations queue, newest first.

    Answers the two questions an editor has after uploading a paper: did the
    upload land, and is it still running. ``isTerminal`` is per job rather than
    per page so a polling client can stop on the job it cares about.
    """
    if stage is not None and stage.upper() not in JOB_STAGES:
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unknown stage",
            detail=f"stage must be one of {sorted(JOB_STAGES)}.",
            type_slug="validation",
            errors=[{"field": "stage", "message": "unknown ingestion stage"}],
        )

    from app.repositories.ingestion import SqlIngestionSink

    # The REQUEST's session, so the listing cannot be served from a different
    # database than the one this request is configured against.
    rows, total = await SqlIngestionSink().list_jobs(
        stage=stage.upper() if stage else None,
        limit=limit,
        offset=(page - 1) * limit,
        session=session,
    )

    return paginated(
        [
            {
                "jobId": row.job_id,
                "stage": row.stage,
                "bucket": row.bucket,
                "storagePath": row.storage_path,
                "draftsCreated": row.drafts_created,
                "pageCount": row.page_count,
                "extractionTier": row.extraction_tier,
                "needsManualReview": row.needs_manual_review,
                # The reason a job failed, surfaced in the list rather than only
                # in the detail view: an operator scanning forty jobs needs to see
                # WHICH ones failed, not open each one to find out.
                "errorReason": row.error_reason,
                "isTerminal": row.is_settled,
                "createdAt": row.created_at,
                "startedAt": row.started_at,
                "finishedAt": row.finished_at,
            }
            for row in rows
        ],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/ingestion/jobs/{job_id}", summary="Ingestion job status")
async def get_job(
    job_id: str,
    _caller: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
):
    """Job status, including the stage history.

    The history is exposed because it answers the question an editor actually
    has when something is slow: which stage is it sitting in, and how long has it
    been there.
    """
    from app.repositories.ingestion import SqlIngestionSink

    sink = SqlIngestionSink()
    try:
        key = uuid.UUID(job_id)
    except ValueError:
        return problem(
            status=status.HTTP_400_BAD_REQUEST,
            title="Invalid job id",
            detail="Job ids are UUIDs.",
            type_slug="validation",
        )

    job = await sink.load_job(str(key))
    if job is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Ingestion job not found",
            detail=f"No ingestion job with id {job_id}",
            type_slug="not-found",
        )

    return success(
        {
            "jobId": job.job_id,
            "stage": job.stage,
            "bucket": job.bucket,
            "storagePath": job.storage_path,
            "draftsCreated": job.drafts_created,
            # Poll-able: a client should back off once the job is terminal.
            "isTerminal": job.stage in {"AWAITING_QA", "PUBLISHED", "REJECTED", "FAILED"},
        },
        request_id=get_request_id(),
    )
