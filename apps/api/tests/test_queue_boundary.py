"""The queue boundary: what the API hands to RQ, and what RQ does with it.

WHY THIS FILE EXISTS

``enqueue_ingestion`` was written as ``retry={"max": MAX_RETRIES}``. RQ expects a
``Retry`` object (or an int) and fails inside ``Queue.create_job`` with
``AttributeError: 'dict' object has no attribute 'max'``.

Every caller wraps the call in ``try/except`` - correctly, because a queue outage must
not fail an upload whose bytes already landed - and turns the failure into
``enqueued: false`` with a log warning. The consequence was that the dict bug could not
be seen from outside:

  * ``POST /admin/content/uploads/{id}/start`` answered 201,
  * the re-process button answered 200,
  * and NO ingestion job ever ran. Nothing was extracted, no OCR requirement was ever
    flagged, and the pipeline looked idle rather than broken.

The live authorization sweep does not catch this either - those routes answer 200 with
the right role, which is what it checks. A test at the queue boundary is the only thing
that sees it, so this is that test.

``fakeredis`` is used rather than a mock of ``Queue``: the failure was INSIDE RQ's job
construction, so a mocked Queue would have accepted the dict happily and proved nothing.
"""

from __future__ import annotations

import pytest

fakeredis = pytest.importorskip("fakeredis", reason="fakeredis not installed")
rq = pytest.importorskip("rq", reason="rq not installed")

from rq import Queue  # noqa: E402

from app.workers import rq_worker  # noqa: E402


@pytest.fixture()
def queue(monkeypatch: pytest.MonkeyPatch) -> Queue:
    """A real Queue over a real Redis protocol implementation."""
    client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(rq_worker, "get_redis", lambda: client)
    monkeypatch.setattr(rq_worker, "get_queue", lambda name: Queue(name, connection=client))
    return Queue(rq_worker.QUEUE_INGESTION, connection=client)


def test_an_ingestion_job_is_actually_queued(queue: Queue) -> None:
    """The claim: after this call, a worker will find a job to run."""
    job = rq_worker.enqueue_ingestion("job-1", "library/paper.pdf", "source-pdfs")

    assert job.id
    assert queue.count == 1
    queued = queue.get_jobs()[0]
    assert queued.func_name == "app.workers.rq_worker.process_ingestion_job"
    # The three positional arguments the worker needs, in the order it unpacks them.
    assert queued.args == ("job-1", "library/paper.pdf", "source-pdfs")


def test_the_job_carries_retries_and_a_timeout(queue: Queue) -> None:
    """A retry count of "whatever RQ defaults to" is not the same as the intended one.

    ``retries_left`` is read from the job AFTER serialisation, which is where the dict
    blew up. Reading it here is what makes this test fail for the old spelling rather
    than merely asserting that some object was returned.
    """
    job = rq_worker.enqueue_ingestion("job-2", "library/paper.pdf", "source-pdfs")

    assert job.retries_left == rq_worker.MAX_RETRIES
    assert job.timeout == rq_worker.INGESTION_TIMEOUT_SECONDS


def test_the_dict_spelling_of_retry_is_rejected_by_rq(queue: Queue) -> None:
    """Keeps the reason for this file honest.

    If a future RQ version starts accepting a mapping, this test fails and the comment
    above can be corrected - rather than the codebase carrying a warning about a bug
    that no longer exists.
    """
    with pytest.raises(AttributeError):
        queue.enqueue(
            rq_worker.process_ingestion_job,
            "job-3",
            "library/paper.pdf",
            "source-pdfs",
            retry={"max": 2},
        )


def test_a_queue_outage_is_reported_and_not_raised_at_the_route() -> None:
    """The contract the callers rely on: a dead Redis is not a 500 on the upload.

    ``enqueue_ingestion`` is allowed to raise - the ROUTE catches it. What matters is
    that the route's exception type is broad enough to cover what RQ raises, and that
    the enqueue sites still look like that. This asserts the shape of the call sites in
    ``content.py`` so a later edit cannot quietly drop the guard.
    """
    import inspect
    import pathlib
    import re

    source = pathlib.Path(rq_worker.__file__).resolve()
    # PHASE 5. content.py is now a package. The two guarded `enqueue_ingestion`
    # call sites - upload processing and reprocess - live in `content/uploads.py`
    # and `content/documents.py`, so both are scanned. The assertions are
    # unchanged: every call site must still sit inside a `try:`.
    content_dir = source.parents[1] / "api" / "v1" / "content"
    sources = sorted(content_dir.glob("*.py"))
    text = "\n".join(p.read_text() for p in sources)

    # Every enqueue call site sits inside a try block whose except turns it into
    # `enqueued: false`.
    call_sites = [m.start() for m in re.finditer(r"enqueue_ingestion\(", text)]
    assert call_sites, "the enqueue call sites moved; update this test"
    for position in call_sites:
        window = text[max(0, position - 600) : position]
        assert "try:" in window, "an enqueue call is no longer guarded against an outage"

    assert "enqueued" in text
    # And the worker module documents its own entry point.
    assert inspect.getdoc(rq_worker.enqueue_ingestion)

    # A guard that only caught a narrow RQ exception would let Redis errors escape as
    # 500s; the sites catch broadly, which is deliberate here.
    assert "except Exception" in text


def test_the_worker_listens_on_the_ingestion_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` must register the ingestion queue, or jobs queue up forever.

    Cheap to assert and invisible when wrong: the API accepts work, RQ holds it, and no
    worker ever picks it up - which looks exactly like a pipeline that is merely slow.

    The check reads the CONSTANT the code uses rather than the string it evaluates to,
    so renaming the queue stays a one-line change here and there, while dropping the
    queue from the list fails.
    """
    import inspect

    source = inspect.getsource(rq_worker.main)
    assert "QUEUE_INGESTION" in source

    # ...and prove the constant is the queue the enqueue path uses, so the two cannot
    # drift apart.
    captured: list[str] = []

    class _Recorder:
        def __init__(self, name: str) -> None:
            self.name = name

    def record(name: str) -> object:
        captured.append(name)
        return _Recorder(name)

    class _Worker:
        def __init__(self, queues: object, connection: object = None) -> None:
            self.queues = queues

        def work(self, **kwargs: object) -> None:
            return None

    monkeypatch.setattr(rq_worker, "get_redis", lambda: object())
    monkeypatch.setattr(rq_worker, "get_queue", record)
    monkeypatch.setattr(rq_worker, "Worker", _Worker)

    rq_worker.main()

    assert rq_worker.QUEUE_INGESTION in captured
