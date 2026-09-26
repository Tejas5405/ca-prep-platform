"""PDF extraction and segmentation.

These tests build real PDFs in memory with PyMuPDF, so they exercise the actual
tier-1 extraction path rather than a mock. The Tesseract path needs the system
binary, which is absent in some environments (and deliberately present in the
worker image), so those tests skip rather than fail - a skip is honest about what
was not verified, whereas a mocked pass would claim coverage that does not exist.
"""

from __future__ import annotations

import shutil

import pytest

from app.ocr.extractor import (
    MIN_CHARS_PER_PAGE,
    OCR_CONFIDENCE_THRESHOLD,
    ExtractionTier,
    extract_pdf,
    infer_question_type,
    looks_like_text_layer,
    segment_questions,
)

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
requires_tesseract = pytest.mark.skipif(
    not TESSERACT_AVAILABLE, reason="tesseract binary not installed"
)


def make_pdf(pages: list[str], *, with_text: bool = True) -> bytes:
    """Build a one-or-more page PDF in memory.

    ``with_text=False`` produces a page with no text layer, which is how a
    scanned paper looks to the extractor.
    """
    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        if with_text:
            page.insert_text((72, 100), body, fontsize=11)
    pdf = doc.tobytes()
    doc.close()
    return pdf


LONG_PARA = (
    "Explain the provisions relating to deductions from gross total income "
    "under the relevant section, with reference to the current assessment year. "
    "Support your answer with brief reasoning and any relevant case law."
)


class TestTextLayerDetection:
    def test_rejects_short_stray_text(self):
        """A scanned page often yields a few characters from a header stamp.

        Treating those as a text layer is how a scanned paper silently produces
        almost-empty questions.
        """
        assert looks_like_text_layer("Page 1") is False
        assert looks_like_text_layer("") is False
        assert looks_like_text_layer("x" * 10) is False

    def test_rejects_garbage_with_no_letters(self):
        # Garbled CID output is long but has almost no alphabetic content.
        garbage = "".join(chr(0x2000 + i % 100) for i in range(MIN_CHARS_PER_PAGE * 2))
        assert looks_like_text_layer(garbage) is False

    def test_accepts_real_prose(self):
        assert looks_like_text_layer(LONG_PARA * 3) is True


class TestPyMuPDFExtraction:
    def test_extracts_text_from_a_digital_pdf(self):
        pdf = make_pdf([LONG_PARA])
        result = extract_pdf(pdf)
        assert result.error is None
        assert len(result.pages) == 1
        assert result.pages[0].tier is ExtractionTier.PYMUPDF
        assert result.needs_ocr_fallback is False
        assert "deductions" in result.pages[0].text.lower()

    def test_marks_a_scanned_page_for_ocr_fallback(self):
        pdf = make_pdf([LONG_PARA], with_text=False)
        result = extract_pdf(pdf)
        assert result.needs_ocr_fallback is True
        assert result.pages[0].tier is ExtractionTier.TESSERACT
        assert result.pages[0].confidence == 0.0

    def test_records_the_worst_tier_across_pages(self):
        # A paper that is digital except for one scanned annexure must be
        # reported as needing OCR, not as fully digital.
        doc = fitz.open()
        p1 = doc.new_page()
        p1.insert_text((72, 100), LONG_PARA, fontsize=11)
        doc.new_page()  # blank -> no text layer
        mixed = doc.tobytes()
        doc.close()

        result = extract_pdf(mixed)
        assert result.needs_ocr_fallback is True
        assert result.tier is ExtractionTier.TESSERACT

    def test_page_numbers_are_one_based(self):
        pdf = make_pdf([LONG_PARA, LONG_PARA, LONG_PARA])
        result = extract_pdf(pdf)
        assert [p.page_number for p in result.pages] == [1, 2, 3]

    def test_char_count_is_recorded(self):
        pdf = make_pdf([LONG_PARA])
        result = extract_pdf(pdf)
        assert result.pages[0].char_count == len(result.pages[0].text)

    def test_duration_is_measured(self):
        result = extract_pdf(make_pdf([LONG_PARA]))
        assert result.duration_seconds >= 0

    def test_handles_a_malformed_upload_without_raising(self):
        """A corrupt upload must fail the job, not crash the worker."""
        result = extract_pdf(b"this is definitely not a pdf")
        assert result.error is not None
        assert result.pages == []

    def test_handles_an_empty_byte_string(self):
        result = extract_pdf(b"")
        assert result.error is not None

    def test_mean_confidence_of_a_digital_pdf_is_high(self):
        result = extract_pdf(make_pdf([LONG_PARA, LONG_PARA]))
        assert result.mean_confidence > OCR_CONFIDENCE_THRESHOLD

    def test_mean_confidence_is_zero_for_a_scanned_pdf_without_ocr(self):
        result = extract_pdf(make_pdf([LONG_PARA], with_text=False))
        assert result.mean_confidence == 0.0


class TestSegmentation:
    PAGE = """
    Q.1 Explain the provisions relating to deductions from gross total income
    under the relevant section of the Income-tax Act, 1961 with reference to
    the current assessment year. (10 Marks)

    Q.2 Compute the total income of Mr. Sharma for the assessment year
    2026-27 from the particulars given below. (8 Marks)

    3. Discuss the applicability of the deemed dividend provisions in the
    context of a loan advanced to a shareholder. (6 Marks)
    """

    def test_finds_multiple_questions(self):
        found = segment_questions(self.PAGE, source_page=1)
        assert len(found) >= 3

    def test_attaches_the_source_page(self):
        found = segment_questions(self.PAGE, source_page=7)
        assert all(q["source_page"] == 7 for q in found)

    def test_does_not_guess_subject_or_chapter(self):
        """Metadata is a QA decision, never inferred at extraction time."""
        found = segment_questions(self.PAGE, source_page=1)
        assert all(q["subject"] is None for q in found)
        assert all(q["chapter"] is None for q in found)

    def test_extracts_marks_when_present(self):
        found = segment_questions(self.PAGE, source_page=1)
        marks = [q["marks"] for q in found if q["marks"] is not None]
        assert 10 in marks

    def test_extracts_the_year(self):
        found = segment_questions(self.PAGE, source_page=1)
        years = [q["year"] for q in found if q["year"]]
        assert 2026 in years

    def test_returns_nothing_for_text_with_no_questions(self):
        assert segment_questions("This page is a syllabus contents listing.", 1) == []

    def test_discards_fragments_too_short_to_be_questions(self):
        found = segment_questions("1. A\n2. B\n3. C\n", source_page=1)
        assert found == []

    def test_is_deterministic(self):
        assert segment_questions(self.PAGE, 1) == segment_questions(self.PAGE, 1)


class TestQuestionTypeInference:
    def test_detects_a_computational_question(self):
        text = "Compute the total income of Mr. Sharma for the assessment year 2026-27."
        assert infer_question_type(text) == "PRACTICAL"

    def test_detects_a_rupee_amount_question(self):
        assert infer_question_type("Calculate GST payable on Rs. 4,50,000.") == "PRACTICAL"

    def test_defaults_ambiguous_text_to_theory_for_human_review(self):
        """Guessing PRACTICAL here would mis-tag a filter students rely on."""
        text = "Discuss the provisions relating to deductions under the Act."
        assert infer_question_type(text) == "THEORETICAL"

    def test_numeric_words_alone_are_not_enough(self):
        # "Discuss" with a stray number is still a theory question.
        assert infer_question_type("Discuss section 2 and section 3 together.") == "THEORETICAL"


@requires_tesseract
class TestTesseractPath:
    """Only runs where the tesseract binary exists (the worker image)."""

    def test_ocr_of_a_rendered_page_yields_text(self):
        from app.ocr.extractor import ocr_page

        pdf = make_pdf([LONG_PARA])
        doc = fitz.open(stream=pdf, filetype="pdf")
        pix = doc[0].get_pixmap(dpi=200)
        png = pix.tobytes("png")
        doc.close()

        page = ocr_page(png, page_number=1)
        assert page.tier is ExtractionTier.TESSERACT
        assert page.confidence > 0, "confidence must be computed, not defaulted"
        assert page.confidence <= 1.0

    def test_tesseract_is_available_when_this_class_runs(self):
        assert TESSERACT_AVAILABLE
