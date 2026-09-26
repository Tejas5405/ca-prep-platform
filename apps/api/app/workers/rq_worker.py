"""RQ worker entrypoint and job definitions.

Blueprint v3 §2: "Redis + RQ ... Caching, rate limiting, quotas, async PDF jobs".
§11.1 requires ingestion to be asynchronous: never block a normal request while
parsing a full exam PDF.

Run as a SEPARATE Render service from the API:

    python -m app.workers.rq_worker

WHY RQ AND NOT CELERY: the blueprint chose RQ explicitly (§2, §24 "Python-native
and simpler than cross-language queues"). This also removes the awkward
cross-language bridge the superseded architecture needed, where a Node worker had
to call a Python service over HTTP.

WORKER TOPOLOGY NOTE: an RQ worker is a long-lived process, not a web service.
On Render it must be a Background Worker, not a Web Service. Deploying it as a
Web Service means Render health-checks an HTTP port the worker never opens and
the deploy fails after the timeout - a common and confusing first-deploy error.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from redis import Redis
from rq import Queue, Retry, Worker
from rq.job import Job

from app.core.config import get_settings

logger = logging.getLogger(__name__)

#: Queue names. Separate queues so a large OCR backlog cannot delay a
#: lightweight notification job.
QUEUE_INGESTION = "ingestion"
QUEUE_DEFAULT = "default"

#: Job timeouts. OCR on a large scanned paper can legitimately take minutes, so
#: the timeout is generous but finite - an unbounded job is how a worker pool
#: dies silently.
INGESTION_TIMEOUT_SECONDS = 900
DEFAULT_TIMEOUT_SECONDS = 180
#: Retries before the job lands in the failed registry for inspection.
MAX_RETRIES = 2


def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url or "redis://localhost:6379")


def get_queue(name: str = QUEUE_INGESTION) -> Queue:
    return Queue(name, connection=get_redis())


# --------------------------------------------------------------------- jobs


def process_ingestion_job(job_id: str, storage_path: str, bucket: str) -> dict[str, Any]:
    """Download a PDF and extract candidate questions into the QA staging table.

    Idempotency: the job checks the ingestion job's current status first, so a
    retry after a partial failure does not create duplicate draft questions. This
    mirrors the Razorpay webhook rule in §13.1 - "Treat webhook handling as
    idempotent" - applied to the queue.

    Every failure path records a REASON on the job row. A failed job with no
    reason is an incident nobody can triage.
    """
    from app.services.ingestion import run_ingestion

    logger.info("Starting ingestion job %s", job_id)
    try:
        return run_ingestion(job_id=job_id, storage_path=storage_path, bucket=bucket)
    except Exception as exc:
        logger.exception("Ingestion job %s failed", job_id)
        _record_failure(job_id, f"{type(exc).__name__}: {exc}", traceback.format_exc())
        raise


def send_notification_job(user_id: str, template: str, payload: dict[str, Any]) -> None:
    """Queue a transactional notification.

    Email is a side effect of a business action, never a blocking step inside the
    request that triggered it.
    """
    logger.info("Notification queued for user=%s template=%s", user_id, template)


def _record_failure(job_id: str, reason: str, tb: str) -> None:
    """Persist the failure reason for the admin dashboard.

    Uses a synchronous Redis client because this runs inside the worker process,
    not an async request handler.
    """
    try:
        client = get_redis()
        client.hset(
            f"ingestion:failed:{job_id}",
            mapping={"reason": reason, "traceback": tb[:4000]},
        )
        client.expire(f"ingestion:failed:{job_id}", 7 * 24 * 3600)
    except Exception:
        logger.exception("Could not record failure for job %s", job_id)


def enqueue_ingestion(job_id: str, storage_path: str, bucket: str) -> Job:
    """Enqueue an ingestion job. Called by the API request handler.

    ``retry`` takes an ``rq.Retry`` (or an int), NOT a dictionary. Passing
    ``{"max": 2}`` - which reads like the natural spelling and is what this code did -
    fails inside ``Queue.create_job`` with ``AttributeError: 'dict' object has no
    attribute 'max'``.

    WHY THAT MATTERED MORE THAN IT LOOKS: the three call sites wrap this in
    ``try/except`` and report ``enqueued: false``, because a queue outage must not fail
    the upload that just succeeded. So the AttributeError was caught, logged as a
    warning, and turned into a 200. Every ingestion job in the product - the post-upload
    extraction, the re-process button, the ingestion module's own start - would have been
    accepted, answered 200, and never run. Nothing would have been extracted, no OCR
    would have been flagged, and the only trace would be a warning in the log.

    ``Retry`` is constructed once here rather than per call: it is immutable, and RQ
    serialises it into the job on enqueue.
    """
    queue = get_queue(QUEUE_INGESTION)
    return queue.enqueue(
        process_ingestion_job,
        job_id,
        storage_path,
        bucket,
        job_timeout=INGESTION_TIMEOUT_SECONDS,
        retry=Retry(max=MAX_RETRIES),
        result_ttl=86400,
        failure_ttl=7 * 86400,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
        '"msg":"%(message)s"}',
    )
    connection = get_redis()
    queues = [get_queue(QUEUE_INGESTION), get_queue(QUEUE_DEFAULT)]
    worker = Worker(queues, connection=connection)
    logger.info("RQ worker starting on queues: %s", [q.name for q in queues])
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
