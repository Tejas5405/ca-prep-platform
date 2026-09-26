"""Collections and the LDR list, against a live PostgreSQL.

WHY THIS FILE EXISTS

`POST /collections` was the last named route in blueprint §7.3 with nothing behind
it, and the failure mode for that kind of gap is specific: the endpoints exist,
answer 200, and the row a student cares about is not there. So every assertion here
reads the DATABASE, on a second connection, after the request finished — the same
standard the publishing and ingestion suites are held to.

The four properties worth a test rather than a code review:

  * ownership is a predicate in the query, so one student's collection is a 404 for
    another — asserted in both directions, because a test that only checks the
    owner can pass while the check is missing.
  * adding a DRAFT question is refused, because a collection is a reading surface
    and the publishing rules have to hold there too.
  * adding the same question twice does not error and reports what actually
    changed, because "12 added" when three were already present is a lie.
  * the LDR list is the practice loop's flag, not a second copy of it — flagging a
    question through the practice route makes it appear here, and nothing in this
    module writes that flag itself.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.models.collection import Collection, CollectionQuestion

from ._db import observe, run_in_database
from .test_postgres_publishing import Actor, Content, make_question, make_user

pytestmark = pytest.mark.postgres

COLLECTIONS = "/api/v1/collections"
LDR = "/api/v1/ldr"


async def answer_question(client: Any, question_id: Any, label: str = "A") -> None:
    """Answer, because the flag lives on the progress row an answer creates.

    `POST /practice/questions/{id}/bookmark` refuses with 409 until there is a
    progress row, which is deliberate: the LDR list is "questions I have met and
    want to see again", and a flag with no attempt behind it would put a question
    nobody has seen into a revision list. Every test below has to answer first, and
    that is the feature working rather than the test working around it.
    """
    response = await client.post(
        "/api/v1/practice/answers",
        json={"question_id": str(question_id), "chosen_option": label, "time_spent_seconds": 40},
    )
    assert response.status_code == 200, response.text


async def make_collection(client: Any, name: str, **extra: Any) -> dict[str, Any]:
    response = await client.post(COLLECTIONS, json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --------------------------------------------------------------------- creation


def test_a_collection_is_created_and_read_back(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            created = await make_collection(client, "Costing weak spots")
            listed = await client.get(COLLECTIONS)

        async with observe(database_url) as other:
            row = await other.get(Collection, uuid.UUID(created["id"]))

        return {
            "status": listed.status_code,
            "created": created,
            "row_name": row.name if row else None,
            "row_kind": row.kind if row else None,
            "row_owner": str(row.user_id) if row else None,
            "owner": str(student.id),
            "listed_ids": [item["id"] for item in listed.json()["data"]],
        }

    result = run_in_database(database_url, body)
    # THE ROW, on a second connection: a 201 whose write was never committed would
    # still pass an HTTP-only assertion.
    assert result["row_name"] == "Costing weak spots"
    assert result["row_kind"] == "MANUAL"
    assert result["row_owner"] == result["owner"]
    assert result["created"]["questionCount"] == 0
    assert result["created"]["id"] in result["listed_ids"]


def test_a_second_collection_with_the_same_name_is_a_conflict(database_url: str) -> None:
    """Two collections called "revision" is the state the feature exists to avoid."""

    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            first = await client.post(COLLECTIONS, json={"name": "Revision before mock 3"})
            second = await client.post(COLLECTIONS, json={"name": "Revision before mock 3"})
            listed = await client.get(COLLECTIONS)

        return {
            "first": first.status_code,
            "second": second.status_code,
            "detail": second.json().get("detail"),
            "count": len(listed.json()["data"]),
        }

    result = run_in_database(database_url, body)
    assert result["first"] == 201
    assert result["second"] == 409
    assert "already have a collection" in result["detail"]
    # And the failed attempt did not leave a second row behind.
    assert result["count"] == 1


def test_a_smart_collection_stores_validated_filters(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            created = await make_collection(
                client,
                "Hard costing questions",
                kind="SMART",
                filters={"difficulty": "HARD", "syllabus_scheme": "NEW_2024"},
            )

        async with observe(database_url) as other:
            row = await other.get(Collection, uuid.UUID(created["id"]))

        return {"created": created, "filters": row.filters if row else None}

    result = run_in_database(database_url, body)
    assert result["created"]["kind"] == "SMART"
    # Stored as the validated predicate, not as the raw JSON the client sent.
    assert result["filters"] == {"difficulty": "HARD", "syllabus_scheme": "NEW_2024"}


def test_an_unknown_filter_field_is_refused(database_url: str) -> None:
    """The filter blob is a security boundary, not a config string.

    Raw client JSON reaching the query builder is a data-exfiltration vector, which
    is why `CollectionFilters` forbids extras. The route must turn that refusal into
    a 422 rather than a 500 - and must not store the collection either.
    """

    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            response = await client.post(
                COLLECTIONS,
                json={
                    "name": "Sneaky",
                    "kind": "SMART",
                    "filters": {"difficulty": "HARD", "password_hash": "x"},
                },
            )
            listed = await client.get(COLLECTIONS)
        return {
            "status": response.status_code,
            "body": response.json(),
            "count": len(listed.json()["data"]),
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 422
    assert result["body"]["type"].endswith("/collections")
    assert result["count"] == 0


def test_filters_on_a_manual_collection_are_refused(database_url: str) -> None:
    """Refused rather than ignored: a manual collection with filters would look
    like it filtered, and would filter nothing."""

    async def body(session) -> dict[str, Any]:
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            response = await client.post(
                COLLECTIONS, json={"name": "Manual", "filters": {"difficulty": "HARD"}}
            )
        return {"status": response.status_code, "title": response.json().get("title")}

    result = run_in_database(database_url, body)
    assert result["status"] == 422
    assert result["title"] == "Filters on a manual collection"


# ---------------------------------------------------------------------- contents


def test_added_questions_are_stored_and_served_without_the_answer(database_url: str) -> None:
    """A collection is a reading list, not an answer key."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            collection = await make_collection(client, "Before mock 3")
            added = await client.post(
                f"{COLLECTIONS}/{collection['id']}/questions",
                json={"question_ids": [str(question.id)], "note": "lost marks on presentation"},
            )
            detail = await client.get(f"{COLLECTIONS}/{collection['id']}")

        async with observe(database_url) as other:
            links = (await other.execute(select(CollectionQuestion))).scalars().all()

        return {
            "added": added.json()["data"],
            "detail": detail.json()["data"],
            "link_count": len(links),
            "note": links[0].note if links else None,
            "raw_text": detail.text,
            "question_id": str(question.id),
        }

    result = run_in_database(database_url, body)
    assert result["added"]["added"] == 1
    assert result["link_count"] == 1
    assert result["note"] == "lost marks on presentation"

    payload = result["detail"]
    assert payload["questionId"] == result["question_id"] if "questionId" in payload else True
    assert payload["count"] == 1
    assert payload["questions"][0]["questionId"] == result["question_id"]
    assert payload["questions"][0]["note"] == "lost marks on presentation"
    assert [option["label"] for option in payload["questions"][0]["options"]] == [
        "A",
        "B",
        "C",
        "D",
    ]
    # The answer is absent from the response TEXT, not merely from the fields the
    # test thought to check.
    for forbidden in ("correctAnswer", "explanation", "isCorrect", "modelAnswer"):
        assert forbidden not in result["raw_text"], f"{forbidden} leaked through a collection"


def test_adding_a_draft_question_is_refused(database_url: str) -> None:
    """The publishing rules have to hold on every reading surface.

    A draft id smuggled into a collection would otherwise be readable through the
    collection screen - the same leak `_published()` prevents everywhere else.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        draft = await make_question(session, content, status="DRAFT")

        async with Actor(session, student) as client:
            collection = await make_collection(client, "Sneaky list")
            response = await client.post(
                f"{COLLECTIONS}/{collection['id']}/questions",
                json={"question_ids": [str(draft.id)]},
            )
            detail = await client.get(f"{COLLECTIONS}/{collection['id']}")

        async with observe(database_url) as other:
            links = (await other.execute(select(CollectionQuestion))).scalars().all()

        return {
            "status": response.status_code,
            "data": response.json()["data"],
            "rows": len(links),
            "served": detail.json()["data"]["count"],
        }

    result = run_in_database(database_url, body)
    # No error: the request is well-formed, it simply has nothing valid to add. What
    # matters is that nothing was written and nothing is served.
    assert result["status"] == 200
    assert result["data"]["added"] == 0
    assert result["rows"] == 0
    assert result["served"] == 0


def test_adding_the_same_question_twice_reports_what_actually_changed(database_url: str) -> None:
    """Idempotent, and honest about it.

    A double-tap on a slow connection must not error, and the count that comes back
    must be the number ADDED rather than the number requested.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            collection = await make_collection(client, "Repeat")
            first = await client.post(
                f"{COLLECTIONS}/{collection['id']}/questions",
                json={"question_ids": [str(question.id)]},
            )
            second = await client.post(
                f"{COLLECTIONS}/{collection['id']}/questions",
                json={"question_ids": [str(question.id)]},
            )
            listed = await client.get(COLLECTIONS)

        async with observe(database_url) as other:
            links = (await other.execute(select(CollectionQuestion))).scalars().all()

        return {
            "first": first.json()["data"],
            "second": second.json()["data"],
            "rows": len(links),
            "counted": listed.json()["data"][0]["questionCount"],
        }

    result = run_in_database(database_url, body)
    assert result["first"]["added"] == 1
    assert result["second"]["added"] == 0
    assert result["second"]["alreadyPresent"] == 1
    # One row, and the count a student sees matches the rows that exist.
    assert result["rows"] == 1
    assert result["counted"] == 1


def test_removing_a_question_leaves_the_question_alone(database_url: str) -> None:
    """Deleting a membership must not delete the question or the other memberships."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        keep = await make_question(session, content, status="PUBLISHED", verifier=student)
        drop = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            collection = await make_collection(client, "Two questions")
            await client.post(
                f"{COLLECTIONS}/{collection['id']}/questions",
                json={"question_ids": [str(keep.id), str(drop.id)]},
            )
            removed = await client.delete(f"{COLLECTIONS}/{collection['id']}/questions/{drop.id}")
            listed = await client.get(COLLECTIONS)
            detail = await client.get(f"{COLLECTIONS}/{collection['id']}")

        async with observe(database_url) as other:
            links = (await other.execute(select(CollectionQuestion))).scalars().all()
            from app.models.question import Question

            question = await other.get(Question, drop.id)

        return {
            "removed": removed.status_code,
            "rows": len(links),
            "remaining": detail.json()["data"]["questions"][0]["questionId"],
            "keep": str(keep.id),
            "question_still_there": question is not None,
            "counted": listed.json()["data"][0]["questionCount"],
        }

    result = run_in_database(database_url, body)
    assert result["removed"] == 200
    assert result["rows"] == 1
    assert result["remaining"] == result["keep"]
    assert result["question_still_there"] is True
    assert result["counted"] == 1


# ---------------------------------------------------------------------- ownership


def test_another_students_collection_is_a_404_in_both_directions(database_url: str) -> None:
    """Ownership is a predicate in the query, not a comparison afterwards.

    Asserted from both sides: a test that only checks the owner can pass while the
    check is entirely absent, because the owner's request succeeds either way.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        owner = await make_user(session, "STUDENT")
        stranger = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=owner)
        # Plain ids, not ORM instances. Every refused request below rolls its
        # transaction back, and a rollback expires every instance in this shared
        # session - so `question.id` a few lines down would issue a lazy load, which
        # in an async context raises MissingGreenlet rather than returning an id.
        question_id = str(question.id)

        async with Actor(session, owner) as client:
            collection = await make_collection(client, "Mine")
            collection_id = collection["id"]
            await client.post(
                f"{COLLECTIONS}/{collection_id}/questions",
                json={"question_ids": [question_id]},
            )

        async with Actor(session, stranger) as client:
            read = await client.get(f"{COLLECTIONS}/{collection_id}")
            rename = await client.patch(f"{COLLECTIONS}/{collection_id}", json={"name": "Hijacked"})
            add = await client.post(
                f"{COLLECTIONS}/{collection_id}/questions",
                json={"question_ids": [question_id]},
            )
            remove = await client.delete(f"{COLLECTIONS}/{collection_id}/questions/{question_id}")
            drop = await client.delete(f"{COLLECTIONS}/{collection_id}")
            stranger_list = await client.get(COLLECTIONS)

        async with Actor(session, owner) as client:
            owner_read = await client.get(f"{COLLECTIONS}/{collection_id}")

        return {
            "read": read.status_code,
            "rename": rename.status_code,
            "add": add.status_code,
            "remove": remove.status_code,
            "delete": drop.status_code,
            "stranger_count": len(stranger_list.json()["data"]),
            "owner_name": owner_read.json()["data"]["name"],
            "owner_questions": owner_read.json()["data"]["count"],
        }

    result = run_in_database(database_url, body)
    # Every write path refuses, and refuses as NOT FOUND rather than forbidden: a
    # 403 would confirm that the id exists.
    for key in ("read", "rename", "add", "remove", "delete"):
        assert result[key] == 404, f"{key} returned {result[key]}"
    assert result["stranger_count"] == 0
    # And nothing the owner built was changed by any of it.
    assert result["owner_name"] == "Mine"
    assert result["owner_questions"] == 1


def test_deleting_a_collection_takes_its_memberships_and_nothing_else(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            collection = await make_collection(client, "Doomed")
            await client.post(
                f"{COLLECTIONS}/{collection['id']}/questions",
                json={"question_ids": [str(question.id)]},
            )
            deleted = await client.delete(f"{COLLECTIONS}/{collection['id']}")
            missing = await client.get(f"{COLLECTIONS}/{collection['id']}")

        async with observe(database_url) as other:
            links = (await other.execute(select(CollectionQuestion))).scalars().all()
            from app.models.question import Question

            question_row = await other.get(Question, question.id)

        return {
            "deleted": deleted.status_code,
            "missing": missing.status_code,
            "links": len(links),
            "question_still_there": question_row is not None,
        }

    result = run_in_database(database_url, body)
    assert result["deleted"] == 200
    assert result["missing"] == 404
    # ON DELETE CASCADE on the membership rows only.
    assert result["links"] == 0
    assert result["question_still_there"] is True


def test_a_question_knows_which_of_your_collections_hold_it(database_url: str) -> None:
    """What "add to collection" needs in order to show its own state."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        other = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            first = await make_collection(client, "One")
            second = await make_collection(client, "Two")
            await client.post(
                f"{COLLECTIONS}/{first['id']}/questions", json={"question_ids": [str(question.id)]}
            )
            await client.post(
                f"{COLLECTIONS}/{second['id']}/questions", json={"question_ids": [str(question.id)]}
            )
            mine = await client.get(f"/api/v1/questions/{question.id}/collections")

        # Another student's membership must not appear in this list.
        async with Actor(session, other) as client:
            theirs = await client.get(f"/api/v1/questions/{question.id}/collections")

        return {
            "status": mine.status_code,
            "mine": sorted(mine.json()["data"]["collectionIds"]),
            "expected": sorted([first["id"], second["id"]]),
            "theirs": theirs.json()["data"]["collectionIds"],
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["mine"] == result["expected"]
    assert result["theirs"] == []


# -------------------------------------------------------------------------- LDR


def test_the_ldr_list_is_the_practice_flags_flag_and_not_a_copy(database_url: str) -> None:
    """Flagging through the practice route is what puts a question here.

    There is no second write path, deliberately: two answers to "is this flagged"
    means the wrong one wins, depending on which route ran last.
    """

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            empty = await client.get(LDR)
            # The refusal comes FIRST, before any attempt exists.
            refused = await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": True}
            )
            await answer_question(client, question.id)
            flagged = await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": True}
            )
            listed = await client.get(LDR)

        from app.models.progress import UserQuestionProgress

        async with observe(database_url) as other:
            progress = (
                await other.execute(
                    select(UserQuestionProgress).where(
                        UserQuestionProgress.user_id == student.id,
                        UserQuestionProgress.question_id == question.id,
                    )
                )
            ).scalar_one()

        return {
            "empty_total": empty.json()["meta"]["total"],
            "refused": refused.status_code,
            "refused_detail": refused.json().get("detail"),
            "flag_status": flagged.status_code,
            "total": listed.json()["meta"]["total"],
            "ids": [row["questionId"] for row in listed.json()["data"]],
            "question_id": str(question.id),
            # The source of truth is the progress row the practice loop writes.
            "flag_in_db": progress.is_marked_for_review,
        }

    result = run_in_database(database_url, body)
    assert result["empty_total"] == 0
    # Flagging a question nobody has answered is refused, with a reason a student can
    # act on rather than a 500 or a silently empty list.
    assert result["refused"] == 409
    assert "Answer the question first" in result["refused_detail"]
    assert result["flag_status"] == 200
    assert result["total"] == 1
    assert result["question_id"] in result["ids"]
    assert result["flag_in_db"] is True


def test_unflagging_removes_it_from_the_ldr_list(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            await answer_question(client, question.id)
            await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": True}
            )
            before = await client.get(LDR)
            await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": False}
            )
            after = await client.get(LDR)

        return {"before": before.json()["meta"]["total"], "after": after.json()["meta"]["total"]}

    result = run_in_database(database_url, body)
    assert result["before"] == 1
    assert result["after"] == 0


def test_the_ldr_list_never_shows_an_unpublished_question(database_url: str) -> None:
    """A question archived since it was flagged disappears rather than appearing
    unanswerable."""

    async def body(session) -> dict[str, Any]:
        from datetime import UTC, datetime

        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=student)

        async with Actor(session, student) as client:
            await answer_question(client, question.id)
            await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": True}
            )
            before = await client.get(LDR)

            question.status = "ARCHIVED"
            await session.commit()

            after = await client.get(LDR)

        return {
            "before": before.json()["meta"]["total"],
            "after": after.json()["meta"]["total"],
            "archived_at": datetime.now(UTC).isoformat(),
        }

    result = run_in_database(database_url, body)
    assert result["before"] == 1
    assert result["after"] == 0


def test_the_ldr_list_is_private_to_its_owner(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        owner = await make_user(session, "STUDENT")
        stranger = await make_user(session, "STUDENT")
        question = await make_question(session, content, status="PUBLISHED", verifier=owner)

        async with Actor(session, owner) as client:
            await answer_question(client, question.id)
            await client.post(
                f"/api/v1/practice/questions/{question.id}/bookmark", json={"marked": True}
            )
            mine = await client.get(LDR)

        async with Actor(session, stranger) as client:
            theirs = await client.get(LDR)

        return {"mine": mine.json()["meta"]["total"], "theirs": theirs.json()["meta"]["total"]}

    result = run_in_database(database_url, body)
    assert result["mine"] == 1
    assert result["theirs"] == 0


def test_pagination_reports_reality(database_url: str) -> None:
    """`total` is the filtered count, and `hasMore` follows from it."""

    async def body(session) -> dict[str, Any]:
        content = await Content.seeded(session)
        student = await make_user(session, "STUDENT")
        questions = [
            await make_question(session, content, status="PUBLISHED", verifier=student)
            for _ in range(3)
        ]

        async with Actor(session, student) as client:
            collection = await make_collection(client, "Three")
            await client.post(
                f"{COLLECTIONS}/{collection['id']}/questions",
                json={"question_ids": [str(q.id) for q in questions]},
            )
            page_one = await client.get(f"{COLLECTIONS}/{collection['id']}?page=1&limit=2")
            page_two = await client.get(f"{COLLECTIONS}/{collection['id']}?page=2&limit=2")

        return {"one": page_one.json()["data"], "two": page_two.json()["data"]}

    result = run_in_database(database_url, body)
    assert result["one"]["total"] == 3
    assert result["one"]["count"] == 2
    assert result["one"]["hasMore"] is True
    assert result["two"]["count"] == 1
    assert result["two"]["hasMore"] is False
    # Different rows, not the same page twice.
    assert (
        result["one"]["questions"][0]["questionId"] != result["two"]["questions"][0]["questionId"]
    )
