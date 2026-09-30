# Content Operations

## Copyright posture

ICAI material is copyrighted. The platform's position is **link-first**:

- the product links to and references official material, it does not republish
  ICAI text or past papers;
- questions in the bank are **original** work written for this platform;
- the study-aid disclaimer is stored per row and surfaced in the UI, so a
  learner is never left thinking this is an official extract.

`questions.disclaimer_text` and `ck_questions_historical_requires_disclaimer`
enforce the third point at the database: a historical question cannot exist
without its disclaimer.

## Quality control

`ck_questions_published_requires_verifier` refuses a `PUBLISHED` question with
no `verified_by`. A published question is one every student is marked against;
releasing an unreviewed one produces a report that calls students wrong, and
the student cannot tell the difference between a hard question and a defect.

The verifier is always the **caller's** row, resolved server-side from the
authenticated principal. A request cannot name who signed a question off.

`question_versions` records every revision, so an attempt marked against
version 3 still resolves correctly after version 4 lands. Without it, a disputed
marking cannot be reconstructed.

Additional constraints doing real work: `ck_questions_objective_requires_answer`
(no MCQ without a recorded key), `ck_questions_marks_positive`, and
`ck_questions_year` bounding `year` to a plausible range.

## OCR ingestion

```
upload → ingestion_jobs → raw_extractions → ingestion_drafts
                                              ↓
                                        human verification
                                              ↓
                                          questions (PUBLISHED)
```

PDFs are stored in Supabase Storage; `content_documents` tracks extraction.

**Text-layer extraction first.** If a PDF already has a text layer, parsing it is
faster, exact, and free. OCR is the fallback for scanned pages only.

Scanned pages need two system binaries that `pip` does not provide:

- `tesseract-ocr` + `tesseract-ocr-eng` (English data is not pulled in by
  default, and a bare install errors at runtime)
- `poppler-utils` for `pdftoppm`

This is why the worker runs as a Docker service. The failure without them is
easy to miss: the pipeline keeps its text-layer extraction, so a scanned paper
produces zero drafts and **no error on the job**. The worker looks healthy while
a whole class of uploads goes nowhere.

A document with no usable text is marked `OCR_REQUIRED` rather than processed to
nothing, so the gap is visible instead of silent.

## Draft review

Drafts from extraction are proposals, not content. They queue for review, and
only a reviewed draft becomes a `PUBLISHED` question. `ingestion_drafts.detected_attempt`
records what the OCR read off a past paper; it is a provenance hint, not an
entitlement — it is deliberately **not** backfilled into
`applicable_attempts`.
