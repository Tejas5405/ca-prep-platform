"""The remainder that checkout and the Intermediate list actually depend on.

A saved Razorpay secret must not come back in the response. A saved price must
be the price the catalogue shows. A student at Intermediate must not be offered
the combined papers, and a question that has not been classified must not appear
in both splits.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.models.curriculum import Chapter, Course, Subject, SubjectComponent
from app.models.question import Question
from app.seed import seed_curriculum

from ._db import run_in_database
from .test_postgres_publishing import Actor, make_user

pytestmark = pytest.mark.postgres


def test_intermediate_students_see_splits_and_not_the_combined_papers(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        await seed_curriculum(session)
        await session.commit()
        student = await make_user(session, "STUDENT")
        owner = await make_user(session, "SUPER_ADMIN")
        intermediate = (
            await session.execute(select(Course).where(Course.level == "INTERMEDIATE"))
        ).scalar_one()
        foundation = (
            await session.execute(select(Course).where(Course.level == "FOUNDATION"))
        ).scalar_one()
        async with Actor(session, student) as client:
            split = await client.get(f"/api/v1/curriculum/subjects?course_id={intermediate.id}")
            forced = await client.get(
                f"/api/v1/curriculum/subjects?course_id={intermediate.id}&include_parents=true"
            )
            foundation_list = await client.get(
                f"/api/v1/curriculum/subjects?course_id={foundation.id}"
            )
        async with Actor(session, owner) as client:
            staff = await client.get(
                f"/api/v1/curriculum/subjects?course_id={intermediate.id}&include_parents=true"
            )
        return {
            "student": [row["name"] for row in split.json()["data"]["subjects"]],
            "forced": [row["name"] for row in forced.json()["data"]["subjects"]],
            "foundation": [row["name"] for row in foundation_list.json()["data"]["subjects"]],
            "staff": [row["name"] for row in staff.json()["data"]["subjects"]],
        }

    result = run_in_database(database_url, body)
    assert "Direct Tax" in result["student"]
    assert "Indirect Tax" in result["student"]
    assert "Financial Management" in result["student"]
    assert "Strategic Management" in result["student"]
    assert "Taxation" not in result["student"]
    assert "Financial Management and Strategic Management" not in result["student"]
    assert "Taxation" not in result["forced"]
    assert "Taxation" in result["staff"]
    assert "Accounting" in result["foundation"]


def test_a_split_does_not_return_the_other_splits_questions(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        await seed_curriculum(session)
        await session.commit()
        student = await make_user(session, "STUDENT")
        tax = (await session.execute(select(Subject).where(Subject.code == "INT_TAX"))).scalar_one()
        gst = (
            await session.execute(select(Chapter).where(Chapter.code == "INT_TAX_03"))
        ).scalar_one()
        income = (
            await session.execute(select(Chapter).where(Chapter.code == "INT_TAX_01"))
        ).scalar_one()
        direct = (
            await session.execute(
                select(SubjectComponent).where(SubjectComponent.code == "DIRECT_TAX")
            )
        ).scalar_one()
        indirect = (
            await session.execute(
                select(SubjectComponent).where(SubjectComponent.code == "INDIRECT_TAX")
            )
        ).scalar_one()
        gst_question = Question(
            id=uuid.uuid4(),
            course_id=tax.course_id,
            subject_id=tax.id,
            chapter_id=gst.id,
            text="GST place of supply for this seeded chapter.",
            question_type="MCQ",
            correct_answer="B",
            status="PUBLISHED",
            verified_by=student.id,
        )
        loose = Question(
            id=uuid.uuid4(),
            course_id=tax.course_id,
            subject_id=tax.id,
            text="A taxation question with no chapter and no split.",
            question_type="MCQ",
            correct_answer="A",
            status="PUBLISHED",
            verified_by=student.id,
        )
        session.add(gst_question)
        session.add(loose)
        await session.commit()
        async with Actor(session, student) as client:
            indirect_set = await client.get(
                f"/api/v1/practice/questions?subject_id={indirect.id}&limit=20"
            )
            direct_set = await client.get(
                f"/api/v1/practice/questions?subject_id={direct.id}&limit=20"
            )
            parent_set = await client.get(
                f"/api/v1/practice/questions?subject_id={tax.id}&limit=20"
            )
            chapters = await client.get(f"/api/v1/curriculum/chapters?subject_id={indirect.id}")
        return {
            "indirect": [row["text"] for row in indirect_set.json()["data"]["questions"]],
            "direct": [row["text"] for row in direct_set.json()["data"]["questions"]],
            "parent": parent_set.json()["data"]["questions"],
            "chapters": [row["code"] for row in chapters.json()["data"]["chapters"]],
            "income_component": str(income.subject_component_id),
            "direct_id": str(direct.id),
        }

    result = run_in_database(database_url, body)
    assert any("GST place of supply" in text for text in result["indirect"])
    assert not any("GST place of supply" in text for text in result["direct"])
    assert not any("no chapter" in text for text in result["indirect"])
    assert not any("no chapter" in text for text in result["direct"])
    assert result["parent"] == []
    assert "INT_TAX_03" in result["chapters"]
    assert "INT_TAX_01" not in result["chapters"]
    assert result["income_component"] == result["direct_id"]


def test_a_saved_price_is_the_price_the_catalogue_shows(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        owner = await make_user(session, "SUPER_ADMIN")
        async with Actor(session, owner) as client:
            before = await client.get("/api/v1/payments/plans")
            saved = await client.put(
                "/api/v1/admin/plans",
                json={
                    "code": "PREMIUM_PLUS_YEARLY",
                    "amount_paise": 149_900,
                    "duration_days": 365,
                },
            )
            after = await client.get("/api/v1/payments/plans")
            admin = await client.get("/api/v1/admin/plans")
        plus_before = next(
            plan for plan in before.json()["data"]["plans"] if plan["code"] == "PREMIUM_PLUS_YEARLY"
        )
        plus_after = next(
            plan for plan in after.json()["data"]["plans"] if plan["code"] == "PREMIUM_PLUS_YEARLY"
        )
        plus_admin = next(
            plan for plan in admin.json()["data"]["plans"] if plan["code"] == "PREMIUM_PLUS_YEARLY"
        )
        return {
            "before": plus_before["amountPaise"],
            "saved": saved.status_code,
            "after": plus_after["amountPaise"],
            "admin": plus_admin["amountPaise"],
            "editable": admin.json()["data"]["editable"],
        }

    result = run_in_database(database_url, body)
    assert result["before"] == 129_900
    assert result["saved"] == 200
    assert result["after"] == 149_900
    assert result["admin"] == 149_900
    assert result["editable"] is True


def test_a_saved_razorpay_secret_is_not_returned(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        owner = await make_user(session, "SUPER_ADMIN")
        student = await make_user(session, "STUDENT")
        secret = "rzp_test_secret_value_not_for_the_browser"
        async with Actor(session, owner) as client:
            saved = await client.put(
                "/api/v1/admin/payments/gateway",
                json={
                    "key_id": "rzp_test_keyidvalue",
                    "key_secret": secret,
                    "webhook_secret": "whsec_test_value_long_enough",
                },
            )
            read = await client.get("/api/v1/admin/payments/gateway")
        async with Actor(session, student) as client:
            refused = await client.put(
                "/api/v1/admin/payments/gateway",
                json={"key_id": "rzp_test_studentshouldnot"},
            )
        return {
            "status": saved.status_code,
            "body": saved.text,
            "read": read.text,
            "ready": saved.json()["data"]["checkoutReady"],
            "student": refused.status_code,
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["ready"] is True
    assert "rzp_test_secret_value_not_for_the_browser" not in result["body"]
    assert "rzp_test_secret_value_not_for_the_browser" not in result["read"]
    assert "whsec_test_value_long_enough" not in result["body"]
    assert result["student"] == 403


def test_a_law_notice_flags_a_match_and_does_not_change_the_answer(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        await seed_curriculum(session)
        await session.commit()
        editor = await make_user(session, "CONTENT_MANAGER")
        subject = (await session.execute(select(Subject).limit(1))).scalar_one()
        citation = "section 80C of the Finance Act"
        question = Question(
            id=uuid.uuid4(),
            course_id=subject.course_id,
            subject_id=subject.id,
            text=f"What does {citation} allow as a deduction?",
            question_type="MCQ",
            correct_answer="A",
            status="PUBLISHED",
            verified_by=editor.id,
        )
        session.add(question)
        await session.commit()
        async with Actor(session, editor) as client:
            recorded = await client.post(
                "/api/v1/admin/law-notices",
                json={"citation": citation, "summary": "Review this citation. Do not rewrite it."},
            )
        await session.refresh(question)
        data = recorded.json()["data"] if recorded.status_code == 200 else {}
        return {
            "status": recorded.status_code,
            "matched": data.get("matched", recorded.text),
            "changed": data.get("answersChanged"),
            "answer": question.correct_answer,
        }

    result = run_in_database(database_url, body)
    assert result["status"] == 200
    assert result["matched"] == 1
    assert result["changed"] is False
    assert result["answer"] == "A"
