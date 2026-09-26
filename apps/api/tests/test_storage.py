"""Supabase Storage integration: validation, path generation, and the brokered
upload flow that browser-side identity makes necessary.

No network calls. An in-memory httpx transport stands in for Supabase so the
request shapes are asserted rather than assumed.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.integrations.supabase_storage import (
    BUCKETS,
    DOWNLOAD_URL_TTL_SECONDS,
    MAX_IMAGE_BYTES,
    MAX_PDF_BYTES,
    MAX_SIGNED_TTL_SECONDS,
    PREFIXES,
    UPLOAD_URL_TTL_SECONDS,
    StorageError,
    SupabaseStorage,
    build_object_path,
    validate_upload,
)

SUPABASE_URL = "https://project.supabase.co"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        supabase_url=SUPABASE_URL,
        supabase_secret_key="service-role-key",
    )


def make_storage(settings: Settings, handler) -> SupabaseStorage:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return SupabaseStorage(settings, http=client)


class TestUploadValidation:
    def test_accepts_a_pdf(self):
        validate_upload(filename="paper.pdf", content_type="application/pdf", size_bytes=1024)

    def test_accepts_images(self):
        for name, mime in (
            ("scan.png", "image/png"),
            ("photo.jpg", "image/jpeg"),
            ("photo.jpeg", "image/jpeg"),
            ("shot.webp", "image/webp"),
        ):
            validate_upload(filename=name, content_type=mime, size_bytes=1024)

    def test_rejects_a_mismatched_extension(self):
        """Dropping a .doc past the MIME check is the whole point."""
        with pytest.raises(StorageError, match=r"does not end in \.pdf"):
            validate_upload(
                filename="syllabus.docx", content_type="application/pdf", size_bytes=100
            )

    def test_rejects_an_unsupported_content_type(self):
        with pytest.raises(StorageError, match="Unsupported content type"):
            validate_upload(
                filename="a.exe", content_type="application/x-msdownload", size_bytes=100
            )

    def test_rejects_an_image_extension_mismatch(self):
        with pytest.raises(StorageError, match="no image extension"):
            validate_upload(filename="scan.pdf", content_type="image/png", size_bytes=100)

    def test_rejects_an_oversized_pdf(self):
        with pytest.raises(StorageError, match="MB limit"):
            validate_upload(
                filename="huge.pdf",
                content_type="application/pdf",
                size_bytes=MAX_PDF_BYTES + 1,
            )

    def test_accepts_a_pdf_exactly_at_the_limit(self):
        validate_upload(
            filename="big.pdf", content_type="application/pdf", size_bytes=MAX_PDF_BYTES
        )

    def test_rejects_an_oversized_image(self):
        with pytest.raises(StorageError, match="MB limit"):
            validate_upload(
                filename="big.png", content_type="image/png", size_bytes=MAX_IMAGE_BYTES + 1
            )

    def test_rejects_a_blank_filename(self):
        with pytest.raises(StorageError, match="Filename is required"):
            validate_upload(filename="", content_type="application/pdf", size_bytes=10)

    def test_extension_check_is_case_insensitive(self):
        validate_upload(filename="PAPER.PDF", content_type="application/pdf", size_bytes=10)


class TestObjectPathGeneration:
    """Paths are server-generated. A client-supplied path is a security hole."""

    def test_namespaces_by_owner(self):
        path = build_object_path(owner_id="uid-123", filename="paper.pdf")
        assert path.startswith("uid-123/")

    def test_is_unique_across_calls(self):
        a = build_object_path(owner_id="u", filename="paper.pdf")
        b = build_object_path(owner_id="u", filename="paper.pdf")
        assert a != b, "two uploads of the same filename must not collide"

    def test_honours_a_prefix(self):
        path = build_object_path(owner_id="u", filename="p.pdf", prefix="originals")
        assert path.startswith("originals/u/")

    def test_strips_path_traversal_attempts(self):
        """A filename is attacker-controlled input."""
        path = build_object_path(owner_id="u", filename="../../etc/passwd")
        assert ".." not in path
        assert "etc/passwd" not in path

    def test_strips_path_separators(self):
        path = build_object_path(owner_id="u", filename="a/b/c.pdf")
        assert path.count("/") == 2, f"separators survived: {path}"

    def test_strips_a_leading_slash(self):
        # A leading slash would escape the intended bucket prefix.
        path = build_object_path(owner_id="u", filename="/absolute.pdf")
        assert not path.startswith("/")
        assert "//" not in path

    def test_handles_a_filename_that_sanitises_to_nothing(self):
        path = build_object_path(owner_id="u", filename="!!!???.pdf")
        assert path.endswith(".pdf") or "upload" in path

    def test_caps_the_length_of_the_original_name(self):
        path = build_object_path(owner_id="u", filename="x" * 500 + ".pdf")
        assert len(path) < 200


class TestBrokeredUpload:
    """The flow a browser-held session requires: client uploads, API authorizes."""

    async def test_mints_a_signed_upload_url(self, settings):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"url": "/object/upload/sign/b/x?token=abc"})

        storage = make_storage(settings, handler)
        result = await storage.create_signed_upload_url("bucket", "u/1/file.pdf")

        assert "token=abc" in result["uploadUrl"]
        assert result["uploadUrl"].startswith("http")
        assert "upload/sign" in seen["url"]
        # The service-role key stays server-side; it authorizes the MINT, and the
        # client receives only the short-lived capability.
        assert seen["auth"] == "Bearer service-role-key"

    async def test_upload_url_ttl_is_short(self, settings):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"url": "/x?token=t"})

        storage = make_storage(settings, handler)
        await storage.create_signed_upload_url("bucket", "u/1/f.pdf")

        assert seen["body"]["expiresIn"] == UPLOAD_URL_TTL_SECONDS
        assert UPLOAD_URL_TTL_SECONDS <= 600, "an upload capability should be short-lived"

    async def test_raises_when_signing_fails(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="row-level security")

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="Could not create upload URL"):
            await storage.create_signed_upload_url("bucket", "u/1/f.pdf")

    async def test_raises_when_the_response_has_no_url(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="no URL"):
            await storage.create_signed_upload_url("bucket", "u/1/f.pdf")


class TestDownloadUrls:
    async def test_signs_a_download_url(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"signedURL": "/object/sign/b/p?token=xyz"})

        storage = make_storage(settings, handler)
        url = await storage.signed_url("bucket", "u/1/f.pdf")
        assert url == f"{SUPABASE_URL}/storage/v1/object/sign/b/p?token=xyz"

    async def test_caps_the_ttl_regardless_of_what_the_caller_asks(self, settings):
        """A caller requesting 30 days must not defeat the short-lived-URL policy."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"signedURL": "/x?token=t"})

        storage = make_storage(settings, handler)
        await storage.signed_url("bucket", "p", expires_in=30 * 24 * 3600)

        assert seen["body"]["expiresIn"] == MAX_SIGNED_TTL_SECONDS
        assert MAX_SIGNED_TTL_SECONDS == 3600

    async def test_raises_on_a_failed_sign(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="Sign failed"):
            await storage.signed_url("bucket", "missing.pdf")


class TestFailureModesAreOneException:
    """Every way storage can go wrong must arrive as ``StorageError``.

    WHY THIS CLASS EXISTS: three endpoints (the PDF preview, the admin download and the
    re-process button) answered 500 for every role in a live authorization sweep. The
    cause was not the endpoints - it was this client raising ``JSONDecodeError`` and
    ``httpx.ConnectError`` out of methods the endpoints wrap in ``except StorageError``.
    A gateway's HTML error page, a wrong host, a refused connection: all of them reached
    the operator as "an unexpected error occurred", which points at the wrong system and
    gives them nothing to act on.

    The endpoints' contract is "storage trouble is a 503 saying so". These are the
    inputs that used to break it.
    """

    async def test_an_html_error_page_is_not_a_json_decode_crash(self, settings):
        """A 200 carrying a gateway's HTML - what a wrong storage host returns."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text="<!DOCTYPE html><html><body><h1>Welcome to nginx</h1></body></html>",
            )

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError) as caught:
            await storage.create_signed_upload_url("bucket", "u/1/f.pdf")

        message = str(caught.value)
        assert "non-JSON body" in message
        # The markup is stripped and the useful line kept: the message is read by a
        # person in a browser tab.
        assert "Welcome to nginx" in message
        assert "<html>" not in message

    async def test_an_html_failure_page_is_reported_with_its_status(self, settings):
        """A 5xx from an edge proxy: HTML, not the JSON body the client expects."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502,
                text="<html><head><title>502 Bad Gateway</title></head></html>",
            )

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="Sign failed \\(502\\)"):
            await storage.signed_url("bucket", "u/1/f.pdf")

    async def test_a_json_body_of_the_wrong_shape_is_reported(self, settings):
        """A list where an object was expected is still a storage problem, not a bug."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="where an object was expected"):
            await storage.create_signed_upload_url("bucket", "u/1/f.pdf")

    async def test_a_connection_failure_is_a_storage_error(self, settings):
        """DNS failure, refused connection, timeout: one type, one 503."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Name or service not known", request=request)

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="unreachable"):
            await storage.signed_url("bucket", "u/1/f.pdf")

    async def test_a_timeout_is_a_storage_error(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="unreachable"):
            await storage.download("bucket", "u/1/f.pdf")

    async def test_every_request_method_goes_through_the_guard(self, settings):
        """No method may bypass ``_send``, which is what makes the promise above true.

        Read from the source rather than by calling each method: the point is that the
        NEXT method someone adds cannot quietly use ``self._client()`` directly.
        """
        import inspect
        import re

        source = inspect.getsource(SupabaseStorage)
        direct_calls = [
            line.strip()
            for line in source.splitlines()
            if re.search(r"self\._client()\.(get|post|put|delete|head|patch|request)\(", line)
        ]
        assert direct_calls == [], (
            "these calls bypass _send and can leak JSONDecodeError/httpx errors past "
            f"the except StorageError handlers: {direct_calls}"
        )


class TestDirectOperations:
    async def test_upload_returns_a_checksum(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("x-upsert") == "false"
            return httpx.Response(200, json={"Key": "u/1/f.pdf"})

        storage = make_storage(settings, handler)
        obj = await storage.upload("bucket", "u/1/f.pdf", b"hello", "application/pdf")

        assert obj.size_bytes == 5
        assert len(obj.checksum_sha256) == 64
        # Deterministic: sha256("hello")
        assert obj.checksum_sha256.startswith("2cf24dba5fb0a30e")

    async def test_upload_never_overwrites_by_default(self, settings):
        """x-upsert=false means a re-upload cannot silently replace an object.

        Overwriting a source PDF would destroy the original that a question's
        provenance points at.
        """
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["upsert"] = request.headers.get("x-upsert")
            return httpx.Response(200, json={})

        storage = make_storage(settings, handler)
        await storage.upload("bucket", "p", b"x", "application/pdf")
        assert seen["upsert"] == "false"

    async def test_upload_raises_on_failure(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(413, text="too large")

        storage = make_storage(settings, handler)
        with pytest.raises(StorageError, match="Upload failed"):
            await storage.upload("bucket", "p", b"x", "application/pdf")

    async def test_download(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"pdf-bytes")

        storage = make_storage(settings, handler)
        assert await storage.download("bucket", "p") == b"pdf-bytes"

    async def test_exists(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        storage = make_storage(settings, handler)
        assert await storage.exists("bucket", "p") is True

    async def test_delete_cleans_up_a_rejected_upload(self, settings):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        storage = make_storage(settings, handler)
        assert await storage.delete("bucket", "p") is True


class TestConfigurationGuard:
    def test_refuses_to_construct_without_credentials(self):
        """Fails loudly rather than making unauthenticated calls."""
        with pytest.raises(StorageError, match="not configured"):
            SupabaseStorage(Settings(_env_file=None))

    def test_bucket_names_match_the_blueprint(self):
        assert BUCKETS["questions"] == "question-pdfs"
        assert BUCKETS["media"] == "question-media"
        assert BUCKETS["user_uploads"] == "user-uploads"

    def test_no_bucket_name_contains_a_path_separator(self):
        """Supabase rejects a bucket name containing "/" with 400
        InvalidBucketName - confirmed against the live project.

        The blueprint's folder structure (originals/, processed/, videos/,
        doubts/) is a PATH PREFIX, not part of the bucket name. An earlier
        version of this code stored "question-pdfs/originals" as the bucket,
        which fails at the first request rather than at import. Since the storage
        URL is /object/{bucket}/{path}, a slash in the bucket also silently
        shifts a path segment into the bucket position.
        """
        for key, bucket in BUCKETS.items():
            assert "/" not in bucket, (
                f"BUCKETS[{key!r}] = {bucket!r} contains a path separator; "
                "the folder belongs in PREFIXES"
            )
            assert bucket == bucket.lower(), f"{bucket!r} must be lower case"
            assert " " not in bucket

    def test_prefixes_are_paths_not_buckets(self):
        assert PREFIXES["originals"] == "originals"
        for key, prefix in PREFIXES.items():
            assert "/" not in prefix, f"PREFIXES[{key!r}] should be a single segment"

    def test_no_bucket_is_public(self):
        # A public bucket would make the signed-URL policy meaningless.
        for name, bucket in BUCKETS.items():
            assert "public" not in bucket.lower(), f"{name} looks like a public bucket"

    def test_the_buckets_the_code_uses_are_the_ones_that_exist(self):
        """Pins the three names against the live project's actual buckets.

        This is a recorded fact, not a live check - the suite must not depend on
        network access. It fails if someone renames a bucket in code without
        creating it in Supabase, which otherwise surfaces as a 404 at the first
        upload.
        """
        assert set(BUCKETS.values()) == {"question-pdfs", "question-media", "user-uploads"}

    def test_download_ttl_is_bounded(self):
        assert DOWNLOAD_URL_TTL_SECONDS <= MAX_SIGNED_TTL_SECONDS


class TestAuthHeaderFormat:
    """The new Supabase keys are opaque strings, not JWTs.

    Verified against the live project: a secret key sent ONLY on
    `Authorization: Bearer` is rejected with
    403 {"error":"Unauthorized","message":"Invalid Compact JWS"}.
    The same key on `apikey` returns 200. Sending both works only because the
    platform tolerates a bearer value that exactly matches the apikey - so which
    headers are sent is decided by key format rather than by luck.
    """

    def test_an_sb_secret_key_uses_the_apikey_header_only(self):
        headers = SupabaseStorage._build_headers("sb_secret_abc123")
        assert headers["apikey"] == "sb_secret_abc123"
        assert "Authorization" not in headers, (
            "an sb_ key is not a JWT; sending it as a bearer token is what "
            "produces Invalid Compact JWS"
        )

    def test_a_legacy_jwt_key_keeps_the_bearer_header(self):
        """Legacy service_role keys ARE JWTs and the gateway expects the bearer
        form, so both headers stay for the migration period."""
        legacy = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
        headers = SupabaseStorage._build_headers(legacy)
        assert headers["apikey"] == legacy
        assert headers["Authorization"] == f"Bearer {legacy}"

    def test_a_publishable_key_is_also_treated_as_opaque(self):
        """The publishable key is the browser-side equivalent. It is not used by
        the backend, but the format detection must not fall through to the JWT
        branch and start sending it as a bearer token."""
        headers = SupabaseStorage._build_headers("sb_publishable_xyz")
        assert "Authorization" not in headers

    def test_the_constructed_client_uses_the_format_specific_headers(self):
        settings = Settings(
            _env_file=None,
            SUPABASE_URL="https://example.supabase.co",
            SUPABASE_SECRET_KEY="sb_secret_live_key",
        )
        client = SupabaseStorage(settings)
        assert client._headers == {"apikey": "sb_secret_live_key"}


class TestKeyNameMigration:
    """SUPABASE_SECRET_KEY is the new name; the legacy one still works."""

    def test_the_new_variable_name_is_read(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_from_new_var")
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        assert Settings(_env_file=None).supabase_secret_key == "sb_secret_from_new_var"

    def test_the_legacy_variable_name_still_works(self, monkeypatch):
        """Pydantic matches on field name, so SUPABASE_SERVICE_ROLE_KEY does NOT
        populate supabase_secret_key automatically. Without the explicit
        fallback, renaming the field would have silently disabled storage in any
        environment still holding the old variable."""
        monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "eyJlegacy.service_role.key")
        assert Settings(_env_file=None).supabase_secret_key == "eyJlegacy.service_role.key"

    def test_the_new_name_wins_when_both_are_set(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_new")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "eyJlegacy")
        assert Settings(_env_file=None).supabase_secret_key == "sb_secret_new"
