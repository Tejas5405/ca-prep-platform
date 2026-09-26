"""Supabase Storage integration.

Blueprint v3 §12: buckets are PRIVATE. The browser uses short-lived signed URLs,
PostgreSQL stores ``storage_path``, ``mime_type``, ``size_bytes`` and
``checksum``, and students never receive direct public access to source PDFs.

=============================================================================
WHY THIS MODULE IS BACKEND-BROKERED
=============================================================================
Identity, database and storage are all Supabase now, so Storage's row-level
security policies COULD evaluate ``auth.uid()`` from a user's access token. That
is deliberately not how file access works here. Three reasons:

  * One boundary, not two. Authorization that lives partly in Postgres policies
    and partly in Python has two places to get wrong and two places to audit.
  * The browser must never hold the secret key, which bypasses RLS entirely.
  * A student's own token would let Storage list and read anything their RLS
    policies allow, including objects the API would refuse for other reasons
    (an unpublished paper, a soft-deleted upload).

So the flow stays brokered:

    1. Client authenticates with Supabase, calls the API with its access token.
    2. API verifies the token, applies the role check, validates upload
       metadata (``validate_upload``).
    3. API asks Supabase for a SHORT-LIVED SIGNED UPLOAD URL (secret key).
    4. Client uploads the file DIRECTLY to Supabase with that URL.
    5. Client reports the storage path back; API enqueues the ingestion job.

Step 4 is what makes this better than proxying. Two constraints on Render make
proxying a bad idea for large files:

  * Render enforces a request body size limit well below a scanned exam paper's
    size, so a 40-page PDF upload would fail at the proxy with an opaque error.
  * The request would occupy a worker for the whole transfer, so uploads compete
    with normal API traffic.

A signed URL keeps the authorization decision server-side while the bytes never
touch the API.

SECURITY NOTE ON SIGNED UPLOAD URLS: Minting one for an arbitrary path would let
a client overwrite another user's object. Paths are therefore SERVER-GENERATED
and namespaced (see ``build_object_path``) rather than accepted from the client.
The client supplies a filename for display and extension checking only.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Bucket NAMES from blueprint v3 §12.
#:
#: These are bucket identifiers, NOT paths. An earlier version stored
#: "question-pdfs/originals" here, which conflates the two layers: Supabase
#: rejects a bucket name containing "/" with 400 InvalidBucketName, verified
#: against the live project. The originals/processed split is a PATH PREFIX
#: within one bucket - see PREFIXES below - because the storage URL is
#: /object/{bucket}/{path}, so a slash in the bucket silently shifts a path
#: segment into the bucket position.
#:
#: All three are PRIVATE. Nothing here is readable without a signed URL.
BUCKETS = {
    "questions": "question-pdfs",
    "media": "question-media",
    "user_uploads": "user-uploads",
}

#: Path prefixes inside a bucket. These are the blueprint's folders.
PREFIXES = {
    "originals": "originals",
    "processed": "processed",
    "doubts": "doubts",
}

#: Upload guard rails (v3 §15 "File uploads").
ALLOWED_PDF_MIME = {"application/pdf"}
ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
#: Extensions permitted to survive into a stored object path.
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

#: Signed URL lifetimes. Upload URLs are deliberately SHORT - a minted URL is a
#: bearer capability, so the window in which a leaked one is useful is kept
#: small. Preview URLs are longer because a student may keep a page open.
UPLOAD_URL_TTL_SECONDS = 300  # 5 minutes
DOWNLOAD_URL_TTL_SECONDS = 3600  # 1 hour
MAX_SIGNED_TTL_SECONDS = 3600


class StorageError(RuntimeError):
    """Every way talking to storage can fail, as ONE exception type.

    The endpoints catch this and answer 503 with a message. They do not catch
    ``JSONDecodeError``, ``httpx.ConnectError`` or ``KeyError`` - so anything that
    escapes as one of those reaches the client as a 500 "unexpected error", which is a
    lie about where the fault is and gives the operator nothing to act on.

    That is not hypothetical: a storage host behind a gateway (or pointed at the wrong
    URL, which is the first thing to go wrong in a new environment) answers with an HTML
    error page. ``resp.json()`` on that page raises ``JSONDecodeError``, and the PDF
    preview, the download and the re-process endpoint returned 500 for every role. The
    live authorization sweep is what surfaced it, because it counts 5xx separately from
    403s.
    """

    pass


def _snippet(response: httpx.Response, limit: int = 200) -> str:
    """A short, single-line, HTML-stripped piece of a failed response body.

    HTML goes through this because the interesting part of a gateway's error page is
    one line of text inside half a kilobyte of markup, and the whole thing is echoed
    into an API error message that an operator reads in a browser tab.
    """
    body = response.text[: limit * 3]
    if "<" in body:
        without_tags = re.sub(r"<[^>]*>", " ", body)
        body = re.sub(r"\s+", " ", without_tags).strip()
    return body[:limit]


def _json_body(response: httpx.Response, *, action: str) -> dict[str, Any]:
    """Parse a storage response, or raise ``StorageError`` saying what came back.

    A storage request that SUCCEEDED at the HTTP level can still carry a body this code
    cannot use. Both cases have to end as a StorageError: the caller's only sensible
    reaction to either is "storage is not answering as expected".
    """
    try:
        payload = response.json()
    except ValueError:
        raise StorageError(
            f"Storage {action} returned {response.status_code} with a non-JSON body: "
            f"{_snippet(response)}"
        ) from None
    if not isinstance(payload, dict):
        raise StorageError(
            f"Storage {action} returned {response.status_code} with a "
            f"{type(payload).__name__} body where an object was expected"
        )
    return payload


@dataclass(frozen=True)
class StoredObject:
    path: str
    mime_type: str
    size_bytes: int
    checksum_sha256: str


def validate_upload(*, filename: str, content_type: str, size_bytes: int) -> None:
    """Validate an upload before a signed URL is ever minted.

    Extension is checked IN ADDITION to the declared MIME type, because the
    Content-Type header is client-supplied and trivially spoofed. Neither check
    is a substitute for malware scanning (deferred, see the blueprint's build
    summary) - they only keep obviously wrong files out.

    Size is validated from the client's DECLARED value here, which stops the
    common case. Supabase enforces a bucket-level maximum as the real boundary,
    so a client lying about the size is caught at upload time rather than
    consuming unbounded storage.
    """
    lowered = filename.lower()
    if not lowered or lowered in (".", ".."):
        raise StorageError("Filename is required")

    if content_type in ALLOWED_PDF_MIME:
        if not lowered.endswith(".pdf"):
            raise StorageError("Declared PDF but the filename does not end in .pdf")
        if size_bytes > MAX_PDF_BYTES:
            raise StorageError(f"PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB limit")
        return

    if content_type in ALLOWED_IMAGE_MIME:
        if not lowered.endswith((".png", ".jpg", ".jpeg", ".webp")):
            raise StorageError("Declared image but the filename has no image extension")
        if size_bytes > MAX_IMAGE_BYTES:
            raise StorageError(f"Image exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit")
        return

    raise StorageError(f"Unsupported content type: {content_type!r}")


def _sanitise_filename(filename: str) -> str:
    """Reduce a client-supplied filename to a safe stem plus a known extension.

    Splitting the extension off BEFORE stripping matters. Sanitising the whole
    string at once destroys the extension: ``"!!!???.pdf"`` reduces to ``"pdf"``,
    because the ``.`` is stripped as a leading/trailing dot and the punctuation
    around it is removed. The stored object then has no file type at all, which
    breaks dashboard identification and any extension-based dispatch later.

    So: extract the extension, keep it only if it is one we accept, and sanitise
    the stem independently.
    """
    lowered = filename.lower()
    dot = lowered.rfind(".")
    stem, extension = (lowered[:dot], lowered[dot + 1 :]) if dot > 0 else (lowered, "")

    # Only extensions the platform actually accepts survive. An arbitrary
    # extension from a client is meaningless noise at best.
    if extension not in ALLOWED_EXTENSIONS:
        extension = ""

    safe_stem = "".join(c for c in stem if c.isalnum() or c in ("-", "_")).strip("-_")
    safe_stem = safe_stem[:60] or "upload"

    return f"{safe_stem}.{extension}" if extension else safe_stem


def build_object_path(*, owner_id: str, filename: str, prefix: str = "") -> str:
    """Build a collision-proof, non-guessable storage path.

    SERVER-GENERATED, never client-supplied. The client's filename is retained
    only as a readable suffix for the dashboard; the unique component comes from
    ``uuid4``.

    The original filename is sanitised rather than used directly. A filename is
    attacker-controlled input, and unescaped path separators or ``..`` segments
    would allow writing outside the intended prefix - or, with a leading slash,
    into another tenant's namespace.
    """
    safe = _sanitise_filename(filename)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    unique = uuid.uuid4().hex[:16]
    parts = [p for p in (prefix, owner_id, stamp, f"{unique}-{safe}") if p]
    return "/".join(parts)


def storage_from_settings(settings: Settings | None = None) -> SupabaseStorage:
    """THE ONLY SUPPORTED WAY TO BUILD A STORAGE CLIENT.

    Written after finding four storage-backed endpoints in the content library that
    called ``SupabaseStorage()`` with no arguments - while ``__init__`` requires
    ``settings`` - and called methods that do not exist on this class
    (``signed_download_url``, ``object_exists``, ``signed_upload_url``). Every one of
    them raised, every one was swallowed by ``except Exception`` and reported as
    ``503 Storage not configured``, so the bulk-upload manifest, the PDF preview, the
    original-file download and the post-upload existence check could not have worked
    in production with the credentials correctly set. The owner would have been sent
    to check environment variables that were already right.

    Two things prevent a repeat: one constructor, so a signature change is a
    type-checked edit in one place; and tests that drive the endpoints through a
    stand-in storage client, so a wrong method name is a failing test rather than a
    503 nobody reads twice.
    """
    return SupabaseStorage(settings or get_settings())


class SupabaseStorage:
    """Thin async client over the Supabase Storage REST API.

    Deliberately narrow: bucket check, signed upload URL, upload, download,
    signed download URL, exists. Anything beyond that belongs in a repository.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        if not settings.supabase_url or not settings.supabase_secret_key:
            raise StorageError("Supabase Storage is not configured")
        self.settings = settings
        self._base = f"{settings.supabase_url.rstrip('/')}/storage/v1"
        self._headers = self._build_headers(settings.supabase_secret_key or "")
        self._http = http
        self._owns_http = http is None

    @staticmethod
    def _build_headers(key: str) -> dict[str, str]:
        """Build the auth headers for the key FORMAT in use.

        Verified against the live project: an ``sb_secret_...`` key sent only on
        ``Authorization: Bearer`` is rejected with
        ``403 {"error":"Unauthorized","message":"Invalid Compact JWS"}``, because
        the new keys are opaque strings rather than JWTs and nothing can verify
        them as one. The same key on the ``apikey`` header returns 200.

        So the header set depends on which key system the project is on:

          * ``sb_`` keys  - ``apikey`` only. Correct today, and it keeps working
            when Supabase deprecates the legacy keys at the end of 2026.
          * legacy JWT    - both headers. Legacy service_role keys are JWTs and
            Supabase's Storage gateway expects the bearer form for them.

        Sending both for an sb_ key also happens to work, but only because the
        platform tolerates a bearer value that exactly matches the apikey. Relying
        on that would break silently the moment the two diverge, so the format is
        detected explicitly instead.
        """
        headers = {"apikey": key}
        if not key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def __aenter__(self) -> SupabaseStorage:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
            self._owns_http = True
        return self._http

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """The only place a storage HTTP call is made.

        A DNS failure, a refused connection or a timeout is a storage outage, which is
        a 503 the operator can act on - not an "unexpected error" that reads like a bug
        in this codebase. Routing every call through here is what guarantees that,
        instead of relying on each method to remember.
        """
        try:
            return await self._client().request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise StorageError(f"Storage is unreachable ({type(exc).__name__}): {exc}") from exc

    # --------------------------------------------------------------- checks

    async def bucket_exists(self, bucket: str) -> bool:
        resp = await self._send("GET", f"{self._base}/bucket/{bucket}", headers=self._headers)
        return resp.status_code == 200

    # ------------------------------------------------------- brokered upload

    async def create_signed_upload_url(self, bucket: str, path: str) -> dict[str, Any]:
        """Mint a short-lived signed URL the CLIENT uploads to directly.

        Returns the upload URL and a token. The client PUTs the file body to the
        returned URL; the service-role key never leaves the server.

        The TTL is intentionally short (5 minutes). The URL is a bearer
        capability - anyone holding it can upload to that exact path - so the
        exposure window is minimised.
        """
        resp = await self._send(
            "POST",
            f"{self._base}/object/upload/sign/{bucket}/{path}",
            headers=self._headers,
            json={"expiresIn": UPLOAD_URL_TTL_SECONDS},
        )
        if resp.status_code not in (200, 201):
            raise StorageError(
                f"Could not create upload URL ({resp.status_code}): {resp.text[:200]}"
            )
        payload = _json_body(resp, action="upload-sign")
        url = payload.get("url") or payload.get("signedURL")
        if not url:
            raise StorageError("Upload-sign response contained no URL")
        if not url.startswith("http"):
            url = f"{self._base}{url}"
        return {"uploadUrl": url, "token": payload.get("token"), "path": path}

    # ------------------------------------------------------ direct upload

    async def upload(
        self,
        bucket: str,
        path: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        """Server-side upload. Used by the worker and by small server-generated files.

        Prefer ``create_signed_upload_url`` for anything a student uploads, so
        the bytes bypass the API. This method exists for admin tooling and for
        the worker writing processed derivatives.

        The checksum is computed here and persisted alongside the path so the
        platform can detect a corrupted or substituted object later without
        downloading it.
        """
        resp = await self._send(
            "POST",
            f"{self._base}/object/{bucket}/{path}",
            headers={**self._headers, "Content-Type": content_type, "x-upsert": "false"},
            content=content,
        )
        if resp.status_code not in (200, 201):
            raise StorageError(f"Upload failed ({resp.status_code}): {_snippet(resp)}")

        return StoredObject(
            path=path,
            mime_type=content_type,
            size_bytes=len(content),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
        )

    # ------------------------------------------------------------ retrieval

    async def signed_url(
        self, bucket: str, path: str, expires_in: int = DOWNLOAD_URL_TTL_SECONDS
    ) -> str:
        """Create a short-lived signed download URL.

        The TTL is CAPPED at one hour regardless of what the caller asks for.
        A long-lived public URL is exactly what the blueprint prohibits for
        source PDFs - and a caller passing 30 days would otherwise quietly
        defeat that policy.
        """
        expires_in = min(max(1, expires_in), MAX_SIGNED_TTL_SECONDS)
        resp = await self._send(
            "POST",
            f"{self._base}/object/sign/{bucket}/{path}",
            headers=self._headers,
            json={"expiresIn": expires_in},
        )
        if resp.status_code != 200:
            raise StorageError(f"Sign failed ({resp.status_code}): {resp.text[:200]}")
        payload = _json_body(resp, action="download-sign")
        signed = payload.get("signedURL") or payload.get("signedUrl")
        if not signed:
            raise StorageError("Sign response contained no URL")
        return f"{self.settings.supabase_url.rstrip('/')}/storage/v1{signed}"

    async def download(self, bucket: str, path: str) -> bytes:
        resp = await self._send(
            "GET", f"{self._base}/object/{bucket}/{path}", headers=self._headers
        )
        if resp.status_code != 200:
            raise StorageError(f"Download failed ({resp.status_code})")
        return resp.content

    async def exists(self, bucket: str, path: str) -> bool:
        resp = await self._send(
            "HEAD", f"{self._base}/object/{bucket}/{path}", headers=self._headers
        )
        return resp.status_code == 200

    async def delete(self, bucket: str, path: str) -> bool:
        """Delete an object.

        Used to clean up a client upload that failed validation AFTER the signed
        URL was issued - the window between minting a URL and the API confirming
        the resulting path. Without this, a rejected file stays in the bucket
        forever and consumes quota.
        """
        resp = await self._send(
            "DELETE", f"{self._base}/object/{bucket}/{path}", headers=self._headers
        )
        return resp.status_code in (200, 204)
