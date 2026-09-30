"""The content library, access control, notifications and permissions, against PostgreSQL.

WHAT THESE TESTS ARE FOR

The three claims that matter most in this round are also the three that are invisible
from a screenshot, so each is asserted against the DATABASE or against the HTTP status
of a route the "wrong" user should not reach:

  * a student cannot read a document their rules deny, EVEN BY CALLING THE API DIRECTLY
    - asserted on every read path (list, detail, pages, file, search), because a single
    forgotten clause is the whole vulnerability;
  * the audit log and the analytics events are actually WRITTEN, which is the claim
    that was false before this round (``analytics_events`` did not exist);
  * a permission is enforced from the DATABASE role, so revoking it takes effect on the
    next request rather than when a token expires.

The tests read rows on a second connection after the request returns, following the
standard the publishing and collections suites set.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.integrations.supabase_storage import StorageError
from app.models.content import ContentAccessRule, ContentDocument
from app.models.engagement import AnalyticsEvent, AuditLog, Notification

from ._db import observe, run_in_database
from .test_postgres_publishing import Actor, make_user

pytestmark = pytest.mark.postgres

ADMIN_UUID = uuid.uuid4()


async def make_staff(session: Any, role: str = "ADMIN") -> Any:
    return await make_user(session, role)


class FakeStorage:
    """A stand-in for SupabaseStorage, used to prove the endpoints are WIRED to it.

    The previous version of these endpoints constructed the client with the wrong
    arguments and called methods that did not exist, and no test noticed because
    nothing ever exercised the seam. This is that test: the route is given an object
    with the real method names and the assertions are about what it was asked to do.
    """

    def __init__(self, *, object_present: bool = True) -> None:
        self.object_present = object_present
        self.calls: list[tuple[str, str]] = []

    async def create_signed_upload_url(self, bucket: str, path: str) -> dict[str, Any]:
        self.calls.append(("create_signed_upload_url", path))
        return {
            "uploadUrl": f"https://example.invalid/storage/v1/object/upload/sign/{bucket}/{path}?token=abc",
            "token": "abc",
            "path": path,
        }

    async def signed_url(self, bucket: str, path: str, expires_in: int = 3600) -> str:
        self.calls.append(("signed_url", path))
        return f"https://example.invalid/storage/v1/object/sign/{bucket}/{path}?token=xyz"

    async def exists(self, bucket: str, path: str) -> bool:
        self.calls.append(("exists", path))
        return self.object_present


@contextlib.contextmanager
def fake_storage(**kwargs: Any):
    """Point the content routes at a stand-in and put the real factory back afterwards."""
    import app.api.v1.content.documents as content_documents
    import app.api.v1.content.library as content_library
    import app.api.v1.content.uploads as content_uploads
    from app.integrations.supabase_storage import storage_from_settings

    stand_in = FakeStorage(**kwargs)
    # PHASE 5. app/api/v1/content.py is now a package and each sub-module holds
    # its own binding of `storage_from_settings`, so patching the package no
    # longer intercepts it. Patch every sub-module that binds one; the stand-in
    # and the restore afterwards are unchanged.
    patched = (content_library, content_uploads, content_documents)
    for _mod in patched:
        _mod.storage_from_settings = lambda *a, **k: stand_in
    try:
        yield stand_in
    finally:
        for _mod in patched:
            _mod.storage_from_settings = storage_from_settings


async def with_curriculum(session: Any) -> dict[str, Any]:
    """Seed courses, subjects and chapters. Truncated between tests like everything else."""
    from app.seed import seed_curriculum

    ids = await seed_curriculum(session)
    await session.commit()
    return ids


async def with_defaults(session: Any) -> None:
    """Write the baseline settings and badge catalogue.

    The suite TRUNCATES every table between tests for isolation - correct, and the
    same reason the curriculum tests call the seeder. Migration 0008 creates these
    rows in a real database; this is the equivalent for a truncated one.
    """
    from app.services.platform_defaults import ensure_platform_defaults

    await ensure_platform_defaults(session)
    await session.commit()


async def seed_document(
    session: Any,
    *,
    owner: Any,
    tier: str = "PREMIUM",
    status: str = "COMPLETED",
    published: bool = True,
    pages: list[str] | None = None,
    title: str = "Financial Reporting — Study Material",
    original_filename: str = "material.pdf",
    error: str | None = None,
) -> ContentDocument:
    """A finished document with extracted text, as the pipeline would leave it."""
    from app.models.content import DocumentPage

    document = ContentDocument(
        id=uuid.uuid4(),
        title=title,
        kind="STUDY_MATERIAL",
        bucket="question-pdfs",
        storage_path=f"originals/2026/09/{uuid.uuid4().hex}/material.pdf",
        original_filename=original_filename,
        size_bytes=1024,
        checksum_sha256=uuid.uuid4().hex * 2,
        status=status,
        access_tier=tier,
        is_published=published,
        uploaded_by=owner.id,
        page_count=len(pages or []),
        extracted_chars=sum(len(p) for p in (pages or [])),
        error=error,
    )
    session.add(document)
    await session.flush()
    for index, text in enumerate(pages or [], start=1):
        session.add(
            DocumentPage(
                id=uuid.uuid4(),
                document_id=document.id,
                page_number=index,
                text=text,
                raw_text=text,
                char_count=len(text),
                extraction_tier="PYMUPDF",
            )
        )
    await session.flush()
    return document


# ============================================================ access control


def test_a_free_student_cannot_read_a_premium_document(database_url: str) -> None:
    """The whole point of the access model, asserted on EVERY read path.

    A single route that forgets the predicate is the entire vulnerability, so each
    read the library offers is checked separately rather than trusting that one check
    covers the rest.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(
            session, owner=admin, tier="PREMIUM", pages=["Ind AS 116 governs leases."]
        )
        path = f"/api/v1/content/documents/{document.id}"

        async with Actor(session, student) as client:
            listed = await client.get("/api/v1/content/library")
            detail = await client.get(path)
            pages = await client.get(f"{path}/pages")
            file_url = await client.get(f"{path}/file")
            search = await client.get("/api/v1/content/search?q=leases")

        return {
            "listed_ids": [row["id"] for row in listed.json()["data"]],
            "detail": detail.status_code,
            "pages": pages.status_code,
            "file": file_url.status_code,
            "search_hits": search.json()["data"]["hits"],
        }

    result = run_in_database(database_url, body)
    # Not one of them may see it.
    assert result["listed_ids"] == []
    assert result["detail"] == 404
    assert result["pages"] == 404
    assert result["file"] == 404
    # And search must not leak it either: the filter is on the DOCUMENT ids, so a hit
    # inside a document the caller cannot read is not reachable.
    assert result["search_hits"] == []


def test_an_explicit_allow_rule_overrides_the_tier(database_url: str) -> None:
    """A comp, a scholarship, a bundle sold over the phone."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="PREMIUM")

        async with Actor(session, student) as client:
            before = await client.get(f"/api/v1/content/documents/{document.id}")

        session.add(
            ContentAccessRule(
                id=uuid.uuid4(),
                document_id=document.id,
                scope="USER",
                effect="ALLOW",
                user_id=student.id,
                reason="Scholarship cohort",
                granted_by=admin.id,
            )
        )
        await session.commit()

        async with Actor(session, student) as client:
            after = await client.get(f"/api/v1/content/documents/{document.id}")

        return {"before": before.status_code, "after": after.status_code}

    result = run_in_database(database_url, body)
    assert result["before"] == 404
    assert result["after"] == 200


def test_a_deny_rule_beats_an_allow_and_the_tier(database_url: str) -> None:
    """Withdrawing one document from one person, without unpublishing it for everyone."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="FREE")

        async with Actor(session, student) as client:
            before = await client.get(f"/api/v1/content/documents/{document.id}")

        session.add(
            ContentAccessRule(
                id=uuid.uuid4(),
                document_id=document.id,
                scope="USER",
                effect="ALLOW",
                user_id=student.id,
                granted_by=admin.id,
            )
        )
        session.add(
            ContentAccessRule(
                id=uuid.uuid4(),
                document_id=document.id,
                scope="USER",
                effect="DENY",
                user_id=student.id,
                reason="Rights restriction",
                granted_by=admin.id,
            )
        )
        await session.commit()

        async with Actor(session, student) as client:
            after = await client.get(f"/api/v1/content/documents/{document.id}")

        return {"before": before.status_code, "after": after.status_code}

    result = run_in_database(database_url, body)
    assert result["before"] == 200  # FREE tier, free document
    assert result["after"] == 404  # denied, despite the allow


def test_an_unpublished_document_is_invisible_to_students_but_visible_to_staff(
    database_url: str,
) -> None:
    """The QA gate and the access rule are the same check, deliberately.

    A file still being reviewed must not be readable because someone guessed its id,
    and staff need to see it or review is impossible.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(
            session, owner=admin, tier="FREE", published=False, status="COMPLETED"
        )
        path = f"/api/v1/content/documents/{document.id}"

        async with Actor(session, student) as client:
            student_view = await client.get(path)
        async with Actor(session, admin) as client:
            staff_view = await client.get(path)
            admin_list = await client.get("/api/v1/admin/content/documents")
            admin_text = await client.get(f"/api/v1/admin/content/documents/{document.id}/text")

        return {
            "student": student_view.status_code,
            "staff": staff_view.status_code,
            "staff_text": admin_text.status_code,
            "staff_list_total": admin_list.json()["meta"]["total"],
        }

    result = run_in_database(database_url, body)
    assert result["student"] == 404
    assert result["staff"] == 200
    assert result["staff_text"] == 200
    # The admin list is the only place an unpublished document can be found.
    assert result["staff_list_total"] == 1


def test_search_matches_extracted_text_not_filenames(database_url: str) -> None:
    """The reason the page table exists: find the paragraph, not the file.

    The filename deliberately does NOT contain the search term, so a search that
    matched on titles alone fails this test.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        said = "The lease liability is measured at the present value of the lease payments."
        other = "Goodwill is not amortised but tested annually for impairment."
        await seed_document(
            session,
            owner=admin,
            tier="FREE",
            title="FR chapter 5",
            pages=["Cover page.", said, other],
        )
        await seed_document(
            session, owner=admin, tier="FREE", title="FR chapter 6", pages=["Cover page.", other]
        )

        async with Actor(session, student) as client:
            hits = await client.get("/api/v1/content/search?q=%22lease%20liability%22")

        data = hits.json()["data"]
        return {
            "count": data["count"],
            "titles": [hit["title"] for hit in data["hits"]],
            "pages": [hit["pageNumber"] for hit in data["hits"]],
            "excerpt": data["hits"][0]["excerpt"] if data["hits"] else "",
        }

    result = run_in_database(database_url, body)
    assert result["count"] == 1
    assert result["titles"] == ["FR chapter 5"]
    # The PAGE number, which is what makes a citation followable.
    assert result["pages"] == [2]
    # Highlighted, and the client renders this as text rather than as HTML.
    assert "<mark>" in result["excerpt"]


def test_search_never_reaches_a_document_you_cannot_read(database_url: str) -> None:
    """Two documents, one phrase, one allowed. The denied one must not appear."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        phrase = "Ind AS 116 requires a right-of-use asset to be recognised."
        await seed_document(session, owner=admin, tier="FREE", title="Free notes", pages=[phrase])
        secret = await seed_document(
            session, owner=admin, tier="PREMIUM_PLUS", title="Premium pack", pages=[phrase]
        )

        async with Actor(session, student) as client:
            hits = await client.get("/api/v1/content/search?q=%22right-of-use%22")

        data = hits.json()["data"]
        return {
            "titles": [hit["title"] for hit in data["hits"]],
            "secret_id": str(secret.id),
            "raw": hits.text,
        }

    result = run_in_database(database_url, body)
    assert result["titles"] == ["Free notes"]
    # The premium document's id must not appear in the response body at all - not in a
    # hit, not in a count, not in a debug field.
    assert result["secret_id"] not in result["raw"]


def test_the_admin_library_search_reads_the_text_inside_the_documents(database_url: str) -> None:
    """The console's search box answers "do we hold anything about this?".

    An operator's question is about CONTENT, not filenames: a 500-document library is
    only navigable if the phrase inside a document is findable, because the file is
    named after the attempt and the chapter, not after every paragraph in it.

    Two documents are seeded. One holds the phrase in its page text and says nothing
    about it in the title or the filename; the other carries the phrase in the FILENAME
    only and has no text to match. Both must come back, and the response must say which
    is which - otherwise a row whose title looks unrelated reads as a false positive.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        phrase = "A change in accounting policy is applied retrospectively."
        await seed_document(session, owner=admin, tier="FREE", title="FR chapter 1", pages=[phrase])
        await seed_document(
            session,
            owner=admin,
            tier="FREE",
            title="Scan without a text layer",
            original_filename="accounting-policy-scan.pdf",
            pages=[],
        )

        async with Actor(session, admin) as client:
            resp = await client.get("/api/v1/admin/content/documents?q=accounting%20policy")

        rows = resp.json()["data"]
        return {
            "status": resp.status_code,
            "by_title": {
                row["title"]: (row.get("matchSource"), row.get("matchedPage")) for row in rows
            },
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    # Found by the TEXT: the title and the filename of this document contain neither word.
    assert result["by_title"]["FR chapter 1"] == ("TEXT", 1)
    # Found by the METADATA, and reported as such, with no page to point at.
    assert result["by_title"]["Scan without a text layer"] == ("METADATA", None)


def test_a_percent_in_the_admin_search_is_a_character_not_a_wildcard(database_url: str) -> None:
    """A search box that returns everything when you type "%" is a broken filter.

    ``%`` and ``_`` are LIKE wildcards. Unescaped, an operator looking for a question
    that mentions "12% p.a." gets the entire library back - and the more documents there
    are, the more convincing the wrong answer looks.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        await seed_document(
            session,
            owner=admin,
            tier="FREE",
            title="GST rate chart",
            original_filename="accounting-policy-scan.pdf",
            pages=["Five."],
        )
        await seed_document(
            session, owner=admin, tier="FREE", title="Interest rates", pages=["Six."]
        )

        async with Actor(session, admin) as client:
            wildcard = await client.get("/api/v1/admin/content/documents?q=%25")
            literal = await client.get("/api/v1/admin/content/documents?q=GST")
            everything = await client.get("/api/v1/admin/content/documents")
            # The hyphen/spelling problem, in the direction an operator hits it: the file
            # is named "accounting-policy-scan.pdf" and the search box gets two words.
            hyphenated = await client.get("/api/v1/admin/content/documents?q=accounting%20policy")
            return {
                "wildcard_total": wildcard.json()["meta"]["total"],
                "literal_titles": [row["title"] for row in literal.json()["data"]],
                "all": everything.json()["meta"]["total"],
                "hyphenated_titles": [row["title"] for row in hyphenated.json()["data"]],
            }

    result = run_in_database(database_url, body)
    # No document contains a literal "%", and the two seeded rows are not the whole
    # library either - so a wildcard leak would show up as a total above zero.
    assert result["wildcard_total"] == 0
    assert result["literal_titles"] == ["GST rate chart"]
    assert result["all"] >= 2
    # Two words in the box, hyphens in the file name, one match.
    assert result["hyphenated_titles"] == ["GST rate chart"]


def test_one_students_pages_are_not_another_students(database_url: str) -> None:
    """Data isolation, asserted by body text rather than by status code."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(
            session, owner=admin, tier="PREMIUM", pages=["Confidential premium content."]
        )
        async with Actor(session, student) as client:
            response = await client.get(f"/api/v1/content/documents/{document.id}/pages")
        return {"status": response.status_code, "body": response.text}

    result = run_in_database(database_url, body)
    assert result["status"] == 404
    assert "Confidential premium content" not in result["body"]


# ============================================================ permissions


def test_a_student_cannot_reach_any_admin_route(database_url: str) -> None:
    """Server-side authorization, checked route by route.

    The frontend hides these screens; that is presentation. This asserts that calling
    the API directly - which is what an attacker does - is refused for every one of
    them.
    """

    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        document_id = uuid.uuid4()
        async with Actor(session, student) as client:
            checks = {
                "dashboard": await client.get("/api/v1/admin/dashboard"),
                "users": await client.get("/api/v1/admin/users"),
                "audit": await client.get("/api/v1/admin/audit"),
                "settings": await client.get("/api/v1/admin/settings"),
                "settings_put": await client.put(
                    "/api/v1/admin/settings", json={"key": "features.ai_assistant", "value": True}
                ),
                "badges": await client.get("/api/v1/admin/badges"),
                "badge_create": await client.post(
                    "/api/v1/admin/badges",
                    json={"code": "SNEAKY", "name": "Sneaky"},
                ),
                "notify": await client.post(
                    "/api/v1/admin/notifications",
                    json={"title": "hi", "audience": "ALL_STUDENTS"},
                ),
                "documents": await client.get("/api/v1/admin/content/documents"),
                "upload": await client.post(
                    "/api/v1/admin/content/uploads",
                    json={
                        "files": [
                            {
                                "filename": "x.pdf",
                                "size_bytes": 10,
                                "checksum_sha256": "a" * 64,
                            }
                        ]
                    },
                ),
                "bulk_metadata": await client.post(
                    "/api/v1/admin/content/bulk-metadata",
                    json={"document_ids": [str(uuid.uuid4())], "kind": "NOTES"},
                ),
                "access_rules": await client.post(
                    f"/api/v1/admin/content/documents/{document_id}/access",
                    json={"scope": "ROLE", "role": "STUDENT"},
                ),
                "permissions": await client.get("/api/v1/admin/permissions"),
                "audit_analytics": await client.get("/api/v1/admin/analytics"),
            }
        return {name: response.status_code for name, response in checks.items()}

    result = run_in_database(database_url, body)
    # 403 everywhere - not 404, because these routes exist and the caller is known.
    # The one exception the API makes is for a MALFORMED id, which FastAPI rejects at
    # validation with 422 before any dependency runs; none of the ids above are
    # malformed, so a single non-403 here is a real hole.
    for name, code in result.items():
        assert code in (403, 401), f"{name} returned {code} for a STUDENT"


def test_revoking_a_role_takes_effect_on_the_next_request(database_url: str) -> None:
    """Permissions read the DATABASE role, not only the token claim.

    This is the difference that matters on a bad day: an access token lives for an
    hour, and "we revoked their admin access" must not mean "within the hour".
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_user(session, "EDITOR")

        async with Actor(session, editor) as client:
            before = await client.get("/api/v1/admin/content/documents")

        editor.role = "STUDENT"
        await session.commit()

        async with Actor(session, editor) as client:
            after = await client.get("/api/v1/admin/content/documents")

        return {"before": before.status_code, "after": after.status_code}

    result = run_in_database(database_url, body)
    assert result["before"] == 200  # EDITOR holds VIEW_CONTENT
    assert result["after"] == 403  # demoted, and the same token now gets nowhere


def test_an_editor_cannot_manage_users_or_roles(database_url: str) -> None:
    """Rank is not the same as permission: an editor manages content, not people."""

    async def body(session) -> dict[str, Any]:
        editor = await make_user(session, "EDITOR")
        target = await make_user(session, "STUDENT")
        async with Actor(session, editor) as client:
            users = await client.get("/api/v1/admin/users")
            promote = await client.patch(f"/api/v1/admin/users/{target.id}", json={"role": "ADMIN"})
            content = await client.get("/api/v1/admin/content/documents")
        return {
            "users": users.status_code,
            "promote": promote.status_code,
            "content": content.status_code,
        }

    result = run_in_database(database_url, body)
    assert result["users"] == 403
    assert result["promote"] == 403
    assert result["content"] == 200


def test_the_owner_cannot_change_their_own_role(database_url: str) -> None:
    """Losing the only account that can grant roles is a mistake to refuse.

    Asserted for the OWNER rather than an Admin, because an Admin holds no
    MANAGE_ROLES-equivalent route access for its own rank: it may move someone BELOW
    itself and nothing else (see ``test_an_admin_cannot_create_a_peer``).
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session, "SUPER_ADMIN")
        async with Actor(session, admin) as client:
            response = await client.patch(
                f"/api/v1/admin/users/{admin.id}", json={"role": "STUDENT"}
            )
        return {"status": response.status_code, "title": response.json().get("title")}

    result = run_in_database(database_url, body)
    assert result["status"] == 409
    assert result["title"] == "Cannot change your own role"


def test_an_admin_cannot_create_a_peer_or_a_superior(database_url: str) -> None:
    """Rank-limited delegation: an Admin manages the roles below it, and no more.

    Without this rule a delegated Admin could promote an accomplice to Admin in one
    request - which is why the permission is not simply owner-only (that would leave
    the delegated Admin unable to do the job) but is bounded by rank.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session, "ADMIN")
        student = await make_user(session, "STUDENT")
        async with Actor(session, admin) as client:
            to_editor = await client.patch(
                f"/api/v1/admin/users/{student.id}", json={"role": "EDITOR"}
            )
            to_admin = await client.patch(
                f"/api/v1/admin/users/{student.id}", json={"role": "ADMIN"}
            )
            to_owner = await client.patch(
                f"/api/v1/admin/users/{student.id}", json={"role": "SUPER_ADMIN"}
            )
            own = await client.patch(f"/api/v1/admin/users/{admin.id}", json={"role": "EDITOR"})
        return {
            "to_editor": to_editor.status_code,
            "to_admin": to_admin.status_code,
            "to_owner": to_owner.status_code,
            "own": own.status_code,
            "reason": to_admin.json().get("detail", ""),
        }

    result = run_in_database(database_url, body)
    # Below its rank: allowed.
    assert result["to_editor"] == 200
    # At its rank, above it, and against itself: refused.
    assert result["to_admin"] == 403
    # SUPER_ADMIN is not an assignable value at all, so it never reaches the rank test.
    assert result["to_owner"] == 422
    assert result["own"] == 409
    assert "Only the owner can grant" in result["reason"]


def test_the_permission_matrix_is_served_not_hardcoded(database_url: str) -> None:
    """The admin UI must not carry its own copy of who may do what."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session, "ADMIN")
        async with Actor(session, admin) as client:
            matrix = await client.get("/api/v1/admin/permissions")
            mine = await client.get("/api/v1/admin/me/permissions")
        return {
            "status": matrix.status_code,
            "roles": {
                row["role"]: len(row["permissions"]) for row in matrix.json()["data"]["roles"]
            },
            "student_gets_nothing": True,
            "my_perm_count": len(mine.json()["data"]["permissions"]),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    # STUDENT holds no administrative permission at all - its access to content comes
    # from the access rules, not from this matrix.
    assert result["roles"]["STUDENT"] == 0
    assert result["roles"]["ADMIN"] > 10
    assert result["my_perm_count"] > 10


# ============================================================ audit + analytics


def test_admin_actions_are_written_to_the_audit_log(database_url: str) -> None:
    """The audit trail is evidence, so it is asserted in the TABLE, not the response."""

    async def body(session) -> dict[str, Any]:
        # SUPER_ADMIN, because one of the three actions under test is a settings
        # change, and MANAGE_SETTINGS is deliberately owner-only.
        admin = await make_staff(session, "SUPER_ADMIN")
        target = await make_user(session, "STUDENT")

        # A student who stays ACTIVE: the broadcast below resolves to recipients, and
        # "sent to 0 people" is correctly a 422 rather than a silent no-op.
        await make_user(session, "STUDENT")

        async with Actor(session, admin) as client:
            await client.patch(f"/api/v1/admin/users/{target.id}", json={"is_active": False})
            await client.put(
                "/api/v1/admin/settings",
                json={"key": "platform.name", "value": "CA Prep Test"},
            )
            await client.post(
                "/api/v1/admin/notifications",
                json={"title": "Timetable update", "audience": "ALL_STUDENTS"},
            )
            log = await client.get("/api/v1/admin/audit")

        async with observe(database_url) as other:
            rows = (await other.execute(select(AuditLog))).scalars().all()

        return {
            "actions": sorted({row.action for row in rows}),
            "count": len(rows),
            "actors": {str(row.actor_user_id) for row in rows},
            "admin_id": str(admin.id),
            "via_api": {row["action"] for row in log.json()["data"]},
            "suspended_row": next(
                (row.changes for row in rows if row.action == "user.suspended"), None
            ),
        }

    result = run_in_database(database_url, body)
    assert "user.suspended" in result["actions"]
    assert "settings.changed" in result["actions"]
    assert "notification.sent" in result["actions"]
    # The actor is recorded, and the before/after values with it: "role changed" with
    # no "from what" is an audit line that answers nothing.
    assert result["actors"] == {result["admin_id"]}
    assert result["suspended_row"]["is_active"] == {"from": "True", "to": "False"}
    # And the log is readable through the API, which is how the owner actually reads it.
    assert "user.suspended" in result["via_api"]


def test_analytics_events_are_actually_written(database_url: str) -> None:
    """The claim that was FALSE before this round, asserted against the table.

    ``analytics_events`` did not exist: no table, no writes, no reads. A test that
    checked "the analytics endpoint returns 200" would have passed on an empty screen,
    which is exactly why this one reads the rows.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(
            session, owner=admin, tier="FREE", pages=["Leases are accounted for under Ind AS 116."]
        )

        async with Actor(session, student) as client:
            # Each of these must leave a row behind.
            await client.get(f"/api/v1/content/documents/{document.id}")
            await client.get("/api/v1/content/search?q=leases")
            await client.get("/api/v1/content/library")

        async with observe(database_url) as other:
            rows = (await other.execute(select(AnalyticsEvent))).scalars().all()

        return {
            "names": sorted(row.name for row in rows),
            "count": len(rows),
            "roles": {row.role for row in rows},
            "user_ids": {str(row.user_id) for row in rows},
            "student_id": str(student.id),
            "document_props": next(
                (row.properties for row in rows if row.name == "content.document_opened"), None
            ),
        }

    result = run_in_database(database_url, body)
    assert "content.document_opened" in result["names"]
    assert "content.searched" in result["names"]
    assert result["count"] >= 2
    # The role is denormalised at write time so reporting survives a deleted account,
    # and the events are attributed to the student who caused them.
    assert result["roles"] == {"STUDENT"}
    assert result["user_ids"] == {result["student_id"]}
    assert result["document_props"]["title"].startswith("Financial Reporting")


def test_the_analytics_endpoint_reports_the_events_that_were_written(database_url: str) -> None:
    """Read-back: a dashboard that cannot see the events would be a page of zeroes."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="FREE", pages=["x" * 40])

        async with Actor(session, student) as client:
            await client.get(f"/api/v1/content/documents/{document.id}")

        async with Actor(session, admin) as client:
            summary = await client.get("/api/v1/admin/analytics?days=1")

        data = summary.json()["data"]
        return {
            "totals": {row["event"]: row["count"] for row in data["totals"]},
            "total": data["totalEvents"],
            "daily_nonempty": bool(data["daily"]),
        }

    result = run_in_database(database_url, body)
    assert result["totals"].get("content.document_opened") == 1
    assert result["total"] >= 1
    assert result["daily_nonempty"] is True


def test_the_dashboard_returns_real_numbers(database_url: str) -> None:
    """Not a wall of zeroes: the counts come from rows the test created."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        await make_user(session, "STUDENT")
        await make_user(session, "STUDENT")
        await seed_document(session, owner=admin, tier="FREE", pages=["a" * 20, "b" * 20])
        await seed_document(
            session, owner=admin, tier="PREMIUM", status="FAILED", error="OCR failed"
        )

        async with Actor(session, admin) as client:
            response = await client.get("/api/v1/admin/dashboard")

        return response.json()["data"]

    result = run_in_database(database_url, body)
    assert result["students"]["total"] == 2
    assert result["content"]["documents"] == 2
    assert result["content"]["failed"] == 1
    assert result["content"]["pages"] == 2
    assert result["content"]["extractedChars"] == 40
    assert result["money"]["revenueRupees"] == 0  # nothing has been sold in this database


# ============================================================ notifications


def test_a_broadcast_lands_in_each_students_inbox(database_url: str) -> None:
    """Fan-out at write time, and each student sees only their own row."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        first = await make_user(session, "STUDENT")
        second = await make_user(session, "STUDENT")

        async with Actor(session, admin) as client:
            sent = await client.post(
                "/api/v1/admin/notifications",
                json={
                    "title": "Mock 5 timetable is out",
                    "body": "Paper starts at 10:00 IST.",
                    "audience": "ALL_STUDENTS",
                    "link_url": "/mocks",
                },
            )
            history = await client.get("/api/v1/admin/notifications")

        async with Actor(session, first) as client:
            mine = await client.get("/api/v1/notifications")
            count = await client.get("/api/v1/notifications/unread-count")
            mark = await client.post(f"/api/v1/notifications/{mine.json()['data'][0]['id']}/read")
            after = await client.get("/api/v1/notifications/unread-count")

        async with Actor(session, second) as client:
            theirs = await client.get("/api/v1/notifications")

        async with observe(database_url) as other:
            rows = (await other.execute(select(Notification))).scalars().all()

        return {
            "recipients": sent.json()["data"]["recipients"],
            "rows": len(rows),
            "mine": mine.json()["meta"]["total"],
            "unread": count.json()["data"]["unread"],
            "after_read": after.json()["data"]["unread"],
            "mark": mark.status_code,
            "theirs": theirs.json()["meta"]["total"],
            # The second student's row is untouched by the first student's read.
            "theirs_unread": theirs.json()["meta"]["unread"],
            "history": history.json()["data"][0]["recipients"],
        }

    result = run_in_database(database_url, body)
    assert result["recipients"] == 2
    assert result["rows"] == 2
    assert result["mine"] == 1
    assert result["unread"] == 1
    assert result["after_read"] == 0
    assert result["mark"] == 200
    assert result["theirs"] == 1
    assert result["theirs_unread"] == 1
    assert result["history"] == 2


def test_marking_someone_elses_notification_read_is_a_404(database_url: str) -> None:
    """The ``user_id`` clause on the update, asserted in both directions."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        owner = await make_user(session, "STUDENT")
        stranger = await make_user(session, "STUDENT")

        async with Actor(session, admin) as client:
            await client.post(
                "/api/v1/admin/notifications",
                json={"title": "For one student", "audience": "ONE_USER", "user_id": str(owner.id)},
            )

        async with Actor(session, owner) as client:
            mine = await client.get("/api/v1/notifications")
        notification_id = mine.json()["data"][0]["id"]

        async with Actor(session, stranger) as client:
            attempt = await client.post(f"/api/v1/notifications/{notification_id}/read")

        async with Actor(session, owner) as client:
            still_unread = await client.get("/api/v1/notifications/unread-count")

        return {
            "attempt": attempt.status_code,
            "still_unread": still_unread.json()["data"]["unread"],
        }

    result = run_in_database(database_url, body)
    assert result["attempt"] == 404
    assert result["still_unread"] == 1


def test_a_notification_cannot_carry_an_external_link(database_url: str) -> None:
    """An admin-authored absolute URL rendered as a link is a phishing primitive."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        await make_user(session, "STUDENT")
        async with Actor(session, admin) as client:
            bad = await client.post(
                "/api/v1/admin/notifications",
                json={
                    "title": "Reset your password",
                    "audience": "ALL_STUDENTS",
                    "link_url": "https://caprep-login.evil.example/",
                },
            )
            good = await client.post(
                "/api/v1/admin/notifications",
                json={"title": "Timetable", "audience": "ALL_STUDENTS", "link_url": "/mocks"},
            )
        return {"bad": bad.status_code, "good": good.status_code}

    result = run_in_database(database_url, body)
    assert result["bad"] == 422
    assert result["good"] == 201


# ============================================================ gamification


def test_points_and_badges_survive_the_round_trip(database_url: str) -> None:
    """Reads over data the EXISTING practice loop writes, plus the new catalogue."""

    async def body(session) -> dict[str, Any]:
        from app.models.enums import PointsReason
        from app.models.progress import UserBadge
        from app.repositories.progress import SqlProgressRepository

        await with_defaults(session)
        student = await make_user(session, "STUDENT")
        repo = SqlProgressRepository(session)
        await repo.award(
            user_id=student.id, reason=PointsReason.QUESTION_CORRECT, reference_id="q-1"
        )
        await repo.award(
            user_id=student.id, reason=PointsReason.QUESTION_CORRECT, reference_id="q-2"
        )
        session.add(
            UserBadge(
                id=uuid.uuid4(),
                user_id=student.id,
                badge_code="FIRST_STEPS",
                earned_at=datetime.now(UTC),
            )
        )
        await session.commit()

        async with Actor(session, student) as client:
            points = await client.get("/api/v1/gamification/points")
            badges = await client.get("/api/v1/gamification/badges")
            achievements = await client.get("/api/v1/gamification/achievements")

        # The catalogue the migration seeded must be visible, with the earned flag set
        # on exactly one badge.
        catalogue = badges.json()["data"]["badges"]
        return {
            "points": points.json()["data"]["total"],
            "ledger_rows": len(points.json()["data"]["recent"]),
            "badge_count": len(catalogue),
            "earned": [row["code"] for row in catalogue if row["earned"]],
            "achievements": achievements.json()["data"],
        }

    result = run_in_database(database_url, body)
    assert result["points"] == 20  # 2 x QUESTION_CORRECT
    assert result["ledger_rows"] == 2
    # Seeded by migration 0008: the catalogue the existing user_badges rows had no
    # definition for.
    assert result["badge_count"] >= 8
    assert result["earned"] == ["FIRST_STEPS"]
    assert result["achievements"]["badgesEarned"] == 1
    assert result["achievements"]["rank"] == 1


def test_the_leaderboard_ranks_by_points_and_never_exposes_an_email(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        from app.models.enums import PointsReason
        from app.repositories.progress import SqlProgressRepository

        await with_defaults(session)
        student = await make_user(session, "STUDENT")
        other = await make_user(session, "STUDENT")
        repo = SqlProgressRepository(session)
        for index in range(3):
            await repo.award(
                user_id=other.id,
                reason=PointsReason.QUESTION_CORRECT,
                reference_id=f"o-{index}",
            )
        await repo.award(
            user_id=student.id, reason=PointsReason.QUESTION_CORRECT, reference_id="mine"
        )
        await session.commit()

        async with Actor(session, student) as client:
            board = await client.get("/api/v1/gamification/leaderboard?window=all")

        data = board.json()["data"]
        return {
            "entries": data["entries"],
            "you": data["you"],
            "raw": board.text,
            "other_email": other.email,
        }

    result = run_in_database(database_url, body)
    assert result["entries"][0]["points"] == 30
    assert result["entries"][0]["rank"] == 1
    assert result["you"]["rank"] == 2
    assert result["you"]["points"] == 10
    # A leaderboard row carries a display name and a total. An email in the body would
    # make an encouragement feature into an enumeration surface.
    assert result["other_email"] not in result["raw"]
    # The caller's own row is flagged, which is how a student finds themselves.
    assert any(entry["isYou"] for entry in result["entries"])


def test_a_badge_can_be_awarded_by_hand_and_is_idempotent(database_url: str) -> None:
    """Manual awards exist for what no rule can express, and must not double-award."""

    async def body(session) -> dict[str, Any]:
        from app.models.progress import UserBadge

        await with_defaults(session)
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        # Read `student_id` NOW: HTTP calls expire the instance, and touching an expired
        # attribute later triggers a lazy load outside the async greenlet.
        student_id = student.id

        async with Actor(session, admin) as client:
            catalogue = await client.get("/api/v1/admin/badges")
            badge_id = next(
                row["id"]
                for row in catalogue.json()["data"]["badges"]
                if row["code"] == "MOCK_FINISHER"
            )
            first = await client.post(f"/api/v1/admin/badges/{badge_id}/award?user_id={student_id}")
            second = await client.post(
                f"/api/v1/admin/badges/{badge_id}/award?user_id={student_id}"
            )

        async with observe(database_url) as other:
            rows = (
                (await other.execute(select(UserBadge).where(UserBadge.user_id == student_id)))
                .scalars()
                .all()
            )
            inbox = (
                (
                    await other.execute(
                        select(Notification).where(Notification.user_id == student_id)
                    )
                )
                .scalars()
                .all()
            )

        return {
            "first": first.json()["data"],
            "second": second.json()["data"],
            "badge_rows": len(rows),
            "notified": len(inbox),
            "notification_kind": inbox[0].kind if inbox else None,
        }

    result = run_in_database(database_url, body)
    assert result["first"]["awarded"] is True
    assert result["second"]["awarded"] is False
    assert result["badge_rows"] == 1  # the unique constraint held
    # And the student is told, in their inbox, with a link to the achievements screen.
    assert result["notified"] == 1
    assert result["notification_kind"] == "ACHIEVEMENT"


def test_the_leaderboard_can_be_turned_off_by_a_setting(database_url: str) -> None:
    """A feature flag that actually gates the feature, not just the button."""

    async def body(session) -> dict[str, Any]:
        from app.models.engagement import PlatformSetting

        await with_defaults(session)
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")

        async with Actor(session, admin) as client:
            on = await client.get("/api/v1/gamification/leaderboard")

        row = (
            await session.execute(
                select(PlatformSetting).where(PlatformSetting.key == "features.leaderboard")
            )
        ).scalar_one()
        row.value = {"value": False}
        await session.commit()

        async with Actor(session, student) as client:
            off = await client.get("/api/v1/gamification/leaderboard")
            badges = await client.get("/api/v1/gamification/badges")

        return {
            "on": on.status_code,
            "off": off.status_code,
            "off_title": off.json().get("title"),
            "badges_still_work": badges.status_code,
        }

    result = run_in_database(database_url, body)
    assert result["on"] == 200
    assert result["off"] == 403
    assert result["off_title"] == "Leaderboard disabled"
    # Turning off the leaderboard must not turn off the rest of gamification.
    assert result["badges_still_work"] == 200


# ============================================================ bulk upload


def test_a_batch_reports_duplicates_before_a_single_byte_moves(database_url: str) -> None:
    """Checksum-based duplicate detection, which is what makes 500 files practical.

    The library is seeded with one document; a manifest containing the same checksum
    must be reported as a duplicate and given NO upload URL.
    """

    async def body(session) -> dict[str, Any]:
        from app.integrations.supabase_storage import build_object_path

        admin = await make_staff(session)
        checksum = "c" * 64
        existing = ContentDocument(
            id=uuid.uuid4(),
            title="Already here",
            kind="STUDY_MATERIAL",
            bucket="question-pdfs",
            storage_path=build_object_path(
                prefix="originals", filename="here.pdf", owner_id="batch"
            ),
            original_filename="here.pdf",
            checksum_sha256=checksum,
            status="COMPLETED",
            access_tier="FREE",
            is_published=True,
        )
        session.add(existing)
        await session.commit()

        with fake_storage() as storage:
            async with Actor(session, admin) as client:
                response = await client.post(
                    "/api/v1/admin/content/uploads",
                    json={
                        "files": [
                            {
                                "filename": "here.pdf",
                                "size_bytes": 1024,
                                "checksum_sha256": checksum,
                            },
                            {
                                "filename": "new.pdf",
                                "size_bytes": 2048,
                                "checksum_sha256": "d" * 64,
                            },
                        ],
                        "access_tier": "PREMIUM",
                    },
                )

        assert response.status_code == 201, response.text
        data = response.json()["data"]
        async with observe(database_url) as other:
            documents = (await other.execute(select(ContentDocument))).scalars().all()

        return {
            "status": response.status_code,
            "accepted": data["acceptedCount"],
            "skipped": data["skippedCount"],
            "skip_reason": data["skipped"][0]["reason"],
            "skip_points_at": data["skipped"][0]["existingDocumentId"],
            "existing_id": str(existing.id),
            "has_batch": all(row["batchId"] == data["batchId"] for row in []),
            "upload_url_is_signed": "token=" in data["accepted"][0]["uploadUrl"],
            # The stand-in was actually called, which is the assertion that would have
            # caught `SupabaseStorage()` with no arguments and `signed_upload_url`.
            "storage_calls": [name for name, _ in storage.calls],
            "storage_path_is_server_generated": data["accepted"][0]["uploadUrl"].find("new.pdf")
            > 0,
            "rows": len(documents),
            # The accepted file got a row in UPLOADED, not COMPLETED: nothing has been
            # processed yet, and saying otherwise would be the empty-page lie again.
            "new_row_status": next(
                row.status for row in documents if row.original_filename == "new.pdf"
            ),
            "batch_id": data["batchId"],
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 201
    assert result["accepted"] == 1
    assert result["skipped"] == 1
    assert result["skip_reason"] == "DUPLICATE"
    assert result["skip_points_at"] == result["existing_id"]
    assert result["upload_url_is_signed"] is True
    assert result["storage_calls"] == ["create_signed_upload_url"]
    assert result["storage_path_is_server_generated"] is True
    assert result["rows"] == 2  # the existing one plus the one new row, not three
    assert result["new_row_status"] == "UPLOADED"


def test_a_bad_file_in_the_manifest_is_refused_before_any_row_is_created(
    database_url: str,
) -> None:
    """All-or-nothing validation: 499 half-created documents is worse than a refusal."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        async with Actor(session, admin) as client:
            response = await client.post(
                "/api/v1/admin/content/uploads",
                json={
                    "files": [
                        {
                            "filename": "fine.pdf",
                            "size_bytes": 1024,
                            "checksum_sha256": "e" * 64,
                        },
                        {
                            "filename": "malware.exe",
                            "size_bytes": 1024,
                            "content_type": "application/x-msdownload",
                            "checksum_sha256": "f" * 64,
                        },
                    ]
                },
            )
        async with observe(database_url) as other:
            rows = (await other.execute(select(ContentDocument))).scalars().all()
        return {
            "status": response.status_code,
            "detail": response.json().get("detail", ""),
            "rows": len(rows),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 422
    assert "malware.exe" in result["detail"]
    assert result["rows"] == 0


def test_batch_progress_is_computed_from_the_rows(database_url: str) -> None:
    """Progress survives a closed browser, because it is a query and not a tally."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        batch = uuid.uuid4()
        for status_name in ("COMPLETED", "COMPLETED", "FAILED", "QUEUED"):
            # The RETURNED instance, not a re-query: `created_at` has second resolution
            # and four rows written in the same second tie, so "latest by created_at"
            # silently returned the same row four times.
            document = await seed_document(
                session,
                owner=admin,
                tier="FREE",
                status=status_name,
                pages=["x"] if status_name == "COMPLETED" else None,
            )
            document.batch_id = batch
        await session.commit()

        async with Actor(session, admin) as client:
            response = await client.get(f"/api/v1/admin/content/batches/{batch}")

        return response.json()["data"]

    result = run_in_database(database_url, body)
    assert result["total"] == 4
    assert result["done"] == 2
    assert result["failed"] == 1
    assert result["progressPercent"] == 50
    assert len(result["failures"]) == 1


def test_bulk_metadata_categorises_a_selection(database_url: str) -> None:
    """The step that turns 500 loose files into a syllabus."""

    async def body(session) -> dict[str, Any]:
        from app.models.curriculum import Course

        await with_curriculum(session)
        admin = await make_staff(session)
        documents = [
            await seed_document(session, owner=admin, tier="FREE", title=f"File {i}")
            for i in range(3)
        ]
        course = (await session.execute(select(Course).order_by(Course.code).limit(1))).scalar_one()
        await session.commit()

        async with Actor(session, admin) as client:
            response = await client.post(
                "/api/v1/admin/content/bulk-metadata",
                json={
                    "document_ids": [str(document.id) for document in documents[:2]],
                    "course_id": str(course.id),
                    "kind": "NOTES",
                    "access_tier": "PREMIUM",
                },
            )
            listing = await client.get(
                f"/api/v1/admin/content/documents?course_id={course.id}&kind=NOTES"
            )
            student = await make_user(session, "STUDENT")

        async with Actor(session, student) as client:
            # A free student must not see documents categorised as PREMIUM.
            library = await client.get("/api/v1/content/library")

        return {
            "updated": response.json()["data"]["updated"],
            "listed": listing.json()["meta"]["total"],
            "student_sees": library.json()["meta"]["total"],
        }

    result = run_in_database(database_url, body)
    assert result["updated"] == 2
    assert result["listed"] == 2
    # Two of the three were moved to PREMIUM; the third is still FREE and visible.
    assert result["student_sees"] == 1


def test_reprocessing_replaces_text_without_losing_the_document(database_url: str) -> None:
    """The original file survives every run, and a re-run does not duplicate pages."""

    async def body(session) -> dict[str, Any]:
        from app.models.content import DocumentPage
        from app.repositories.content import SqlContentStore

        admin = await make_staff(session)
        document = await seed_document(
            session, owner=admin, tier="FREE", pages=["first pass", "second page"]
        )
        await session.commit()

        store = SqlContentStore(session)
        written = await store.replace_pages(
            document.id,
            [
                {"page_number": 1, "text": "better pass", "tier": "TESSERACT", "used_ocr": True},
            ],
        )
        await session.commit()

        refreshed = (
            await session.execute(select(ContentDocument).where(ContentDocument.id == document.id))
        ).scalar_one()
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document.id)
                )
            )
            .scalars()
            .all()
        )
        return {
            "written": written,
            "page_rows": len(pages),
            "text": pages[0].text if pages else None,
            "ocr": pages[0].used_ocr if pages else None,
            # The POINT: the document and its object path are untouched by a re-process.
            "storage_path_intact": refreshed.storage_path.endswith("material.pdf"),
            "still_completed": refreshed.status,
        }

    result = run_in_database(database_url, body)
    assert result["written"] == 1
    assert result["page_rows"] == 1  # replaced, not appended
    assert result["text"] == "better pass"
    assert result["ocr"] is True
    assert result["storage_path_intact"] is True
    assert result["still_completed"] == "COMPLETED"


def test_reprocess_can_be_pressed_twice(database_url: str) -> None:
    """The retry button has to work on the SECOND press, against a real database.

    ``uq_ingestion_job_object`` permits one ingestion job per ``(bucket, storage_path)``.
    ``create_job`` ignored that, so the second press inserted a row the constraint
    refused and the request ended in a 500 carrying a PostgreSQL message - on the exact
    path an admin uses after a failure has been reported.

    Asserted over HTTP, twice, because that is the shape the bug had: the FIRST press
    was fine. A test that called it once would have passed throughout.
    """

    async def body(session) -> dict[str, Any]:
        from app.models.ingestion import IngestionJob

        admin = await make_staff(session)
        document = await seed_document(session, owner=admin, tier="FREE", pages=["one page"])
        await session.commit()

        async with Actor(session, admin) as client:
            first = await client.post(f"/api/v1/admin/content/documents/{document.id}/reprocess")
            second = await client.post(f"/api/v1/admin/content/documents/{document.id}/reprocess")

        jobs = (
            (
                await session.execute(
                    select(IngestionJob).where(IngestionJob.bucket == document.bucket)
                )
            )
            .scalars()
            .all()
        )
        refreshed = (
            await session.execute(select(ContentDocument).where(ContentDocument.id == document.id))
        ).scalar_one()
        return {
            "first_status": first.status_code,
            "second_status": second.status_code,
            "second_body": second.text[:200],
            "job_rows": len(jobs),
            "history_entries": len(jobs[0].stage_history or []) if jobs else 0,
            "stage": jobs[0].stage if jobs else None,
            "error_cleared": refreshed.error,
            "status": refreshed.status,
            "document_job_id": (
                str(refreshed.ingestion_job_id) if refreshed.ingestion_job_id else None
            ),
            "job_id": str(jobs[0].id) if jobs else None,
        }

    result = run_in_database(database_url, body)
    assert result["first_status"] == 200, result["second_body"]
    assert result["second_status"] == 200, result["second_body"]
    # ONE row, re-opened - not a second job, and not a failed request.
    assert result["job_rows"] == 1
    assert result["stage"] == "QUEUED"
    # The append-only history shows both presses, so the admin can see it was retried.
    assert result["history_entries"] == 2
    # The document points at that same job and its previous error is cleared.
    assert result["document_job_id"] == result["job_id"]
    assert result["error_cleared"] is None
    assert result["status"] == "QUEUED"


def test_archiving_hides_a_document_from_students_and_keeps_it_for_audit(
    database_url: str,
) -> None:
    """Delete is a soft delete: generated questions still point at the source row."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="FREE", pages=["x" * 30])
        await session.commit()
        path = f"/api/v1/content/documents/{document.id}"

        async with Actor(session, student) as client:
            before = await client.get(path)

        async with Actor(session, admin) as client:
            archived = await client.delete(f"/api/v1/admin/content/documents/{document.id}")
            still_in_admin_list = await client.get(
                "/api/v1/admin/content/documents?include_archived=true"
            )
            hidden_by_default = await client.get("/api/v1/admin/content/documents")

        # The archived row, read while it IS archived - restore is the last act and
        # would otherwise overwrite the state this test is about.
        async with observe(database_url) as other:
            row = (await other.execute(select(ContentDocument))).scalar_one()
            archived_row = {
                "status": row.status,
                "deleted_at_set": row.deleted_at is not None,
                "storage_path": row.storage_path,
                "published": row.is_published,
            }

        async with Actor(session, student) as client:
            after = await client.get(path)

        async with Actor(session, admin) as client:
            restored = await client.post(f"/api/v1/admin/content/documents/{document.id}/restore")

        async with observe(database_url) as other:
            row_after_restore = (await other.execute(select(ContentDocument))).scalar_one()
            refreshed_published = row_after_restore.is_published

        return {
            "before": before.status_code,
            "archived": archived.status_code,
            "after": after.status_code,
            "row_still_there": True,
            "status": archived_row["status"],
            "deleted_at_set": archived_row["deleted_at_set"],
            "storage_path": archived_row["storage_path"],
            "published_while_archived": archived_row["published"],
            "admin_can_still_see": still_in_admin_list.json()["meta"]["total"],
            # Out of the DEFAULT admin list (an archive that still shows up is not an
            # archive), and reversible - which is the difference from a delete.
            "hidden_by_default": hidden_by_default.json()["meta"]["total"],
            "restored": restored.status_code,
            "published_after_restore": refreshed_published,
        }

    result = run_in_database(database_url, body)
    assert result["before"] == 200
    assert result["archived"] == 200
    assert result["after"] == 404
    assert result["published_while_archived"] is False
    assert result["row_still_there"] is True
    assert result["status"] == "ARCHIVED"
    # archived, NOT deleted: `deleted_at` stays null so the document is still findable
    # by its owner.
    assert result["deleted_at_set"] is False
    # The object path is unchanged, so the file is still in storage and the questions
    # generated from it still have a source.
    assert result["storage_path"].endswith("material.pdf")
    assert result["admin_can_still_see"] == 1
    assert result["hidden_by_default"] == 0
    assert result["restored"] == 200
    # Restoring does NOT re-publish: a document must not reappear in front of students
    # because somebody clicked restore while tidying the archive.
    assert result["published_after_restore"] is False


def test_access_rules_are_managed_over_http(database_url: str) -> None:
    """Grant and revoke through the API, then prove the effect on the student."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="PREMIUM_PLUS")
        await session.commit()
        path = f"/api/v1/content/documents/{document.id}"

        async with Actor(session, admin) as client:
            granted = await client.post(
                f"/api/v1/admin/content/documents/{document.id}/access",
                json={"scope": "USER", "effect": "ALLOW", "user_id": str(student.id)},
            )
            rule_id = granted.json()["data"]["id"]
            listed = await client.get(f"/api/v1/admin/content/documents/{document.id}/access")

        async with Actor(session, student) as client:
            with_rule = await client.get(path)

        async with Actor(session, admin) as client:
            await client.delete(f"/api/v1/admin/content/access/{rule_id}")

        async with Actor(session, student) as client:
            after_revoke = await client.get(path)

        # A rule with no target is refused rather than treated as a wildcard.
        async with Actor(session, admin) as client:
            invalid = await client.post(
                f"/api/v1/admin/content/documents/{document.id}/access",
                json={"scope": "ROLE"},
            )

        return {
            "granted": granted.status_code,
            "rules_listed": len(listed.json()["data"]["rules"]),
            "with_rule": with_rule.status_code,
            "after_revoke": after_revoke.status_code,
            "invalid": invalid.status_code,
        }

    result = run_in_database(database_url, body)
    assert result["granted"] == 201
    assert result["rules_listed"] == 1
    assert result["with_rule"] == 200
    assert result["after_revoke"] == 404  # revoked, so the tier applies again
    # The CHECK constraint did its job: a ROLE rule with no role is unrepresentable.
    assert result["invalid"] in (409, 422)


def test_an_expired_rule_stops_granting(database_url: str) -> None:
    """Time-boxed access: a trial, a semester, a refund window."""

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="PREMIUM_PLUS")
        session.add(
            ContentAccessRule(
                id=uuid.uuid4(),
                document_id=document.id,
                scope="USER",
                effect="ALLOW",
                user_id=student.id,
                expires_at=datetime.now(UTC) - timedelta(hours=1),
                granted_by=admin.id,
            )
        )
        await session.commit()

        async with Actor(session, student) as client:
            response = await client.get(f"/api/v1/content/documents/{document.id}")

        return {"status": response.status_code}

    result = run_in_database(database_url, body)
    assert result["status"] == 404


def test_an_upload_that_never_landed_is_reported_rather_than_indexed(database_url: str) -> None:
    """`exists()` is the check that stops the pipeline indexing a file nobody stored.

    A signed upload URL is a capability: the client can take it and never use it, or
    die halfway. Confirming the object exists before queueing extraction is what turns
    "the admin says it uploaded" into "the bytes are there".
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        document = await seed_document(session, owner=admin, tier="FREE", pages=None)
        await session.commit()

        with fake_storage(object_present=False) as storage:
            async with Actor(session, admin) as client:
                missing = await client.post(f"/api/v1/admin/content/documents/{document.id}/start")

        with fake_storage(object_present=True) as storage_ok:
            async with Actor(session, admin) as client:
                confirmed = await client.post(
                    f"/api/v1/admin/content/documents/{document.id}/start"
                )

        async with observe(database_url) as other:
            row = (await other.execute(select(ContentDocument))).scalar_one()

        return {
            "missing_status": missing.status_code,
            "missing_title": missing.json().get("title", ""),
            "exists_called": [name for name, _ in storage.calls],
            "ok_status": confirmed.status_code,
            "ok_calls": [name for name, _ in storage_ok.calls],
            "status_after": row.status,
            "job_id_set": row.ingestion_job_id is not None,
        }

    result = run_in_database(database_url, body)

    assert result["exists_called"] == ["exists"]
    assert result["missing_status"] == 409
    assert result["missing_title"] == "Upload incomplete"
    assert result["ok_status"] in (200, 201), result
    assert result["ok_calls"] == ["exists"]
    # A job row now exists, so the worker has something to pick up.
    assert result["job_id_set"] is True


def test_a_storage_outage_is_a_503_and_not_a_500(database_url: str) -> None:
    """Every storage-backed read answers 503 when storage cannot be reached.

    FOUND BY THE LIVE AUTHORIZATION SWEEP, which counts 5xx separately from 403s: the
    admin download, the student file link and the PDF preview answered 500 for EVERY
    role. The storage client was raising ``StorageError`` - correctly - and those routes
    had no handler, so FastAPI's catch-all reported "an unexpected error occurred".

    That message points at this codebase. The truth was that storage was refusing to
    sign a URL, which is an operational fact with an operational fix. 503 says so, and
    the app-level handler in ``main.py`` is what makes it true for routes that have not
    been written yet.

    The stub raises the same exception a real outage does, which is the point: the test
    asserts the CONTRACT ("any StorageError becomes a 503"), not one particular failure.
    """

    class DeadStorage:
        async def signed_url(self, bucket: str, path: str, expires_in: int = 3600) -> str:
            raise StorageError("Sign failed (501): storage host not found")

        async def exists(self, bucket: str, path: str) -> bool:
            raise StorageError("Storage is unreachable (ConnectError)")

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        document = await seed_document(
            session, owner=admin, tier="FREE", pages=["a page"], published=True
        )
        await session.commit()

        import app.api.v1.content.documents as content_documents
        import app.api.v1.content.library as content_library

        stand_in = DeadStorage()
        # The routes resolve the factory from their own module namespace, so the patch
        # goes there - the same seam the existing fake_storage helper uses. Since the
        # Phase 5 split, "their own module namespace" is one module per sub-module:
        # the download route lives in documents, the library file route in library.
        patched = (content_documents, content_library)
        originals = [_mod.storage_from_settings for _mod in patched]
        for _mod in patched:
            _mod.storage_from_settings = lambda *a, **k: stand_in  # type: ignore[assignment]
        try:
            async with Actor(session, admin) as client:
                download = await client.get(
                    f"/api/v1/admin/content/documents/{document.id}/download"
                )
            student = await make_user(session, "STUDENT")
            async with Actor(session, student) as client:
                file_link = await client.get(f"/api/v1/content/documents/{document.id}/file")
            return {
                "admin_download": download.status_code,
                "admin_body": download.json(),
                "file_link": file_link.status_code,
                "file_body": file_link.json(),
            }
        finally:
            for _mod, _original in zip(patched, originals, strict=True):
                _mod.storage_from_settings = _original

    result = run_in_database(database_url, body)
    assert result["admin_download"] == 503, result["admin_body"]
    assert result["file_link"] == 503, result["file_body"]
    for body_ in (result["admin_body"], result["file_body"]):
        assert body_["status"] == 503
        # The message must describe STORAGE, not "an unexpected error": the operator's
        # next move depends on knowing which dependency is down.
        assert "storage" in body_["detail"].lower() or "sign" in body_["detail"].lower()
        assert "unexpected error" not in body_["detail"].lower()


def test_a_student_link_is_a_short_lived_signed_url_never_a_public_one(
    database_url: str,
) -> None:
    """Students reach the file through a signed URL that expires, and through nothing else.

    The bucket is private. The only credential the browser ever sees is a URL good for
    one hour, minted per request after the access rules have been applied - never a
    storage key, never a public object path.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_staff(session)
        student = await make_user(session, "STUDENT")
        document = await seed_document(
            session, owner=admin, tier="FREE", pages=["page one"], published=True
        )
        await session.commit()

        with fake_storage() as storage:
            async with Actor(session, student) as client:
                link = await client.get(f"/api/v1/content/documents/{document.id}/file")

        return {
            "status": link.status_code,
            "payload": link.json().get("data", {}),
            "calls": [name for name, _ in storage.calls],
        }

    result = run_in_database(database_url, body)

    assert result["status"] == 200, result
    assert result["calls"] == ["signed_url"]
    assert result["payload"]["url"].startswith("https://")
    # Expires, and the payload says when: the student UI shows a real expiry, not a
    # promise.
    assert result["payload"]["expiresInSeconds"] > 0
    assert "secret" not in json.dumps(result["payload"]).lower()
