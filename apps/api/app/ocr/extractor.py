"""PDF text extraction and OCR.

Blueprint v3 §11.2 specifies the component chain: PyMuPDF first for digitally
generated PDFs, pdfplumber as a fallback for tables and awkward layouts,
pdf2image/Poppler to rasterize, and Tesseract for scanned pages.

Three rules from §11.4 that shape this module:

  * low confidence routes the item to QA rather than publishing automatically
  * source page number and the original file reference are kept for traceability
  * the RAW extracted text is never overwritten; cleaned text is stored separately

The third rule is the one teams skip and later regret: without it there is no way
to tell whether a bad question was a bad extraction or a bad edit.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: Below this, an item is flagged for human QA (v3 §11.4).
OCR_CONFIDENCE_THRESHOLD = 0.85
#: Below this many characters on a page, assume the text layer is missing and
#: fall through to OCR. A page with a real text layer yields far more.
MIN_CHARS_PER_PAGE = 120


class ExtractionTier(int, Enum):
    """Processing tier, recorded on the job for cost and quality analysis."""

    PYMUPDF = 1  # digital text layer present
    PYPDFPLUMBER = 2  # layout/table fallback
    TESSERACT = 3  # rasterised + OCR


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    tier: ExtractionTier
    confidence: float
    char_count: int = 0


@dataclass
class ExtractionResult:
    pages: list[ExtractedPage] = field(default_factory=list)
    tier: ExtractionTier = ExtractionTier.PYMUPDF
    duration_seconds: float = 0.0
    needs_ocr_fallback: bool = False
    error: str | None = None

    @property
    def mean_confidence(self) -> float:
        if not self.pages:
            return 0.0
        return round(sum(p.confidence for p in self.pages) / len(self.pages), 4)


def looks_like_text_layer(text: str, *, text_spans: int | None = None) -> bool:
    """Decide whether a page genuinely carries an extractable text layer.

    THE OBVIOUS IMPLEMENTATION IS WRONG. The first version of this function
    required a minimum character count, and the tests caught it immediately: a
    legitimate two-mark objective question ("State the due date for filing
    GSTR-3B. (2 Marks)") is only ~44 characters, which is indistinguishable by
    length from the stray header stamp on a scanned page. A length threshold
    would therefore push real, short questions into expensive OCR - or, in the
    other direction, let scanned pages through.

    The reliable signal is STRUCTURAL, not statistical: does the page contain
    actual text spans, or is it just an image? PyMuPDF reports text blocks for
    every page that has a real text layer, so:
      * ``text_spans`` is passed in when the caller has already parsed the page
        structure (the extraction loop does), and is authoritative;
      * when it is not available, fall back to the character heuristic, which is
        adequate for the quick pre-checks and for OCR output.

    A page with images but no spans is scanned. A page with spans is digital,
    however short its text.
    """
    if text_spans is not None:
        # An image-only page has zero spans. A page with a text layer has at
        # least one, even for a one-line question.
        return text_spans > 0

    stripped = text.strip()
    if not stripped:
        return False
    alpha_ratio = sum(c.isalpha() for c in stripped) / len(stripped)
    return len(stripped) >= MIN_CHARS_PER_PAGE and alpha_ratio > 0.4


def count_text_spans(page: Any) -> int:
    """Count text spans on a PyMuPDF page.

    This is what distinguishes a digital page from a scanned one. Reading the
    structured dict is slightly more expensive than ``get_text("text")`` but is
    the only reliable way to tell an image-only page from a text page.
    """
    try:
        blocks = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001 - be permissive, fall back to length check
        return -1
    return sum(
        len(line.get("spans", []))
        for block in blocks
        if block.get("type") == 0
        for line in block.get("lines", [])
    )


def extract_pdf(
    pdf_bytes: bytes,
    *,
    prefer_plumber: bool = False,
) -> ExtractionResult:
    """Extract text from a PDF using the tiered strategy.

    Import failures for the optional parsers are handled explicitly: a missing
    optional dependency must degrade to a lower tier, not crash the worker.

    NOTE: this function is synchronous and CPU-bound, so the RQ worker calls it
    directly. It must NEVER be called from an async FastAPI request handler -
    parsing a full exam PDF would block the event loop. That is precisely why
    ingestion is a queued job (§5 "Non-Negotiable Separation").
    """
    started = time.perf_counter()
    result = ExtractionResult()

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - environment dependent
        result.error = f"PyMuPDF unavailable: {exc}"
        return result

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - malformed uploads are expected
        result.error = f"Cannot open PDF: {exc}"
        return result

    try:
        for index, page in enumerate(doc):
            text = page.get_text("text") or ""
            # Structural check first: span count is authoritative.
            spans = count_text_spans(page)
            if looks_like_text_layer(text, text_spans=spans):
                result.pages.append(
                    ExtractedPage(
                        page_number=index + 1,
                        text=text,
                        tier=ExtractionTier.PYMUPDF,
                        # PyMuPDF does not report confidence for a real text
                        # layer; treat a clean extraction as high confidence.
                        confidence=0.99,
                        char_count=len(text),
                    )
                )
            else:
                result.pages.append(
                    ExtractedPage(
                        page_number=index + 1,
                        text=text,
                        tier=ExtractionTier.TESSERACT,
                        confidence=0.0,
                        char_count=len(text),
                    )
                )
                result.needs_ocr_fallback = True

        # Optional layout/tables fallback for pages that parsed poorly.
        if prefer_plumber and result.needs_ocr_fallback:
            try:
                import pdfplumber

                with pdfplumber.open(__import__("io").BytesIO(pdf_bytes)) as plumber:
                    for page in result.pages:
                        if page.tier is not ExtractionTier.PYMUPDF:
                            src = plumber.pages[page.page_number - 1]
                            improved = src.extract_text() or ""
                            if len(improved) > len(page.text):
                                page.text = improved
                                page.tier = ExtractionTier.PYPDFPLUMBER
                                page.confidence = 0.9
                                page.char_count = len(improved)
            except ImportError:
                logger.info("pdfplumber not installed; skipping layout fallback")
            except Exception as exc:  # noqa: BLE001
                logger.warning("pdfplumber fallback failed: %s", exc)

        result.tier = _worst_tier(result.pages)
    finally:
        doc.close()

    result.duration_seconds = round(time.perf_counter() - started, 3)
    return result


def ocr_page(png_bytes: bytes, page_number: int) -> ExtractedPage:
    """OCR a rasterized page with Tesseract.

    Tesseract reports a mean confidence per word in ``image_to_data``. Using the
    real number matters: a hardcoded 0.9 would defeat the confidence threshold
    and let bad extractions through to students. (The superseded OCR contract
    carried a ``confidence`` field that nothing ever computed.)
    """
    try:
        import io

        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(f"OCR dependencies unavailable: {exc}") from exc

    image = Image.open(io.BytesIO(png_bytes))
    text = pytesseract.image_to_string(image) or ""

    confidence = 0.0
    try:
        data: dict[str, list[Any]] = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT
        )
        raw = [float(c) for c in data.get("conf", []) if str(c) not in ("-1", "")]
        if raw:
            confidence = round(sum(raw) / len(raw) / 100, 4)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not compute OCR confidence on page %s: %s", page_number, exc)

    return ExtractedPage(
        page_number=page_number,
        text=text,
        tier=ExtractionTier.TESSERACT,
        confidence=confidence,
        char_count=len(text),
    )


def _worst_tier(pages: list[ExtractedPage]) -> ExtractionTier:
    """Report the worst tier used, so the job records whether OCR was needed."""
    if not pages:
        return ExtractionTier.PYMUPDF
    return ExtractionTier(max(p.tier.value for p in pages))


# ----------------------------------------------------------------- segmenting

#: A question usually starts with a number, optionally with a mark allocation.
QUESTION_START_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})[.)]?\s+(?=[A-Z(])", re.MULTILINE)
MARKS_RE = re.compile(r"\((\d{1,3})\s*marks?\)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20\d{2})\b")
ATTEMPT_RE = re.compile(r"\b(May|Nov|November|September|Sep|Jan|January)\b", re.IGNORECASE)


def segment_questions(text: str, source_page: int) -> list[dict[str, Any]]:
    """Split page text into candidate questions.

    Deliberately conservative. This is a FIRST PASS whose only job is to give a
    human editor something reviewable; it is not expected to be correct, and
    every item it produces stays a DRAFT until QA approves it (v3 §11.1). An
    aggressive segmenter that merges two questions is more expensive to fix than
    one that splits too finely, because the merged case hides errors.
    """
    matches = list(QUESTION_START_RE.finditer(text))
    if not matches:
        return []

    candidates: list[dict[str, Any]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) < 25:  # too short to be a real question
            continue

        marks_match = MARKS_RE.search(body)
        year_match = YEAR_RE.search(body)
        attempt_match = ATTEMPT_RE.search(body)

        candidates.append(
            {
                "text": body,
                "source_page": source_page,
                "marks": int(marks_match.group(1)) if marks_match else None,
                "year": int(year_match.group(1)) if year_match else None,
                "attempt": attempt_match.group(1) if attempt_match else None,
                # Titles are never guessed: subject/chapter are set by QA.
                "subject": None,
                "chapter": None,
            }
        )
    return candidates


def infer_question_type(text: str) -> str:
    """Classify a candidate question.

    Only distinguishes plainly numerical content from prose. Anything ambiguous
    returns THEORETICAL so that a human reviews it, rather than guessing
    PRACTICAL and mis-tagging a theory question in a filter students rely on.
    """
    numeric_markers = ("compute", "calculate", "determine", "compute the", "work out")
    lowered = text.lower()
    if any(m in lowered for m in numeric_markers) and any(c.isdigit() for c in text):
        return "PRACTICAL"
    if re.search(r"\b(?:Rs\.?|INR|\u20b9)\s?[\d,]+", text):
        return "PRACTICAL"
    return "THEORETICAL"
