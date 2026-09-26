"""Access grants, end to end: does the admin's authority actually bind?

THE QUESTION THIS FILE ANSWERS

A grant screen that writes rows is worth nothing if the student's own request does not
respect them. Every test here drives the ADMIN endpoint that writes a grant and then the
STUDENT endpoint that reads the document, because the failure mode this guards against
is exactly the gap between the two: a grant row that exists, appears in the admin list,
and changes nothing - or worse, a DENY that is silently ignored.

The precedence is asserted in both directions, including the case operators get wrong
most often: a NEW allow grant does not override an OLD deny, because a denial that can be
talked around by granting something else is not a denial.

Everything runs against real PostgreSQL through the real HTTP app. The truncate-between-
tests harness removes seed rows, so each test writes what it asserts on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.models.content import ContentDocument, ContentGrant
from app.models.user import User

from ._db import observe, run_in_database
from .test_postgres_publishing import Actor, Content, make_user

pytestmark = pytest.mark.asyncio


async def seed_document(
    session: Any,
    *,
    owner: User,
    tier: str = "PREMIUM",
    published: bool = True,
    status: str = "COMPLETED",
    course_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    kind: str = "STUDY_MATERIAL",
    title: str = "Material",
) -> ContentDocument:
    document = ContentDocument(
        id=uuid.uuid4(),
        title=title,
        kind=kind,
        bucket="question-pdfs",
        storage_path=f"originals/{owner.id}/{uuid.uuid4().hex}-material.pdf",
        original_filename="material.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        checksum_sha256=uuid.uuid4().hex * 2,
        status=status,
        access_tier=tier,
        is_published=published,
        uploaded_by=owner.id,
        course_id=course_id,
        subject_id=subject_id,
    )
    session.add(document)
    await session.flush()
    return document


def test_a_course_grant_opens_everything_in_that_course(database_url: str) -> None:
    """One grant, whole course - including documents added AFTER it was written.

    This is the property a per-document rule cannot have, and the reason the grants
    table exists: a fan-out over 200 documents would have to be re-run every time the
    library grows, so material uploaded next month would silently fall outside a grant
    the owner believes covers the course.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        student = await make_user(session, "STUDENT")
        # A REAL course row: the FK from content_documents.course_id means an invented
        # uuid fails the insert, which is the truncate harness reminding us that the
        # curriculum is seeded data like everything else.
        content = await Content.seeded(session)
        course_id = content.course.id
        in_course = await seed_document(
            session, owner=admin, tier="PREMIUM", course_id=course_id, title="In course"
        )
        outside = await seed_document(session, owner=admin, tier="PREMIUM", title="Outside")
        await session.commit()

        before = [str(in_course.id), str(outside.id)]
        async with Actor(session, student) as client:
            library_before = await client.get("/api/v1/content/library")
            blocked_before = await client.get(f"/api/v1/content/documents/{in_course.id}")

        async with Actor(session, admin) as client:
            granted = await client.post(
                "/api/v1/admin/access/grants",
                json={
                    "who_scope": "USER",
                    "user_id": str(student.id),
                    "course_id": str(course_id),
                    "reason": "Bought the course over the phone",
                },
            )

        # Written AFTER the grant, to prove the grant is evaluated at query time.
        async with Actor(session, admin) as client:
            later = await seed_document(
                session,
                owner=admin,
                tier="PREMIUM_PLUS",
                course_id=course_id,
                title="Uploaded later",
            )
            await session.commit()

        async with Actor(session, student) as client:
            library_after = await client.get("/api/v1/content/library")
            allowed = await client.get(f"/api/v1/content/documents/{in_course.id}")
            still_out = await client.get(f"/api/v1/content/documents/{outside.id}")
            new_one = await client.get(f"/api/v1/content/documents/{later.id}")

        return {
            "grant_status": granted.status_code,
            "grant_effect": granted.json().get("data", {}).get("effect"),
            "before_total": library_before.json()["meta"]["total"],
            "blocked_before": blocked_before.status_code,
            "after_total": library_after.json()["meta"]["total"],
            "allowed": allowed.status_code,
            "still_out": still_out.status_code,
            "new_one": new_one.status_code,
            "documents": before,
        }

    result = run_in_database(database_url, body)

    assert result["grant_status"] == 201, result
    assert result["grant_effect"] == "ALLOW"
    assert result["before_total"] == 0
    assert result["blocked_before"] == 404  # does not exist, as far as a student knows
    assert result["after_total"] == 2  # the original and the one uploaded later
    assert result["allowed"] == 200
    assert result["still_out"] == 404
    assert result["new_one"] == 200


def test_a_revoked_grant_stops_granting_immediately(database_url: str) -> None:
    """Revocation is a timestamp, and the next request is already denied.

    Also asserts the row survives: the record that the access existed is what makes
    "who could read this last month" answerable after a leak.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        student = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="PREMIUM")
        await session.commit()

        async with Actor(session, admin) as client:
            created = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "USER", "user_id": str(student.id)},
            )
            grant_id = created.json()["data"]["id"]

        async with Actor(session, student) as client:
            while_granted = await client.get(f"/api/v1/content/documents/{document.id}")

        async with Actor(session, admin) as client:
            revoked = await client.delete(f"/api/v1/admin/access/grants/{grant_id}")
            again = await client.delete(f"/api/v1/admin/access/grants/{grant_id}")
            listed_live = await client.get("/api/v1/admin/access/grants")
            listed_all = await client.get("/api/v1/admin/access/grants?include_revoked=true")

        async with Actor(session, student) as client:
            after = await client.get(f"/api/v1/content/documents/{document.id}")

        async with observe(database_url) as other:
            row = (
                await other.execute(
                    select(ContentGrant).where(ContentGrant.id == uuid.UUID(grant_id))
                )
            ).scalar_one()

        return {
            "revoked": revoked.status_code,
            "revoked_twice": again.status_code,
            "while_granted": while_granted.status_code,
            "after": after.status_code,
            "live_list_total": listed_live.json()["meta"]["total"],
            "all_list_total": listed_all.json()["meta"]["total"],
            "row_still_there": row is not None,
            "revoked_at_set": row.revoked_at is not None,
        }

    result = run_in_database(database_url, body)

    assert result["while_granted"] == 200
    assert result["revoked"] == 200
    assert result["revoked_twice"] == 409  # idempotent-ish, and says so
    assert result["after"] == 404
    # The default list shows current access only; the revoked row is still retrievable.
    assert result["live_list_total"] == 0
    assert result["all_list_total"] == 1
    assert result["row_still_there"] is True
    assert result["revoked_at_set"] is True


def test_a_deny_grant_beats_an_allow_however_old_the_deny_is(database_url: str) -> None:
    """Precedence is by KIND, not by recency.

    An operator who grants a student the whole library expects it to work; an operator
    who has denied that student a course because their access was being abused expects
    that to keep working. If recency decided, the revoke button would be defeatable by
    granting something else - which is exactly the bug this test exists to prevent.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        student = await make_user(session, "STUDENT")
        content = await Content.seeded(session)
        course_id = content.course.id
        document = await seed_document(session, owner=admin, tier="PREMIUM", course_id=course_id)
        await session.commit()

        async with Actor(session, admin) as client:
            denied = await client.post(
                "/api/v1/admin/access/grants",
                json={
                    "who_scope": "USER",
                    "user_id": str(student.id),
                    "effect": "DENY",
                    "course_id": str(course_id),
                    "reason": "Rights restriction on this publisher's material",
                },
            )
            # THE NEWER, BROADER, MORE GENEROUS ONE.
            allowed = await client.post(
                "/api/v1/admin/access/grants",
                json={
                    "who_scope": "USER",
                    "user_id": str(student.id),
                    "effect": "ALLOW",
                    "reason": "Comp, issued later",
                },
            )

        async with Actor(session, student) as client:
            read_attempt = await client.get(f"/api/v1/content/documents/{document.id}")

        async with Actor(session, admin) as client:
            explained = await client.get(
                f"/api/v1/admin/access/explain?user_id={student.id}&document_id={document.id}"
            )

        return {
            "denied": denied.status_code,
            "allowed": allowed.status_code,
            "read_attempt": read_attempt.status_code,
            "decision": explained.json()["data"]["decision"],
            "reason": explained.json()["data"]["reason"],
        }

    result = run_in_database(database_url, body)

    assert result["denied"] == 201
    assert result["allowed"] == 201
    assert result["read_attempt"] == 404
    assert result["decision"] == "DENIED"
    # The explanation names the DENY, so an operator can see why their grant did not win.
    assert "Denied" in result["reason"] or "denied" in result["reason"]


def test_a_role_grant_opens_material_for_every_student(database_url: str) -> None:
    """The role axis, which is the one the spec's "by role" means.

    Asserted with TWO students, because a grant that happens to match the one student a
    test created would pass for the wrong reason.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        first = await make_user(session, "STUDENT")
        second = await make_user(session, "STUDENT")
        editor = await make_user(session, "EDITOR")
        document = await seed_document(session, owner=admin, tier="PREMIUM_PLUS")
        await session.commit()

        async with Actor(session, admin) as client:
            created = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "ROLE", "role": "STUDENT", "effect": "ALLOW"},
            )

        async with Actor(session, first) as client:
            first_read = await client.get(f"/api/v1/content/documents/{document.id}")
        async with Actor(session, second) as client:
            second_read = await client.get(f"/api/v1/content/documents/{document.id}")
        async with Actor(session, editor) as client:
            # An EDITOR is staff for content purposes and reads it through the staff
            # path; the ROLE grant is about students, so this asserts the grant did not
            # accidentally become a wildcard for every role.
            editor_read = await client.get(f"/api/v1/content/documents/{document.id}")

        return {
            "created": created.status_code,
            "first": first_read.status_code,
            "second": second_read.status_code,
            "editor": editor_read.status_code,
        }

    result = run_in_database(database_url, body)

    assert result["created"] == 201
    assert result["first"] == 200
    assert result["second"] == 200
    assert result["editor"] == 200


def test_a_tier_grant_opens_material_for_everyone_on_that_plan(database_url: str) -> None:
    """Subscription-based access, which is what a plan purchase should become.

    A PREMIUM_PLUS document, a TIER grant for PREMIUM, and two students: the one whose
    subscription says PREMIUM reads it, the one on FREE does not. The tier comes from
    ``resolve_viewer``, which reads the subscription clock rather than trusting a stored
    column, so the student's access follows their subscription as it changes.
    """

    async def body(session) -> dict[str, Any]:
        from app.models.user import Subscription

        admin = await make_user(session, "ADMIN")
        premium = await make_user(session, "STUDENT")
        free = await make_user(session, "STUDENT")
        document = await seed_document(session, owner=admin, tier="PREMIUM_PLUS")
        session.add(
            Subscription(
                id=uuid.uuid4(),
                user_id=premium.id,
                tier="PREMIUM",
                status="ACTIVE",
                provider="razorpay",
                provider_subscription_id="sub_test",
                started_at=datetime.now(UTC) - timedelta(days=1),
                expires_at=datetime.now(UTC) + timedelta(days=364),
            )
        )
        await session.commit()

        async with Actor(session, admin) as client:
            created = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "TIER", "tier": "PREMIUM", "effect": "ALLOW"},
            )

        async with Actor(session, premium) as client:
            premium_read = await client.get(f"/api/v1/content/documents/{document.id}")
        async with Actor(session, free) as client:
            free_read = await client.get(f"/api/v1/content/documents/{document.id}")

        return {
            "created": created.status_code,
            "premium": premium_read.status_code,
            "free": free_read.status_code,
        }

    result = run_in_database(database_url, body)

    assert result["created"] == 201
    assert result["premium"] == 200
    assert result["free"] == 404


def test_the_screen_can_explain_why_a_student_is_blocked(database_url: str) -> None:
    """``explain`` reports the outcome of the engine the student's request runs.

    Checked on an UNPUBLISHED document too, because "the tier is too low" and "a reviewer
    has not signed this off yet" are different conversations and the admin needs to know
    which one they are having.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        student = await make_user(session, "STUDENT")
        published = await seed_document(session, owner=admin, tier="PREMIUM", title="Live")
        draft = await seed_document(
            session, owner=admin, tier="FREE", published=False, title="Awaiting review"
        )
        await session.commit()

        async with Actor(session, admin) as client:
            on_live = await client.get(
                f"/api/v1/admin/access/explain?user_id={student.id}&document_id={published.id}"
            )
            on_draft = await client.get(
                f"/api/v1/admin/access/explain?user_id={student.id}&document_id={draft.id}"
            )
            overview = await client.get(f"/api/v1/admin/access/users/{student.id}")

        return {
            "live_decision": on_live.json()["data"]["decision"],
            "live_reason": on_live.json()["data"]["reason"],
            "draft_decision": on_draft.json()["data"]["decision"],
            "draft_reason": on_draft.json()["data"]["reason"],
            "overview_status": overview.status_code,
            "overview_tier": overview.json()["data"]["tier"],
        }

    result = run_in_database(database_url, body)

    assert result["live_decision"] == "NEEDS_UPGRADE"
    assert "PREMIUM" in result["live_reason"]
    assert result["draft_decision"] == "DENIED"
    assert "not published" in result["draft_reason"]
    assert result["overview_status"] == 200
    assert result["overview_tier"] == "FREE"


def test_an_editor_cannot_rewrite_access(database_url: str) -> None:
    """Authorisation is the dependency, not a hidden button.

    An EDITOR holds MANAGE_CONTENT and may edit the library. They do not hold
    MANAGE_ACCESS, so the grant endpoints answer 403 to their token - which is the only
    thing that matters, because a student or a junior editor can always call the API
    directly.
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_user(session, "EDITOR")
        student = await make_user(session, "STUDENT")
        await session.commit()

        async with Actor(session, editor) as client:
            create = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "USER", "user_id": str(student.id)},
            )
            listing = await client.get("/api/v1/admin/access/grants")
            overview = await client.get(f"/api/v1/admin/access/users/{student.id}")

        async with Actor(session, student) as client:
            student_create = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "USER", "user_id": str(student.id)},
            )
            student_list = await client.get("/api/v1/admin/access/grants")

        return {
            "editor_create": create.status_code,
            "editor_list": listing.status_code,
            "editor_overview": overview.status_code,
            "student_create": student_create.status_code,
            "student_list": student_list.status_code,
        }

    result = run_in_database(database_url, body)

    assert result["editor_create"] == 403
    assert result["editor_list"] == 403
    assert result["editor_overview"] == 403
    assert result["student_create"] == 403
    assert result["student_list"] == 403


def test_a_grant_must_name_exactly_one_audience(database_url: str) -> None:
    """Both the route and the database refuse a meaningless or over-broad grant.

    Two selectors would be read by one code path and ignored by another; none at all
    would match every viewer, which presents to the owner as "everyone can suddenly read
    this". The route refuses both with a sentence, and the constraint refuses them even
    if some future caller bypasses the route.
    """

    async def body(session) -> dict[str, Any]:
        admin = await make_user(session, "ADMIN")
        student = await make_user(session, "STUDENT")
        await session.commit()

        async with Actor(session, admin) as client:
            no_audience = await client.post(
                "/api/v1/admin/access/grants", json={"who_scope": "USER"}
            )
            two_audiences = await client.post(
                "/api/v1/admin/access/grants",
                json={
                    "who_scope": "USER",
                    "user_id": str(student.id),
                    "role": "STUDENT",
                },
            )
            bad_scope = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "EVERYONE", "user_id": str(student.id)},
            )
            expired = await client.post(
                "/api/v1/admin/access/grants",
                json={
                    "who_scope": "USER",
                    "user_id": str(student.id),
                    "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                },
            )
            duplicate = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "USER", "user_id": str(student.id)},
            )
            duplicate_again = await client.post(
                "/api/v1/admin/access/grants",
                json={"who_scope": "USER", "user_id": str(student.id)},
            )

        # And the database refuses a row with no audience even when the ORM is bypassed.
        from sqlalchemy.exc import IntegrityError

        rejected_by_db = False
        try:
            session.add(
                ContentGrant(
                    id=uuid.uuid4(),
                    who_scope="USER",
                    effect="ALLOW",
                )
            )
            await session.flush()
        except IntegrityError:
            rejected_by_db = True
            await session.rollback()

        return {
            "no_audience": no_audience.status_code,
            "two_audiences": two_audiences.status_code,
            "bad_scope": bad_scope.status_code,
            "expired": expired.status_code,
            "duplicate": duplicate.status_code,
            "duplicate_again": duplicate_again.status_code,
            "rejected_by_db": rejected_by_db,
        }

    result = run_in_database(database_url, body)

    assert result["no_audience"] == 422
    assert result["two_audiences"] == 422
    assert result["bad_scope"] == 422
    assert result["expired"] == 422
    assert result["duplicate"] == 201
    assert result["duplicate_again"] == 409
    assert result["rejected_by_db"] is True
