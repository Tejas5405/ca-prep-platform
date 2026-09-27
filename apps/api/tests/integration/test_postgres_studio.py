"""The remaining console, against PostgreSQL.

A student must be refused by the server, not by a hidden button. A price screen
must quote the catalogue checkout charges. The assistant must quote a document
the student may read and must not quote one they may not, and it must not invent
an answer when the provider key is absent.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.models.content import ContentDocument
from app.models.engagement import PlatformSetting
from app.models.question import Question
from app.seed import seed_curriculum
from app.services.platform_defaults import ensure_platform_defaults

from ._db import run_in_database
from .test_postgres_publishing import Actor, make_user

pytestmark = pytest.mark.postgres

FREE_PHRASE = "zyxfreeleaseword"
SECRET_PHRASE = "zyxpremiumsecretphrase"
ANSWER_KEY = "SECRETKEY42"


async def _spine(session: Any) -> tuple[Any, Any]:
    await seed_curriculum(session)
    await ensure_platform_defaults(session)
    await session.commit()
    from app.models.curriculum import Course, Subject

    course = (await session.execute(select(Course).order_by(Course.code))).scalars().first()
    subject = (
        await session.execute(select(Subject).where(Subject.course_id == course.id).limit(1))
    ).scalar_one()
    return course, subject


async def _page(
    session: Any, owner: Any, *, tier: str, phrase: str, published: bool = True
) -> None:
    from app.models.content import DocumentPage

    document = ContentDocument(
        id=uuid.uuid4(),
        title=f"{tier} notes",
        kind="STUDY_MATERIAL",
        bucket="question-pdfs",
        storage_path=f"originals/2026/09/{uuid.uuid4().hex}/notes.pdf",
        original_filename="notes.pdf",
        size_bytes=128,
        checksum_sha256=uuid.uuid4().hex * 2,
        status="COMPLETED",
        access_tier=tier,
        is_published=published,
        uploaded_by=owner.id,
        page_count=1,
        extracted_chars=len(phrase),
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentPage(
            id=uuid.uuid4(),
            document_id=document.id,
            page_number=1,
            text=f"The paragraph contains {phrase} for the search.",
            raw_text=phrase,
            char_count=len(phrase),
            extraction_tier="PYMUPDF",
        )
    )
    await session.commit()


def test_a_student_cannot_list_the_question_bank(database_url: str) -> None:
    async def body(session) -> int:
        await _spine(session)
        student = await make_user(session, "STUDENT")
        async with Actor(session, student) as client:
            response = await client.get("/api/v1/admin/questions")
        return response.status_code

    assert run_in_database(database_url, body) == 403


def test_an_editor_can_draft_a_question_and_cannot_skip_the_verifier(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        course, subject = await _spine(session)
        editor = await make_user(session, "EDITOR")
        async with Actor(session, editor) as client:
            missing_answer = await client.post(
                "/api/v1/admin/questions",
                json={
                    "course_id": str(course.id),
                    "subject_id": str(subject.id),
                    "text": "What is the rate?",
                    "question_type": "MCQ",
                },
            )
            historical = await client.post(
                "/api/v1/admin/questions",
                json={
                    "course_id": str(course.id),
                    "subject_id": str(subject.id),
                    "text": "Old law question",
                    "question_type": "DESCRIPTIVE",
                    "is_historical": True,
                },
            )
            created = await client.post(
                "/api/v1/admin/questions",
                json={
                    "course_id": str(course.id),
                    "subject_id": str(subject.id),
                    "text": "A draft about leases",
                    "question_type": "MCQ",
                    "correct_answer": "B",
                    "options": [
                        {"label": "A", "text": "Old rate"},
                        {"label": "B", "text": "Current rate"},
                    ],
                },
            )
            skipped = await client.patch(
                f"/api/v1/admin/questions/{created.json()['data']['id']}",
                json={"status": "PUBLISHED"},
            )
        return {
            "missing": missing_answer.status_code,
            "historical": historical.status_code,
            "created": created.status_code,
            "status": created.json()["data"]["status"],
            "skipped": skipped.status_code,
        }

    result = run_in_database(database_url, body)
    assert result == {
        "missing": 422,
        "historical": 422,
        "created": 201,
        "status": "DRAFT",
        "skipped": 422,
    }


def test_plans_are_the_code_catalogue_and_storage_does_not_invent_usage(
    database_url: str,
) -> None:
    async def body(session) -> dict[str, Any]:
        await ensure_platform_defaults(session)
        await session.commit()
        admin = await make_user(session, "SUPER_ADMIN")
        async with Actor(session, admin) as client:
            plans = await client.get("/api/v1/admin/plans")
            storage = await client.get("/api/v1/admin/storage")
            ai = await client.get("/api/v1/admin/ai")
        return {
            "plans": plans.json()["data"],
            "storage": storage.json()["data"],
            "ai": ai.json()["data"],
        }

    result = run_in_database(database_url, body)
    plus = next(plan for plan in result["plans"]["plans"] if plan["tier"] == "PREMIUM_PLUS")
    assert result["plans"]["editable"] is True
    assert plus["amountPaise"] == 129_900
    assert plus["amountRupees"] == 1299
    assert result["storage"]["objects"] is None
    assert set(result["storage"]["missingEnv"]) <= {"SUPABASE_URL", "SUPABASE_SECRET_KEY"}
    assert result["ai"]["generatesAnswers"] is False
    assert result["ai"]["groundingOptional"] is False
    assert (
        result["ai"]["missingEnv"] == ["AI_PROVIDER_API_KEY"] or result["ai"]["providerConfigured"]
    )


def test_a_revision_does_not_rewrite_the_key_a_student_was_marked_on(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        course, subject = await _spine(session)
        manager = await make_user(session, "CONTENT_MANAGER")
        student = await make_user(session, "STUDENT")
        async with Actor(session, manager) as client:
            created = await client.post(
                "/api/v1/admin/questions",
                json={
                    "course_id": str(course.id),
                    "subject_id": str(subject.id),
                    "text": "What was the rate?",
                    "question_type": "MCQ",
                    "correct_answer": "A",
                    "options": [
                        {"label": "A", "text": "Ten"},
                        {"label": "B", "text": "Twenty"},
                    ],
                },
            )
            question_id = created.json()["data"]["id"]
            published = await client.post(f"/api/v1/admin/questions/{question_id}/publish")
        async with Actor(session, student) as client:
            answered = await client.post(
                "/api/v1/practice/answers",
                json={"question_id": question_id, "chosen_option": "A"},
            )
        async with Actor(session, manager) as client:
            revised = await client.post(
                f"/api/v1/admin/questions/{question_id}/revise",
                json={
                    "change_reason": "Finance Act changed the rate",
                    "correct_answer": "B",
                    "text": "What is the rate now?",
                },
            )
            versions = await client.get(f"/api/v1/admin/questions/{question_id}/versions")
        async with Actor(session, student) as client:
            detail = await client.get(f"/api/v1/questions/{question_id}")
        return {
            "published": published.status_code,
            "answered": answered.status_code,
            "was_correct": answered.json().get("data", {}).get("isCorrect"),
            "revised": revised.status_code,
            "live_key": revised.json().get("data", {}).get("correctAnswer"),
            "versions": [item["version"] for item in versions.json()["data"]["versions"]],
            "shown_key": detail.json().get("data", {}).get("reveal", {}).get("correctAnswer"),
            "detail_status": detail.status_code,
        }

    result = run_in_database(database_url, body)
    assert result["published"] == 200
    assert result["answered"] == 200
    assert result["was_correct"] is True
    assert result["revised"] == 200
    assert result["live_key"] == "B"
    assert result["detail_status"] == 200
    assert result["shown_key"] == "A"
    assert 1 in result["versions"]


def test_the_assistant_quotes_only_what_the_student_may_read(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        course, subject = await _spine(session)
        owner = await make_user(session, "ADMIN")
        student = await make_user(session, "STUDENT")
        await _page(session, owner, tier="FREE", phrase=FREE_PHRASE)
        await _page(session, owner, tier="PREMIUM", phrase=SECRET_PHRASE)
        session.add(
            Question(
                id=uuid.uuid4(),
                course_id=course.id,
                subject_id=subject.id,
                text=f"Published stem mentions {FREE_PHRASE} without the key.",
                question_type="MCQ",
                correct_answer=ANSWER_KEY,
                status="PUBLISHED",
                verified_by=owner.id,
                created_by=owner.id,
            )
        )
        session.add(
            Question(
                id=uuid.uuid4(),
                course_id=course.id,
                subject_id=subject.id,
                text=f"Draft stem mentions {FREE_PHRASE} and must stay hidden.",
                question_type="MCQ",
                correct_answer="A",
                status="DRAFT",
                created_by=owner.id,
            )
        )
        await session.commit()

        async with Actor(session, student) as client:
            off = await client.post("/api/v1/assistant/ask", json={"query": FREE_PHRASE})
            row = (
                await session.execute(
                    select(PlatformSetting).where(PlatformSetting.key == "features.ai_assistant")
                )
            ).scalar_one()
            row.value = {"value": True}
            await session.commit()
            on = await client.post("/api/v1/assistant/ask", json={"query": FREE_PHRASE})
        return {"off": off.status_code, "off_body": off.text, "on": on.json()}

    result = run_in_database(database_url, body)
    assert result["off"] == 403
    assert SECRET_PHRASE not in result["off_body"]
    data = result["on"]["data"]
    assert data["answer"] is None
    assert data["generatesAnswers"] is False
    raw = str(result["on"])
    assert FREE_PHRASE in raw
    assert SECRET_PHRASE not in raw
    assert ANSWER_KEY not in raw
    assert "must stay hidden" not in raw
