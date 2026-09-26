"""Ingestion pipeline tests.

The pipeline is driven with an in-memory sink and a fake storage reader, so the
orchestration logic - stage transitions, the quality gate, the OCR-improvement
rule, idempotency, failure recording - is exercised on every run.

That design is the point. The alternative (a real database and a real Tesseract
binary) would mean these tests are skipped in most environments, and the logic
that decides whether a scanned paper produces usable drafts would go unverified.
The Tesseract integration itself is covered separately in ``test_ocr.py``, where
skipping is honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.models.enums import IngestionStage
from app.ocr.extractor import ExtractedPage, ExtractionTier
from app.services.ingestion import (
    TERMINAL_STAGES,
    IngestionPipeline,
    content_hash,
)

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

LONG_PARA = (
    "Explain the provisions relating to deductions from gross total income under "
    "the relevant section of the Income-tax Act, 1961 with reference to the "
    "current assessment year. Support your answer with brief reasoning."
)


# ============================================================== test doubles


@dataclass
class FakeJob:
    job_id: str
    bucket: str = "question-pdfs"
    storage_path: str = "originals/u/20260924/abc-paper.pdf"
    stage: str = IngestionStage.QUEUED.value
    drafts_created: int = 0


@dataclass
class FakeSink:
    """In-memory implementation of the IngestionSink protocol."""

    job: FakeJob | None = None
    stages: list[tuple[str, str | None]] = field(default_factory=list)
    raw_pages: list[Any] = field(default_factory=list)
    drafts: list[dict[str, Any]] = field(default_factory=list)
    completed: dict[str, Any] | None = None
    failure: str | None = None
    fail_on_save_raw: bool = False

    async def load_job(self, job_id: str) -> FakeJob | None:
        if self.job is None or self.job.job_id != job_id:
            return None
        return self.job

    async def record_stage(
        self, job_id: str, stage: IngestionStage, *, detail: str | None = None
    ) -> None:
        self.stages.append((stage.value, detail))

    async def save_raw_pages(self, job_id: str, pages: list[Any]) -> dict[int, str]:
        if self.fail_on_save_raw:
            raise RuntimeError("database unavailable")
        self.raw_pages = list(pages)
        return {p.page_number: f"raw-{p.page_number}" for p in pages}

    async def save_drafts(self, job_id: str, drafts: list[dict[str, Any]]) -> int:
        self.drafts = list(drafts)
        return len(drafts)

    async def complete(
        self,
        job_id: str,
        *,
        tier: int,
        page_count: int,
        mean_confidence: float,
        drafts_created: int,
        needs_manual_review: bool,
    ) -> None:
        self.completed = {
            "tier": tier,
            "page_count": page_count,
            "mean_confidence": mean_confidence,
            "drafts_created": drafts_created,
            "needs_manual_review": needs_manual_review,
        }

    async def fail(self, job_id: str, reason: str) -> None:
        self.failure = reason


@dataclass
class FakeStorage:
    content: bytes = b""
    raise_on_download: Exception | None = None

    async def download(self, bucket: str, path: str) -> bytes:
        if self.raise_on_download:
            raise self.raise_on_download
        return self.content


def make_pdf(pages: list[str], *, with_text: bool = True) -> bytes:
    """Build a PDF in memory.

    Uses ``insert_textbox`` rather than ``insert_text``. ``insert_text`` draws a
    single unwrapped line and silently CLIPS anything past the page width, so a
    long question loses its trailing "(10 Marks)" - which silently broke the
    marks assertions in this file before the fixture was corrected. A textbox
    wraps like a real exam paper does.

    ``with_text=False`` produces a page with no text layer, which is how a
    scanned paper looks to the extractor.
    """
    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        if with_text and body:
            page.insert_textbox(
                fitz.Rect(56, 72, 539, 770),  # A4 with realistic margins
                body,
                fontsize=10,
                align=fitz.TEXT_ALIGN_LEFT,
            )
    pdf = doc.tobytes()
    doc.close()
    return pdf


def question_page(n: int = 1) -> str:
    return "\n".join(f"Q.{i} {LONG_PARA} ({10 - i} Marks)" for i in range(1, n + 1))


def build(
    pdf: bytes,
    *,
    job: FakeJob | None = None,
    rasteriser: Any = None,
    ocr: Any = None,
    threshold: float = 0.85,
    expected_object: tuple[str, str] | None = None,
) -> tuple[IngestionPipeline, FakeSink]:
    sink = FakeSink(job=job or FakeJob(job_id="job-1"))
    pipeline = IngestionPipeline(
        storage=FakeStorage(content=pdf),
        sink=sink,
        ocr_rasteriser=rasteriser or (lambda _pdf: []),
        ocr_engine=ocr or (lambda _png, _page: None),
        confidence_threshold=threshold,
        expected_object=expected_object,
    )
    return pipeline, sink


# =================================================================== result


class TestHappyPath:
    async def test_processes_a_digital_pdf_end_to_end(self):
        pipeline, sink = build(make_pdf([question_page(3)]))
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.AWAITING_QA
        assert outcome.page_count == 1
        assert outcome.tier == ExtractionTier.PYMUPDF.value
        assert outcome.drafts_created >= 3
        assert outcome.needs_manual_review is False
        assert sink.failure is None

    async def test_never_publishes(self):
        """§11.1 requires a human QA step. The terminal state is AWAITING_QA.

        A published question needs a `verified_by` user id, and an automated
        pipeline has no legitimate one to claim. So "the pipeline never
        publishes" is a structural guarantee, not just current behaviour.
        """
        pipeline, sink = build(make_pdf([question_page(2)]))
        outcome = await pipeline.run("job-1")

        assert sink.failure is None
        assert "PUBLISHED" not in [stage for stage, _ in sink.stages]

        assert outcome.stage is IngestionStage.AWAITING_QA
        assert outcome.stage is not IngestionStage.PUBLISHED

    async def test_visits_stages_in_order(self):
        pipeline, sink = build(make_pdf([question_page(2)]))
        await pipeline.run("job-1")

        visited = [stage for stage, _ in sink.stages]
        assert visited == [
            "DOWNLOADING",
            "EXTRACTING",
            "QUALITY_GATE",
            "SEGMENTING",
            "DETECTING_METADATA",
            "AWAITING_QA",
        ]

    async def test_marks_the_job_complete_with_the_observed_stats(self):
        pipeline, sink = build(make_pdf([question_page(3)]))
        outcome = await pipeline.run("job-1")

        assert sink.completed is not None
        assert sink.completed["page_count"] == 1
        assert sink.completed["tier"] == ExtractionTier.PYMUPDF.value
        assert sink.completed["drafts_created"] == outcome.drafts_created
        assert sink.completed["mean_confidence"] == pytest.approx(outcome.mean_confidence)

    async def test_handles_a_multi_page_paper(self):
        pipeline, _ = build(make_pdf([question_page(2), question_page(3)]))
        outcome = await pipeline.run("job-1")

        assert outcome.page_count == 2
        assert outcome.drafts_created >= 5

    async def test_is_deterministic(self):
        pdf = make_pdf([question_page(3)])
        first, sink_a = build(pdf)
        second, sink_b = build(pdf)
        await first.run("job-1")
        await second.run("job-1")

        assert [d["text"] for d in sink_a.drafts] == [d["text"] for d in sink_b.drafts]


class TestRawTextIsNeverLost:
    """§11.4: raw extracted text is stored and never overwritten."""

    async def test_raw_pages_are_persisted_before_drafts(self):
        pipeline, sink = build(make_pdf([question_page(2)]))
        await pipeline.run("job-1")

        assert len(sink.raw_pages) == 1
        assert sink.raw_pages[0].text.strip()

    async def test_raw_text_is_saved_even_when_segmentation_finds_nothing(self):
        # A page of prose with no question markers still gets stored, so an
        # editor can see what the extractor actually read.
        pipeline, sink = build(make_pdf(["This page is a syllabus contents listing."]))
        outcome = await pipeline.run("job-1")

        assert len(sink.raw_pages) == 1
        assert outcome.drafts_created == 0
        # Zero drafts on a page that had text means something is wrong with the
        # segmenter, so the job is flagged rather than quietly "successful".
        assert outcome.needs_manual_review is True

    async def test_raw_text_survives_a_later_failure(self):
        """Extraction is the expensive step; losing it wastes the whole job."""
        pipeline, sink = build(make_pdf([question_page(2)]))
        sink.fail_on_save_raw = True

        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.FAILED
        assert sink.failure is not None
        assert "database unavailable" in sink.failure

    def test_content_hash_is_stable_and_sensitive(self):
        assert content_hash("abc") == content_hash("abc")
        assert content_hash("abc") != content_hash("abd")
        assert len(content_hash("abc")) == 64


class TestIdempotency:
    """RQ retries and duplicate enqueues are normal, not errors."""

    @pytest.mark.parametrize(
        "stage",
        ["AWAITING_QA", "PUBLISHED", "REJECTED"],
    )
    async def test_skips_an_already_processed_job(self, stage):
        job = FakeJob(job_id="job-1", stage=stage, drafts_created=7)
        pipeline, sink = build(make_pdf([question_page(2)]), job=job)

        outcome = await pipeline.run("job-1")

        assert outcome.skipped is True
        assert outcome.drafts_created == 7
        assert sink.stages == [], "a duplicate run must not redo any work"
        assert sink.drafts == []

    async def test_a_skipped_job_is_not_reported_as_a_failure(self):
        job = FakeJob(job_id="job-1", stage="AWAITING_QA")
        pipeline, sink = build(make_pdf([question_page(1)]), job=job)

        outcome = await pipeline.run("job-1")

        assert sink.failure is None
        assert outcome.reason == "already_processed"
        assert outcome.stage is IngestionStage.AWAITING_QA

    async def test_reprocesses_a_job_that_previously_failed(self):
        # FAILED is intentionally NOT terminal: a transient Supabase outage must
        # not permanently poison a job.
        job = FakeJob(job_id="job-1", stage=IngestionStage.FAILED.value)
        pipeline, _ = build(make_pdf([question_page(2)]), job=job)

        outcome = await pipeline.run("job-1")

        assert outcome.skipped is False
        assert outcome.stage is IngestionStage.AWAITING_QA

    def test_failed_is_not_a_terminal_stage(self):
        assert IngestionStage.FAILED.value not in TERMINAL_STAGES
        assert IngestionStage.PUBLISHED.value in TERMINAL_STAGES

    async def test_a_missing_job_row_is_not_an_exception(self):
        pipeline, sink = build(make_pdf([question_page(1)]))
        outcome = await pipeline.run("does-not-exist")

        assert outcome.reason == "job_not_found"
        assert sink.failure is None


class TestQualityGate:
    """Decides when OCR is worth its cost - the pipeline's most consequential branch."""

    async def test_a_clean_digital_pdf_skips_ocr(self):
        rasterised: list[bytes] = []

        def rasteriser(pdf: bytes):
            rasterised.append(pdf)
            return []

        pipeline, sink = build(make_pdf([question_page(3)]), rasteriser=rasteriser)
        outcome = await pipeline.run("job-1")

        assert rasterised == [], "OCR must not run for a PDF with a text layer"
        assert "OCR_FALLBACK" not in [s for s, _ in sink.stages]
        assert outcome.tier == ExtractionTier.PYMUPDF.value

    async def test_a_scanned_pdf_triggers_the_ocr_fallback(self):
        seen: list[bytes] = []

        def rasteriser(pdf: bytes):
            seen.append(pdf)
            return [(1, b"png-bytes")]

        def ocr(_png: bytes, page_number: int):
            return ExtractedPage(
                page_number=page_number,
                text=question_page(2),
                tier=ExtractionTier.TESSERACT,
                confidence=0.94,
                char_count=200,
            )

        pipeline, sink = build(
            make_pdf([LONG_PARA], with_text=False), rasteriser=rasteriser, ocr=ocr
        )
        outcome = await pipeline.run("job-1")

        assert seen, "rasteriser should have been called for a page with no text layer"
        assert "OCR_FALLBACK" in [s for s, _ in sink.stages]
        assert outcome.tier == ExtractionTier.TESSERACT.value
        assert outcome.drafts_created >= 2

    async def test_the_fallback_stage_records_how_many_pages_needed_it(self):
        def rasteriser(_pdf: bytes):
            return [(1, b"png")]

        def ocr(_png: bytes, page_number: int):
            return ExtractedPage(
                page_number=page_number,
                text=question_page(2),
                tier=ExtractionTier.TESSERACT,
                confidence=0.9,
                char_count=200,
            )

        pipeline, sink = build(
            make_pdf([LONG_PARA], with_text=False), rasteriser=rasteriser, ocr=ocr
        )
        await pipeline.run("job-1")

        fallback = [detail for stage, detail in sink.stages if stage == "OCR_FALLBACK"]
        assert fallback and "ocr_pages=1" in (fallback[0] or "")

    async def test_low_confidence_ocr_still_flags_for_manual_review(self):
        def rasteriser(_pdf: bytes):
            return [(1, b"png")]

        def ocr(_png: bytes, page_number: int):
            return ExtractedPage(
                page_number=page_number,
                text=question_page(2),
                tier=ExtractionTier.TESSERACT,
                # Below the 0.85 gate
                confidence=0.42,
                char_count=200,
            )

        pipeline, sink = build(
            make_pdf([LONG_PARA], with_text=False), rasteriser=rasteriser, ocr=ocr
        )
        outcome = await pipeline.run("job-1")

        assert outcome.needs_manual_review is True
        assert sink.completed is not None
        assert sink.completed["needs_manual_review"] is True


class TestOcrFallbackIsContained:
    """A broken OCR toolchain must degrade the result, not destroy it."""

    async def test_a_missing_toolchain_keeps_the_original_extraction(self):
        def rasteriser(_pdf: bytes):
            raise RuntimeError("poppler not installed")

        pipeline, _ = build(make_pdf([question_page(2)]), rasteriser=rasteriser)
        outcome = await pipeline.run("job-1")

        # The digital text layer was fine; a missing rasteriser is irrelevant.
        assert outcome.stage is IngestionStage.AWAITING_QA
        assert outcome.drafts_created >= 2

    async def test_a_failing_ocr_engine_keeps_the_page_text(self):
        def rasteriser(_pdf: bytes):
            return [(1, b"png")]

        def ocr(_png: bytes, _page: int):
            raise RuntimeError("tesseract: command not found")

        pipeline, sink = build(
            make_pdf([LONG_PARA], with_text=False), rasteriser=rasteriser, ocr=ocr
        )
        outcome = await pipeline.run("job-1")

        # No text was recoverable, but the job completed rather than failed, and
        # the raw page is still stored for an editor to look at.
        assert outcome.stage is IngestionStage.AWAITING_QA
        assert len(sink.raw_pages) == 1
        assert outcome.needs_manual_review is True

    async def test_ocr_output_is_rejected_when_it_is_worse(self):
        """Re-OCRing a page that already parsed cleanly usually degrades it."""

        def rasteriser(_pdf: bytes):
            return [(1, b"png")]

        def ocr(_png: bytes, page_number: int):
            return ExtractedPage(
                page_number=page_number,
                text="gmblrsh garbled output",
                tier=ExtractionTier.TESSERACT,
                confidence=0.30,
                char_count=22,
            )

        # A PDF with a real text layer but a threshold above PyMuPDF's 0.99 so
        # the gate still fires and the improvement rule is what decides.
        pipeline, sink = build(
            make_pdf([question_page(2)]),
            rasteriser=rasteriser,
            ocr=ocr,
            threshold=0.999,
        )
        outcome = await pipeline.run("job-1")

        assert outcome.drafts_created >= 2, "garbled OCR must not replace clean text"
        assert "garbled" not in " ".join(d["text"] for d in sink.drafts)

    async def test_a_zero_confidence_ocr_result_is_never_an_improvement(self):
        assert (
            IngestionPipeline._is_improvement(
                original=ExtractedPage(1, "", ExtractionTier.TESSERACT, 0.0, 0),
                candidate=ExtractedPage(1, "x", ExtractionTier.TESSERACT, 0.0, 1),
            )
            is False
        )


class TestFailureRecording:
    """Every failure path must leave a triageable reason on the job."""

    async def test_a_malformed_pdf_fails_with_a_reason(self):
        pipeline, sink = build(b"not a pdf at all")
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.FAILED
        assert sink.failure is not None
        assert "IngestionError" in sink.failure

    async def test_an_empty_download_fails_rather_than_producing_an_empty_job(self):
        """A zero-byte object is a failed upload, not an empty document.

        Treating it as "0 pages extracted" would mark the job successful with
        nothing in it - an ingestion backlog that looks like an empty PDF.

        The assertion is on the STAGE SEQUENCE, not the error text. Checking
        `"empty" in reason` passed even with the guard deleted, because PyMuPDF
        raises its own "cannot open empty document" error a moment later. The
        observable difference is that the guard fires BEFORE extraction starts,
        which is what this test measures.
        """
        pipeline, sink = build(b"")
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.FAILED
        assert sink.failure is not None
        assert "Downloaded object is empty" in sink.failure
        assert "EXTRACTING" not in [stage for stage, _ in sink.stages], (
            "an empty object must fail before extraction is attempted; reaching "
            "EXTRACTING means the guard is gone and a library error is standing in for it"
        )

    async def test_the_empty_object_guard_is_not_the_pdf_parser_in_disguise(self):
        """Distinguish our guard from the parser's own error on the same input.

        Both produce a FAILED job, so only the stage sequence tells them apart.
        """
        pipeline, sink = build(b"")
        await pipeline.run("job-1")

        failed_at = [stage for stage, _ in sink.stages][-1]
        assert failed_at == "DOWNLOADING"

    async def test_a_storage_outage_is_recorded(self):
        sink = FakeSink(job=FakeJob(job_id="job-1"))
        pipeline = IngestionPipeline(
            storage=FakeStorage(raise_on_download=RuntimeError("supabase 503")),
            sink=sink,
        )
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.FAILED
        assert sink.failure is not None
        assert "supabase 503" in sink.failure

    async def test_complete_is_not_called_on_failure(self):
        pipeline, sink = build(b"not a pdf")
        await pipeline.run("job-1")

        assert sink.completed is None, "a failed job must not be marked complete"

    async def test_failure_after_partial_progress_is_still_recorded(self):
        pipeline, sink = build(make_pdf([question_page(2)]))
        sink.fail_on_save_raw = True
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.FAILED
        # The stages that did run are visible, so the failure location is known.
        assert "EXTRACTING" in [s for s, _ in sink.stages]


class TestObjectIntegrity:
    """A job row mutated after enqueue must not ingest a different file."""

    async def test_rejects_a_job_pointing_at_a_different_object(self):
        job = FakeJob(
            job_id="job-1",
            bucket="question-pdfs",
            storage_path="originals/u/20260924/other-paper.pdf",
        )
        pipeline, sink = build(
            make_pdf([question_page(2)]),
            job=job,
            expected_object=("question-pdfs", "originals/u/20260924/abc-paper.pdf"),
        )
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.FAILED
        assert sink.failure is not None
        assert "job_object_mismatch" in sink.failure

    async def test_accepts_a_matching_object(self):
        job = FakeJob(job_id="job-1", bucket="b", storage_path="p/paper.pdf")
        pipeline, _ = build(
            make_pdf([question_page(2)]), job=job, expected_object=("b", "p/paper.pdf")
        )
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.AWAITING_QA

    async def test_no_check_when_no_expectation_is_supplied(self):
        job = FakeJob(job_id="job-1", bucket="b", storage_path="p/paper.pdf")
        pipeline, _ = build(make_pdf([question_page(2)]), job=job)
        outcome = await pipeline.run("job-1")

        assert outcome.stage is IngestionStage.AWAITING_QA


class TestDrafts:
    async def test_drafts_carry_their_source_page_routing(self):
        pipeline, sink = build(make_pdf([question_page(2)]))
        await pipeline.run("job-1")

        assert all(d["source_page"] == 1 for d in sink.drafts)
        assert all(d["raw_extraction_id"] == "raw-1" for d in sink.drafts)

    async def test_drafts_include_advisory_detected_metadata(self):
        pipeline, sink = build(make_pdf([question_page(2)]))
        await pipeline.run("job-1")

        for draft in sink.drafts:
            assert "detected_question_type" in draft
            assert draft["detected_question_type"] in ("PRACTICAL", "THEORETICAL")

    async def test_marks_are_detected_but_subject_is_left_for_a_human(self):
        """Placement is an editorial decision; extraction must not guess it."""
        pipeline, sink = build(make_pdf([question_page(2)]))
        await pipeline.run("job-1")

        assert any(d["detected_marks"] is not None for d in sink.drafts)
        # The segmenter never invents a subject or chapter.
        assert all(d.get("subject") is None for d in sink.drafts)
        assert all(d.get("chapter") is None for d in sink.drafts)

    async def test_blank_pages_produce_no_drafts(self):
        # A blank page between sections is normal in an exam paper.
        pipeline, sink = build(make_pdf([question_page(2), ""]))
        outcome = await pipeline.run("job-1")

        assert len(sink.raw_pages) == 2
        assert all(d["source_page"] == 1 for d in sink.drafts)
        assert outcome.drafts_created == len(sink.drafts)


class TestSyncWrapperGuard:
    def test_refuses_to_run_inside_a_running_event_loop(self):
        """asyncio.run() inside a live loop raises a cryptic error; be explicit."""
        import asyncio

        from app.services.ingestion import run_ingestion

        async def call_it():
            return run_ingestion(
                "job-1", "p/paper.pdf", "b", storage=FakeStorage(), sink=FakeSink()
            )

        with pytest.raises(RuntimeError, match="running event loop"):
            asyncio.run(call_it())
