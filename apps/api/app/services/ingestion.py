"""PDF ingestion pipeline - blueprint v3 §11.

    download -> extract -> quality gate -> OCR fallback (if needed) ->
    segment -> detect metadata -> persist raw + drafts -> AWAITING_QA

TWO DESIGN DECISIONS THAT MAKE THIS TESTABLE

1. **Dependencies are injected**, not imported. The storage client, the OCR
   rasteriser and the persistence sink all arrive as constructor arguments. That
   is what makes the pipeline testable without a PostgreSQL instance, without
   Supabase, and - most importantly - without the Tesseract binary. The quality
   gate's decision logic is the part that matters, and it is exercised on every
   run rather than skipped whenever a system binary is missing.

2. **The pipeline is async, with a sync wrapper for RQ.** RQ jobs are plain sync
   callables, but the Supabase client is async (httpx). ``run_ingestion`` is the
   sync entry point the worker calls; ``ingest`` is the real implementation.
   Splitting them means tests drive the async path directly with no event-loop
   tricks.

NOTHING HERE PUBLISHES. The pipeline's terminal state is ``AWAITING_QA``. Blueprint
§11.1 requires a human QA step between extraction and publication, and an
automated path that can write ``status = 'PUBLISHED'`` would need a human
verifier id it cannot legitimately have (see the
``ck_questions_published_requires_verifier`` constraint).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.models.enums import IngestionStage
from app.ocr.extractor import (
    OCR_CONFIDENCE_THRESHOLD,
    ExtractionResult,
    ExtractionTier,
    extract_pdf,
    infer_question_type,
    segment_questions,
)

logger = logging.getLogger(__name__)


# =============================================================== interfaces


@dataclass
class JobState:
    """The slice of a job the pipeline needs to decide what to do."""

    job_id: str
    bucket: str
    storage_path: str
    stage: str
    drafts_created: int = 0


@dataclass(frozen=True)
class JobSummary:
    """A row in the operations queue.

    A separate shape from ``JobState`` rather than the same class with extra
    fields: the pipeline needs a bucket and a path to do its work, and an
    operations table needs timestamps and a failure reason. Sharing one type would
    mean every pipeline test constructs fields it does not care about.
    """

    job_id: str
    bucket: str
    storage_path: str
    stage: str
    drafts_created: int
    page_count: int | None
    extraction_tier: int | None
    needs_manual_review: bool
    error_reason: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def is_settled(self) -> bool:
        """True when the job will not advance on its own.

        Read the note on ``SETTLED_STAGES`` before using this: "terminal" is a word
        this codebase uses in two senses, and a failed job is one of the cases where
        they disagree.
        """
        return self.stage in SETTLED_STAGES


class IngestionSink(Protocol):
    """Persistence boundary.

    Kept narrow on purpose. The pipeline decides WHAT happens; the sink decides
    how it is stored. A Protocol rather than an ABC so the SQLAlchemy
    implementation does not have to inherit from anything, and so tests can pass
    a plain object.
    """

    async def load_job(self, job_id: str) -> JobState | None: ...

    async def list_jobs(
        self,
        *,
        stage: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobSummary], int]: ...

    async def record_stage(
        self, job_id: str, stage: IngestionStage, *, detail: str | None = None
    ) -> None: ...

    async def save_raw_pages(self, job_id: str, pages: list[Any]) -> dict[int, str]: ...

    async def save_drafts(self, job_id: str, drafts: list[dict[str, Any]]) -> int: ...

    async def complete(
        self,
        job_id: str,
        *,
        tier: int,
        page_count: int,
        mean_confidence: float,
        drafts_created: int,
        needs_manual_review: bool,
    ) -> None: ...

    async def fail(self, job_id: str, reason: str) -> None: ...


class StorageReader(Protocol):
    """Only the read side of storage is needed here."""

    async def download(self, bucket: str, path: str) -> bytes: ...


# ================================================================ results


@dataclass
class IngestionOutcome:
    """What the pipeline did, in a form both the worker and a test can assert on."""

    job_id: str
    stage: IngestionStage
    tier: int = 0
    page_count: int = 0
    mean_confidence: float = 0.0
    drafts_created: int = 0
    needs_manual_review: bool = False
    skipped: bool = False
    reason: str | None = None
    stages_visited: list[str] = field(default_factory=list)


class IngestionError(RuntimeError):
    """Raised for a failure the pipeline itself cannot recover from."""


# ================================================================ pipeline

#: Stages that mean the job already finished its useful work. Re-running is a
#: no-op rather than an error: RQ retries and duplicate webhook-style
#: notifications are both normal, and treating them as failures would fill the
#: failed registry with noise that hides real failures.
TERMINAL_STAGES = {
    IngestionStage.AWAITING_QA.value,
    IngestionStage.PUBLISHED.value,
    IngestionStage.REJECTED.value,
}

#: Stages after which nothing further will happen UNLESS A HUMAN ACTS.
#:
#: Not the same set as ``TERMINAL_STAGES``, and the difference is FAILED. A failed
#: job is settled - a client should stop polling it and an operations table should
#: stop showing it as in flight - but it is deliberately NOT in ``TERMINAL_STAGES``,
#: because the guard there decides whether a re-run is a no-op, and re-running a
#: failed job is the one thing an operator most wants to do. Two questions, two
#: constants: conflating them either makes failures unretryable or makes a retry
#: silently do nothing.
SETTLED_STAGES = TERMINAL_STAGES | {IngestionStage.FAILED.value}


class IngestionPipeline:
    """Runs the extraction pipeline for one job."""

    def __init__(
        self,
        *,
        storage: StorageReader,
        sink: IngestionSink,
        ocr_rasteriser: Any = None,
        ocr_engine: Any = None,
        confidence_threshold: float = OCR_CONFIDENCE_THRESHOLD,
        prefer_plumber: bool = False,
        expected_object: tuple[str, str] | None = None,
    ) -> None:
        self.storage = storage
        self.sink = sink
        #: (bucket, storage_path) the caller believes this job is for. Checked
        #: against the job row so a row mutated after enqueue cannot cause one
        #: PDF's contents to be attributed to another job.
        self.expected_object = expected_object
        # Injected so the OCR path can be tested without Poppler or Tesseract
        # installed. In production these are the pdf2image and pytesseract
        # wrappers from app.ocr.extractor.
        self.ocr_rasteriser = ocr_rasteriser or _default_rasteriser
        self.ocr_engine = ocr_engine or _default_ocr_engine
        self.confidence_threshold = confidence_threshold
        self.prefer_plumber = prefer_plumber

    async def run(self, job_id: str) -> IngestionOutcome:
        job = await self.sink.load_job(job_id)
        if job is None:
            # Not an exception: the API creates the job row and enqueues in one
            # request, so a missing row means the job was deleted, not that
            # something is broken. Raising here would retry forever.
            logger.warning("Ingestion job %s not found; nothing to do", job_id)
            return IngestionOutcome(
                job_id=job_id,
                stage=IngestionStage.FAILED,
                reason="job_not_found",
            )

        # ---- idempotency guard -------------------------------------------
        if job.stage in TERMINAL_STAGES:
            logger.info(
                "Ingestion job %s already at %s; skipping duplicate run",
                job_id,
                job.stage,
            )
            return IngestionOutcome(
                job_id=job_id,
                stage=IngestionStage(job.stage),
                drafts_created=job.drafts_created,
                skipped=True,
                reason="already_processed",
            )

        outcome = IngestionOutcome(job_id=job_id, stage=IngestionStage.QUEUED)

        try:
            # Integrity check before any work: does the job row still describe
            # the object the caller asked for?
            if self.expected_object is not None:
                observed = (job.bucket, job.storage_path)
                if observed != self.expected_object:
                    raise IngestionError(
                        "job_object_mismatch: the job row points at "
                        f"{job.bucket}/{job.storage_path} but the worker was "
                        f"enqueued for {self.expected_object[0]}/{self.expected_object[1]}"
                    )

            await self._stage(job_id, IngestionStage.DOWNLOADING, outcome)
            pdf_bytes = await self.storage.download(job.bucket, job.storage_path)

            if not pdf_bytes:
                # An empty object is a failed upload, not an empty document.
                # Treated as a failure rather than "0 pages extracted", because
                # the latter produces a job that looks successful with nothing in it.
                raise IngestionError("Downloaded object is empty")

            await self._stage(job_id, IngestionStage.EXTRACTING, outcome)
            result = self._extract(pdf_bytes, outcome)

            if result.error:
                raise IngestionError(result.error)

            # ---- quality gate --------------------------------------------
            await self._stage(job_id, IngestionStage.QUALITY_GATE, outcome)
            if self._needs_ocr(result):
                await self._stage(
                    job_id,
                    IngestionStage.OCR_FALLBACK,
                    outcome,
                    detail=(
                        "ocr_pages="
                        f"{sum(1 for p in result.pages if p.tier is ExtractionTier.TESSERACT)}"
                    ),
                )
                result = await self._run_ocr_fallback(pdf_bytes, result, outcome)

            # ---- persist the raw text BEFORE anything else ---------------
            # §11.4: raw text is never overwritten. It is written first so that
            # if segmentation or draft creation fails afterwards, the expensive
            # extraction work is not lost and the job can be retried cheaply.
            page_ids = await self.sink.save_raw_pages(job_id, result.pages)

            await self._stage(job_id, IngestionStage.SEGMENTING, outcome)
            drafts = self._build_drafts(result, page_ids)

            await self._stage(job_id, IngestionStage.DETECTING_METADATA, outcome)
            drafts = [self._detect_metadata(d) for d in drafts]

            await self._stage(job_id, IngestionStage.AWAITING_QA, outcome)
            created = await self.sink.save_drafts(job_id, drafts)
            outcome.drafts_created = created

            outcome.mean_confidence = result.mean_confidence
            outcome.tier = result.tier.value
            outcome.page_count = len(result.pages)
            # A job that produced nothing usable, or whose confidence is below
            # the gate, is flagged so QA does not treat every draft as equally
            # trustworthy. Silently producing zero drafts is how an ingestion
            # backlog gets mistaken for an empty PDF.
            outcome.needs_manual_review = (
                result.mean_confidence < self.confidence_threshold or created == 0
            )

            await self.sink.complete(
                job_id,
                tier=outcome.tier,
                page_count=outcome.page_count,
                mean_confidence=outcome.mean_confidence,
                drafts_created=created,
                needs_manual_review=outcome.needs_manual_review,
            )
            outcome.stage = IngestionStage.AWAITING_QA
            return outcome

        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.exception("Ingestion job %s failed", job_id)
            # The reason is persisted, not only logged. A failed job with no
            # explanation is an incident nobody can triage, and logs expire
            # while the job row does not.
            await self.sink.fail(job_id, reason)
            outcome.stage = IngestionStage.FAILED
            outcome.reason = reason
            return outcome

    # ------------------------------------------------------------ internals

    async def _stage(
        self,
        job_id: str,
        stage: IngestionStage,
        outcome: IngestionOutcome,
        *,
        detail: str | None = None,
    ) -> None:
        await self.sink.record_stage(job_id, stage, detail=detail)
        outcome.stages_visited.append(stage.value)

    def _extract(self, pdf_bytes: bytes, outcome: IngestionOutcome) -> ExtractionResult:
        # Synchronous and CPU-bound. That is correct here: this runs in an RQ
        # worker process, which is exactly why ingestion is a queued job rather
        # than work inside an async request handler.
        return extract_pdf(pdf_bytes, prefer_plumber=self.prefer_plumber)

    def _needs_ocr(self, result: ExtractionResult) -> bool:
        """Quality gate.

        Two reasons to fall through to OCR, and they are different:
          * ``needs_ocr_fallback`` - pages with no text layer at all, i.e. a
            scan. These will never yield text without OCR.
          * low mean confidence - text WAS extracted but the extractor is not
            confident in it. Re-OCRing a page that already parsed is not always
            worth the cost, so this is only triggered when the average is below
            the threshold AND there is something to redo.
        """
        if not result.pages:
            return False
        if result.needs_ocr_fallback:
            return True
        return result.mean_confidence < self.confidence_threshold

    async def _run_ocr_fallback(
        self, pdf_bytes: bytes, result: ExtractionResult, outcome: IngestionOutcome
    ) -> ExtractionResult:
        """Replace low-quality pages with OCR output.

        Failures here are contained: if rasterising or OCR fails, the original
        extraction is kept and the job is flagged for manual review. Discarding
        successfully-parsed pages because the OCR toolchain is missing would
        turn a degraded result into no result at all.
        """
        try:
            images = await asyncio.to_thread(self.ocr_rasteriser, pdf_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OCR rasterisation unavailable for job %s: %s. Keeping original extraction.",
                outcome.job_id,
                exc,
            )
            return result

        images_by_page = {int(img[0]): img[1] for img in images}
        improved_pages = []

        for page in result.pages:
            png = images_by_page.get(page.page_number)
            if png is None:
                improved_pages.append(page)
                continue

            try:
                ocr_page_result = await asyncio.to_thread(self.ocr_engine, png, page.page_number)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OCR failed on page %s of job %s: %s. Keeping extracted text.",
                    page.page_number,
                    outcome.job_id,
                    exc,
                )
                improved_pages.append(page)
                continue

            # Only accept the OCR result when it is genuinely better. OCR of a
            # page that already had a clean text layer is usually WORSE, and
            # taking it unconditionally would degrade good input.
            if self._is_improvement(page, ocr_page_result):
                improved_pages.append(ocr_page_result)
            else:
                improved_pages.append(page)

        result.pages = improved_pages
        result.tier = ExtractionTier(
            max((p.tier.value for p in result.pages), default=ExtractionTier.PYMUPDF.value)
        )
        result.needs_ocr_fallback = any(
            p.tier is ExtractionTier.TESSERACT and p.confidence < self.confidence_threshold
            for p in result.pages
        )
        return result

    @staticmethod
    def _is_improvement(original: Any, candidate: Any) -> bool:
        if candidate.confidence <= 0:
            return False
        if original.char_count == 0:
            return True
        return candidate.confidence > original.confidence

    def _build_drafts(
        self, result: ExtractionResult, page_ids: dict[int, str]
    ) -> list[dict[str, Any]]:
        drafts: list[dict[str, Any]] = []
        for page in result.pages:
            if not page.text.strip():
                # A blank page is normal in an exam paper. Producing a draft with
                # empty text would put a blank row in front of an editor.
                continue
            for candidate in segment_questions(page.text, source_page=page.page_number):
                drafts.append(
                    {
                        **candidate,
                        "raw_extraction_id": page_ids.get(page.page_number),
                        "source_confidence": page.confidence,
                    }
                )
        return drafts

    def _detect_metadata(self, draft: dict[str, Any]) -> dict[str, Any]:
        """Attach advisory metadata.

        Everything here is a SUGGESTION recorded in ``detected_*`` fields. An
        editor confirms or corrects it during QA. Nothing detected here is ever
        written onto the resulting question as fact - mis-tagging a question's
        type or marks is a silent error in a filter students use to decide what
        to study.
        """
        text = draft["text"]
        return {
            **draft,
            "detected_year": draft.get("year"),
            "detected_attempt": draft.get("attempt"),
            "detected_marks": draft.get("marks"),
            "detected_question_type": infer_question_type(text),
            "detection_confidence": draft.get("source_confidence"),
        }


# ------------------------------------------------------- default adapters


def _default_rasteriser(pdf_bytes: bytes) -> list[tuple[int, bytes]]:
    """Rasterise every PDF page to PNG using pdf2image/Poppler.

    Imported lazily and raised on failure rather than at module import, because
    the pipeline must still work for digitally-generated PDFs on a machine
    without Poppler. The caller treats an exception here as "OCR unavailable"
    and keeps the original extraction.
    """
    import io

    from pdf2image import convert_from_bytes

    images = convert_from_bytes(pdf_bytes, dpi=200, fmt="png")
    out: list[tuple[int, bytes]] = []
    for index, image in enumerate(images, start=1):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        out.append((index, buffer.getvalue()))
    return out


def _default_ocr_engine(png_bytes: bytes, page_number: int) -> Any:
    from app.ocr.extractor import ocr_page

    return ocr_page(png_bytes, page_number)


# ------------------------------------------------------------ sync wrapper


async def ingest(
    *,
    job_id: str,
    storage: StorageReader,
    sink: IngestionSink,
    **kwargs: Any,
) -> IngestionOutcome:
    """Async entry point. Used by tests and by any async caller."""
    return await IngestionPipeline(storage=storage, sink=sink, **kwargs).run(job_id)


def run_ingestion(
    job_id: str,
    storage_path: str,
    bucket: str,
    *,
    storage: StorageReader | None = None,
    sink: IngestionSink | None = None,
) -> dict[str, Any]:
    """RQ entry point. Synchronous, because RQ jobs are plain callables.

    ``asyncio.run`` is only valid when no event loop is already running, which
    holds in an RQ worker process. Calling this from inside an async handler
    would raise ``RuntimeError: asyncio.run() cannot be called from a running
    event loop`` - so the check below turns that into an explicit, explainable
    error instead of a confusing one, and points the caller at ``ingest``.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no running loop - correct context for asyncio.run
    else:
        raise RuntimeError(
            "run_ingestion is the synchronous RQ entry point and cannot be called "
            "from a running event loop. Await app.services.ingestion.ingest instead."
        )

    if storage is None or sink is None:
        # Built here rather than at import time so the module stays importable in
        # tests and in environments with no Supabase configuration.
        from app.integrations.supabase_storage import SupabaseStorage
        from app.repositories.ingestion import SqlIngestionSink

        storage = storage or SupabaseStorage(_settings())
        sink = sink or SqlIngestionSink()

    pipeline = IngestionPipeline(storage=storage, sink=sink, expected_object=(bucket, storage_path))
    outcome = asyncio.run(pipeline.run(job_id))

    return {
        "jobId": outcome.job_id,
        "stage": outcome.stage.value,
        "tier": outcome.tier,
        "pageCount": outcome.page_count,
        "meanConfidence": outcome.mean_confidence,
        "draftsCreated": outcome.drafts_created,
        "needsManualReview": outcome.needs_manual_review,
        "skipped": outcome.skipped,
        "reason": outcome.reason,
    }


def _settings() -> Any:
    from app.core.config import get_settings

    return get_settings()


def content_hash(text: str) -> str:
    """Stable hash of extracted text, used to detect mutation after the fact."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)
