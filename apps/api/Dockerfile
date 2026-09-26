# CA Prep Platform API + RQ worker image.
#
# WHY A DOCKERFILE AT ALL, GIVEN RENDER'S PYTHON RUNTIME:
# The OCR pipeline (blueprint v3 §11.2) needs two SYSTEM BINARIES, not just
# Python packages:
#
#   tesseract-ocr   required by pytesseract  - OCR engine
#   poppler-utils   required by pdf2image    - PDF -> image rasteriser (pdftoppm)
#
# `pip install pytesseract` succeeds without them and the failure only appears
# at runtime, on the first scanned PDF, as a confusing "tesseract is not
# installed or it's not in your PATH" from deep inside a background job.
# A Docker image is the reliable way to pin these.
#
# NOTE: Render's native Python runtime does NOT include these binaries, so the
# worker service must be deployed with `runtime: docker` unless you add a build
# script that apt-installs them. This file is the safe path.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System dependencies.
#
#   tesseract-ocr          OCR engine
#   tesseract-ocr-eng      English language data (NOT pulled in by default -
#                          without it Tesseract errors on a bare install)
#   poppler-utils          pdftoppm/pdfinfo for pdf2image
#   libpq5                 psycopg runtime
#   libgl1 / libglib2.0-0  Pillow runtime for non-JPEG images
#
# --no-install-recommends keeps the image small; the language pack is installed
# explicitly because it IS a recommend, and dropping it silently breaks OCR.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr-eng \
        tesseract-ocr \
        poppler-utils \
        libpq5 \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a source change does not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Application source.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Run as a non-root user. A container that can write to its own application
# code is a container where an exploited dependency can rewrite the app.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Default is the API. The worker service overrides this with
#   python -m app.workers.rq_worker
# Mirrors render.yaml.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
# STARTUP VERIFICATION
# ---------------------------------------------------------------------------
# To confirm the OCR toolchain is present in a built image:
#
#   docker run --rm caprep-api tesseract --version
#   docker run --rm caprep-api pdftoppm -v
#
# Both must succeed. If tesseract is missing, ingestion jobs will fail at the
# scanned-PDF step and every other test will still pass.
