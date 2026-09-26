"""Study tools record facts. They do not grant Premium, send email, or rewrite answers."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.models.question import Question, QuestionFlag
from app.seed import seed_curriculum
from app.services.platform_defaults import ensure_platform_defaults

from ._db import run_in_database
from .test_postgres_publishing import Actor, make_user

pytestmark = pytest.mark.postgres


def test_referral_support_and_review_do_not_invent_outcomes(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        await seed_curriculum(session)
        await ensure_platform_defaults(session)
        await session.commit()
        from sqlalchemy import select

        from app.models.curriculum import Course, Subject

        course = (await session.execute(select(Course).limit(1))).scalar_one()
        subject = (
            await session.execute(select(Subject).where(Subject.course_id == course.id).limit(1))
        ).scalar_one()
        owner = await make_user(session, "SUPER_ADMIN")
        student = await make_user(session, "STUDENT")
        from app.models.user import User

        friend = User(
            auth_user_id=f"test-auth-friend-{uuid.uuid4().hex[:8]}",
            email=f"friend-{uuid.uuid4().hex[:6]}@example.com",
            display_name="Friend",
            role="STUDENT",
        )
        session.add(friend)
        await session.commit()
        question = Question(
            id=uuid.uuid4(),
            course_id=course.id,
            subject_id=subject.id,
            text="Published stem for the review job.",
            question_type="MCQ",
            correct_answer="A",
            status="PUBLISHED",
            verified_by=owner.id,
            created_by=owner.id,
            review_state="CURRENT",
        )
        session.add(question)
        await session.flush()
        session.add(
            QuestionFlag(
                id=uuid.uuid4(),
                question_id=question.id,
                reported_by=student.id,
                reason="HISTORICAL",
                detail="Finance Act year may have moved.",
                status="OPEN",
            )
        )
        await session.commit()
        before = question.correct_answer

        async with Actor(session, student) as client:
            code = await client.get("/api/v1/campus/referrals")
            ticket = await client.post(
                "/api/v1/campus/support",
                json={"subject": "Cannot open a paper", "body": "The start button did nothing."},
            )
            projection = await client.get("/api/v1/progress/projection")
        async with Actor(session, owner) as client:
            created_term = await client.post(
                "/api/v1/admin/glossary",
                json={
                    "term": "Exam mode",
                    "definition": "A record that answers stay hidden until submit. No camera.",
                    "published": True,
                },
            )
        async with Actor(session, student) as client:
            glossary = await client.get("/api/v1/campus/glossary")
        async with Actor(session, friend) as client:
            redeemed = await client.post(
                "/api/v1/campus/referrals/redeem",
                json={"code": code.json()["data"]["code"]},
            )
        async with Actor(session, owner) as client:
            job = await client.post("/api/v1/admin/review-queue/run")
            queue = await client.get("/api/v1/admin/review-queue")
        await session.refresh(question)
        return {
            "code": code.json()["data"],
            "ticket": ticket.json()["data"],
            "projection": projection.json()["data"],
            "createdTerm": created_term.json()["data"],
            "glossary": glossary.json()["data"],
            "redeemed": redeemed.json()["data"],
            "job": job.json()["data"],
            "queue": queue.json()["data"],
            "answer": question.correct_answer,
            "before": before,
            "status": question.status,
        }

    result = run_in_database(database_url, body)
    assert result["code"]["premiumGranted"] is False
    assert result["ticket"]["emailSent"] is False
    assert result["projection"]["examScore"] is None
    assert result["projection"]["enoughData"] is False
    assert any(entry["term"] == "Exam mode" for entry in result["glossary"]["entries"])
    assert result["redeemed"]["premiumGranted"] is False
    assert result["redeemed"]["recorded"] is True
    assert result["job"]["answersChanged"] is False
    assert result["job"]["publishedAutomatically"] is False
    assert result["job"]["needsUpdate"] == 1
    assert result["answer"] == result["before"] == "A"
    assert result["status"] == "PUBLISHED"
    assert result["queue"]["questions"][0]["reviewState"] == "NEEDS_UPDATE"
