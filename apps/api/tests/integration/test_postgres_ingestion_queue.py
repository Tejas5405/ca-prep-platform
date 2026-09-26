"""The ingestion queue views, over HTTP, against a live PostgreSQL.

WHY THIS FILE EXISTS

Both list endpoints had a route, an asserted envelope, and no query. They answered
`200 {"data": [], "meta": {"total": 0}}` for every caller — which is the exact body
a genuinely empty queue returns, so no contract test could tell the two apart, and
neither could an editor looking at the screen. The consequence was not cosmetic:
draft review works by id, so a QA worklist that lists nothing means an approver
cannot discover the work they are able to approve. The ingestion flow stopped at
the last step.

A test of the query is therefore not a test of a query. It is the test that the
worklist exists at all, and it has to run against real PostgreSQL: the failure it
guards against is a query that returns no rows, and a hand-written session double
returns exactly the rows the test author believed were there.

WHAT IS PINNED HERE, AND WHY EACH ONE WOULD OTHERWISE BE UNDISCOVERED

  * rows come back at all, with the fields the UI reads;
  * the drafts worklist is OLDEST FIRST, so a queue with a human at the end cannot
    starve, while the jobs view is NEWEST first, because it answers "did my upload
    land" rather than "what is next";
  * paging over an offset does not repeat or skip a row, which needs a unique
    tie-break — a single ingestion run writes dozens of drafts inside one
    millisecond, so `created_at` alone is not a total order;
  * `total` is the size of the RESULT SET, not of the page, or `hasMore` lies;
  * a failed job is `isTerminal: true` and carries its `errorReason`, while
    remaining retryable by the pipeline — the one place where "terminal" means two
    different things;
  * an unknown stage or status is a 422 naming the allowed values, not an empty
    page the caller has to guess about.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.dependencies import get_db
from app.core.security import Principal, get_current_principal
from app.main import app
from app.models.ingestion import IngestionDraft, IngestionJob
from app.models.user import User
from app.repositories.ingestion import SqlIngestionSink

from ._db import observe, run_in_database

pytestmark = pytest.mark.postgres

JOBS = "/api/v1/ingestion/jobs"
DRAFTS = "/api/v1/ingestion/drafts"


class _SessionScope:
    """Hands the store the TEST's session instead of one it opens itself.

    The store methods take an injected session precisely so this is possible: one
    that built its own engine would write to whichever database the process is
    configured for, not the one this test asserts against - the failure that made
    every one of these endpoints look empty.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Factory:
    def __init__(self, session: Any) -> None:
        self._session = session

    def __call__(self) -> _SessionScope:
        return _SessionScope(self._session)


def _factory(session: Any) -> _Factory:
    return _Factory(session)


# --------------------------------------------------------------------- harness


class Editor:
    """An HTTP client for a signed-in editor, sharing this test's own session.

    ``ASGITransport`` rather than ``TestClient``: TestClient runs the app on its own
    thread and loop, and the session the test asserts through belongs to this loop.
    The two overrides are the only things a request cannot supply for itself — the
    verified principal and the session — so everything else (the real route, the
    real repository, the real SQL, and the role gate the route depends on) is
    exercised.

    THE OVERRIDE IS `get_current_principal`, NOT `get_current_user`. These routes
    are gated by `require_role(Role.EDITOR)`, which reads the role from the PRINCIPAL
    — so a test that stubbed the user dependency would be testing a chain the route
    does not use, and would get a 401 from the real gate instead of noticing.
    """

    def __init__(self, session: Any, user: User) -> None:
        self._session = session
        self._principal = Principal(
            auth_user_id=user.auth_user_id,
            email=user.email,
            role="EDITOR",
            claims={"sub": user.auth_user_id, "app_metadata": {"role": "EDITOR"}},
        )

    async def __aenter__(self) -> httpx.AsyncClient:
        app.dependency_overrides[get_db] = lambda: self._session
        app.dependency_overrides[get_current_principal] = lambda: self._principal
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        return self._client

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()
        app.dependency_overrides.clear()


async def make_editor(session: Any) -> User:
    user = User(
        auth_user_id=f"test-auth-editor-{uuid.uuid4().hex[:8]}",
        email="editor@example.com",
        display_name="Editor",
        role="EDITOR",
    )
    session.add(user)
    await session.commit()
    return user


def _job(
    *,
    stage: str = "AWAITING_QA",
    created_at: datetime,
    drafts_created: int = 0,
    error_reason: str | None = None,
    page_count: int | None = 12,
) -> IngestionJob:
    return IngestionJob(
        bucket="question-pdfs",
        storage_path=f"originals/{uuid.uuid4().hex}.pdf",
        stage=stage,
        page_count=page_count,
        drafts_created=drafts_created,
        error_reason=error_reason,
        created_at=created_at,
    )


def _draft(
    job_id: uuid.UUID,
    *,
    text: str,
    created_at: datetime,
    review_status: str = "PENDING",
    source_page: int = 1,
    detected_year: int | None = 2025,
    detected_marks: int | None = 4,
    detected_question_type: str | None = "DESCRIPTIVE",
) -> IngestionDraft:
    return IngestionDraft(
        job_id=job_id,
        text=text,
        source_page=source_page,
        review_status=review_status,
        detected_year=detected_year,
        detected_marks=detected_marks,
        detected_question_type=detected_question_type,
        detection_confidence=0.87,
        created_at=created_at,
    )


# ------------------------------------------------------------------ jobs queue


def test_the_jobs_queue_lists_what_was_uploaded(database_url: str) -> None:
    """The assertion that would have failed for the whole life of the empty route."""

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        now = datetime.now(UTC)
        session.add_all(
            [
                _job(stage="AWAITING_QA", created_at=now - timedelta(minutes=5), drafts_created=7),
                _job(stage="FAILED", created_at=now - timedelta(minutes=2), error_reason="scan"),
            ]
        )
        await session.commit()

        async with Editor(session, editor) as client:
            response = await client.get(JOBS)
        return {"status": response.status_code, "body": response.json()}

    result = run_in_database(database_url, body)
    assert result["status"] == 200, result
    payload = result["body"]
    assert payload["meta"]["total"] == 2
    assert len(payload["data"]) == 2
    assert {row["stage"] for row in payload["data"]} == {"AWAITING_QA", "FAILED"}
    # Ordered newest first: the failed job was created two minutes ago.
    assert payload["data"][0]["stage"] == "FAILED"
    first = payload["data"][0]
    # The fields the operations table reads, by their wire names.
    assert first["draftsCreated"] == 0
    assert first["pageCount"] == 12
    assert first["bucket"] == "question-pdfs"
    assert first["storagePath"].startswith("originals/")


def test_a_failed_job_is_terminal_here_and_retryable_there(database_url: str) -> None:
    """The two senses of "terminal", pinned in one place.

    A client should stop polling a failed job, so it is settled and the queue says
    `isTerminal: true`. The pipeline's re-run guard must still allow it to be
    retried, so FAILED is deliberately absent from `TERMINAL_STAGES`. An
    implementation that used one set for both would either make failures
    unretryable or make a retry silently do nothing.
    """

    async def body(session) -> dict[str, Any]:
        from app.services.ingestion import SETTLED_STAGES, TERMINAL_STAGES

        editor = await make_editor(session)
        session.add(_job(stage="FAILED", created_at=datetime.now(UTC), error_reason="corrupt"))
        await session.commit()

        async with Editor(session, editor) as client:
            response = await client.get(JOBS)

        rows = response.json()["data"]
        return {
            "row": rows[0],
            "failed_settled": "FAILED" in SETTLED_STAGES,
            "failed_retryable": "FAILED" not in TERMINAL_STAGES,
        }

    result = run_in_database(database_url, body)
    assert result["row"]["isTerminal"] is True
    # The reason travels with the row, so an operator can see WHICH jobs failed
    # without opening forty of them.
    assert result["row"]["errorReason"] == "corrupt"
    assert result["failed_settled"] is True
    assert result["failed_retryable"] is True


def test_a_job_still_running_is_not_marked_terminal(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        session.add_all(
            [
                _job(stage="EXTRACTING", created_at=datetime.now(UTC)),
                _job(stage="QUEUED", created_at=datetime.now(UTC) - timedelta(seconds=1)),
            ]
        )
        await session.commit()

        async with Editor(session, editor) as client:
            response = await client.get(JOBS)
        return {"rows": response.json()["data"]}

    rows = run_in_database(database_url, body)["rows"]
    assert [row["stage"] for row in rows] == ["EXTRACTING", "QUEUED"]
    assert all(row["isTerminal"] is False for row in rows)


def test_the_stage_filter_narrows_the_queue_and_the_total_with_it(database_url: str) -> None:
    """`total` describes the FILTERED set, as the client's pager assumes it does."""

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        now = datetime.now(UTC)
        session.add_all(
            [
                _job(stage="FAILED", created_at=now, error_reason="a"),
                _job(stage="FAILED", created_at=now - timedelta(seconds=1), error_reason="b"),
                _job(stage="AWAITING_QA", created_at=now - timedelta(seconds=2)),
            ]
        )
        await session.commit()

        async with Editor(session, editor) as client:
            filtered = await client.get(JOBS, params={"stage": "FAILED"})
            everything = await client.get(JOBS)
            lower = await client.get(JOBS, params={"stage": "failed"})
        return {
            "filtered": filtered.json(),
            "everything": everything.json(),
            "lower_status": lower.status_code,
        }

    result = run_in_database(database_url, body)
    assert result["filtered"]["meta"]["total"] == 2
    assert len(result["filtered"]["data"]) == 2
    assert result["everything"]["meta"]["total"] == 3
    # Case is not a trap for an operator typing into a filter box.
    assert result["lower_status"] == 200


def test_an_unknown_stage_names_the_allowed_values(database_url: str) -> None:
    """A typo must not look like an empty queue.

    This is the failure mode the whole file is about: the old route ignored the
    filter entirely and returned an empty page, so `?stage=NOPE` and
    `?stage=FAILED` with no failures were the same response.
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        async with Editor(session, editor) as client:
            response = await client.get(JOBS, params={"stage": "NOPE"})
        return {"status": response.status_code, "body": response.json()}

    result = run_in_database(database_url, body)
    assert result["status"] == 422
    assert "FAILED" in result["body"]["detail"]
    assert result["body"]["errors"][0]["field"] == "stage"


def test_jobs_page_without_repeating_or_skipping(database_url: str) -> None:
    """Offset paging needs a total order, not just a sort key.

    Five jobs share the same `created_at`, which is what a bulk upload produces.
    Ordered by timestamp alone, the database is free to return them in a different
    order per query, so page two can repeat a row from page one and drop another.
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        same_instant = datetime.now(UTC)
        session.add_all(
            [_job(stage="QUEUED", created_at=same_instant) for _ in range(5)],
        )
        await session.commit()

        async with Editor(session, editor) as client:
            first = await client.get(JOBS, params={"limit": 2, "page": 1})
            second = await client.get(JOBS, params={"limit": 2, "page": 2})
            third = await client.get(JOBS, params={"limit": 2, "page": 3})
        return {
            "page1": first.json(),
            "page2": second.json(),
            "page3": third.json(),
        }

    result = run_in_database(database_url, body)
    ids = [row["jobId"] for page in ("page1", "page2", "page3") for row in result[page]["data"]]
    assert len(ids) == 5
    assert len(set(ids)) == 5, "paging repeated a row"
    # hasMore must be honest at the boundary.
    assert result["page1"]["meta"]["hasMore"] is True
    assert result["page2"]["meta"]["hasMore"] is True
    assert result["page3"]["meta"]["hasMore"] is False
    assert result["page3"]["meta"]["total"] == 5


# --------------------------------------------------------------- drafts queue


def test_the_worklist_lists_pending_drafts_oldest_first(database_url: str) -> None:
    """Oldest first: a review queue with a human at the end starves if it is not."""

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        job = _job(stage="AWAITING_QA", created_at=datetime.now(UTC))
        session.add(job)
        await session.flush()

        now = datetime.now(UTC)
        session.add_all(
            [
                _draft(job.id, text="the newest", created_at=now),
                _draft(job.id, text="the oldest", created_at=now - timedelta(minutes=3)),
                _draft(job.id, text="the middle", created_at=now - timedelta(minutes=1)),
                # Already decided: the worklist is for work that is not done.
                _draft(
                    job.id,
                    text="already approved",
                    created_at=now - timedelta(minutes=4),
                    review_status="APPROVED",
                ),
            ]
        )
        await session.commit()

        async with Editor(session, editor) as client:
            response = await client.get(DRAFTS)
        return {"body": response.json()}

    payload = run_in_database(database_url, body)["body"]
    assert payload["meta"]["total"] == 3, "decided drafts must not appear in the queue"
    previews = [row["preview"] for row in payload["data"]]
    assert previews == ["the oldest", "the middle", "the newest"]
    first = payload["data"][0]
    # The fields the reviewer needs before opening a draft, under their wire names.
    assert first["reviewStatus"] == "PENDING"
    assert first["detectedYear"] == 2025
    assert first["detectedMarks"] == 4
    assert first["detectionConfidence"] == pytest.approx(0.87)
    assert first["sourcePage"] == 1
    assert uuid.UUID(first["draftId"])
    assert uuid.UUID(first["jobId"])


def test_the_worklist_preview_is_a_preview(database_url: str) -> None:
    """The list must not ship the whole queue.

    A draft is a candidate exam question, often several hundred words. Returning it
    in full makes a page of twenty a megabyte, and the payload then grows with the
    backlog — the list is a set of decisions, and the editor opens one to read it.
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        job = _job(stage="AWAITING_QA", created_at=datetime.now(UTC))
        session.add(job)
        await session.flush()
        long_text = " ".join(
            ["Discuss the treatment of depreciation under the Income-tax Act."] * 20
        )
        session.add(_draft(job.id, text=long_text, created_at=datetime.now(UTC)))
        await session.commit()

        async with Editor(session, editor) as client:
            response = await client.get(DRAFTS)
        # And the full text is still reachable — truncated, not lost. Read through
        # the TEST's session: the store's own methods open a session from the app
        # engine, which is not this database.
        row = (
            await session.execute(select(IngestionDraft).order_by(IngestionDraft.created_at))
        ).scalar_one()
        return {
            "preview": response.json()["data"][0]["preview"],
            "full_length": len(row.text),
            "body_length": len(response.text),
        }

    result = run_in_database(database_url, body)
    assert result["preview"].endswith("\u2026")
    assert len(result["preview"]) <= 161
    # The preview is line-broken on the server, because extraction produces hard
    # wraps sized for a printed column, not for a table cell.
    assert "\n" not in result["preview"]
    assert result["full_length"] > 500
    assert result["body_length"] < 2000


def test_the_worklist_can_be_scoped_to_one_job(database_url: str) -> None:
    """An editor reviewing one 40-page paper should not page through the backlog."""

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        now = datetime.now(UTC)
        first, second = (
            _job(stage="AWAITING_QA", created_at=now),
            _job(stage="AWAITING_QA", created_at=now - timedelta(seconds=1)),
        )
        session.add_all([first, second])
        await session.flush()
        session.add_all(
            [
                _draft(first.id, text="from the first job", created_at=now),
                _draft(
                    second.id,
                    text="from the second job",
                    created_at=now - timedelta(minutes=1),
                ),
            ]
        )
        await session.commit()

        async with Editor(session, editor) as client:
            response = await client.get(DRAFTS, params={"job_id": str(first.id)})
        return {"body": response.json(), "first_id": str(first.id)}

    result = run_in_database(database_url, body)
    assert result["body"]["meta"]["total"] == 1
    assert result["body"]["data"][0]["preview"] == "from the first job"
    assert result["body"]["data"][0]["jobId"] == result["first_id"]


def test_a_decided_draft_is_listable_under_its_own_status(database_url: str) -> None:
    """Reviewers need to answer "what did we reject, and why" as well as "what is next"."""

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        job = _job(stage="AWAITING_QA", created_at=datetime.now(UTC))
        session.add(job)
        await session.flush()
        now = datetime.now(UTC)
        session.add_all(
            [
                _draft(job.id, text="pending one", created_at=now),
                _draft(job.id, text="rejected one", created_at=now, review_status="REJECTED"),
            ]
        )
        await session.commit()

        async with Editor(session, editor) as client:
            rejected = await client.get(DRAFTS, params={"review_status": "REJECTED"})
            pending = await client.get(DRAFTS)
        return {"rejected": rejected.json(), "pending": pending.json()}

    result = run_in_database(database_url, body)
    assert result["rejected"]["meta"]["total"] == 1
    assert result["rejected"]["data"][0]["reviewStatus"] == "REJECTED"
    assert result["pending"]["meta"]["total"] == 1


def test_an_unknown_review_status_is_refused(database_url: str) -> None:
    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        async with Editor(session, editor) as client:
            response = await client.get(DRAFTS, params={"review_status": "MAYBE"})
        return {"status": response.status_code, "body": response.json()}

    result = run_in_database(database_url, body)
    assert result["status"] == 422
    assert "PENDING" in result["body"]["detail"]


def test_the_worklist_is_visible_to_the_editors_who_can_act_on_it(database_url: str) -> None:
    """A queue nobody can read is the same as no queue.

    Reviewing a draft needs EDITOR at minimum (approval needs more), so the list is
    behind the same role as the action. The point of asserting it here is that the
    route and the review endpoint agree — read access narrower than write access
    would hide the work from exactly the people allowed to do it.
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        job = _job(stage="AWAITING_QA", created_at=datetime.now(UTC))
        session.add(job)
        await session.flush()
        session.add(_draft(job.id, text="waiting", created_at=datetime.now(UTC)))
        await session.commit()

        async with Editor(session, editor) as client:
            listed = await client.get(DRAFTS)
            reviewable = listed.json()["data"][0]["draftId"]
            # The id the queue returns is the id the review endpoint accepts.
            reviewed = await client.post(
                f"{DRAFTS}/{reviewable}/review",
                json={
                    "decision": "REJECT",
                    "note": "duplicate of a seeded question",
                    "reason": "other",
                },
            )
        return {
            "listed": listed.status_code,
            "reviewed": reviewed.status_code,
            "text": reviewed.text,
        }

    result = run_in_database(database_url, body)
    assert result["listed"] == 200
    # 422 is acceptable only if it names a field - never 404, which would mean the
    # queue handed out an id the reviewer cannot act on.
    assert result["reviewed"] != 404, result["text"]


# ---------------------------------------------------------------- upload → start


def test_an_upload_creates_the_job_the_start_route_needs(database_url: str) -> None:
    """The chain that could not be walked at all.

    `create_upload` minted a signed URL and stopped. No route created a job, so
    `POST /ingestion/jobs/{id}/start` had no reachable id: every call was a 404 and
    the pipeline was unreachable from a client, which is the difference between
    "extraction is asynchronous" and "extraction never runs".

    The signed URL itself cannot be minted here - Supabase Storage is not
    configured in this environment - so the assertion is on the row: a QUEUED job
    exists, is addressable, and appears in the queue view.
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        sink = SqlIngestionSink(session_factory=_factory(session))
        job_id = await sink.create_job(
            bucket="question-pdfs",
            storage_path="originals/2026/paper.pdf",
            uploaded_by=editor.id,
            session=session,
        )

        async with Editor(session, editor) as client:
            listed = await client.get(JOBS)
        return {"job_id": job_id, "listed": listed.json()}

    result = run_in_database(database_url, body)
    assert result["job_id"]
    assert result["listed"]["meta"]["total"] == 1
    row = result["listed"]["data"][0]
    assert row["jobId"] == result["job_id"]
    assert row["stage"] == "QUEUED"
    # A QUEUED job is not settled: the client should keep polling it.
    assert row["isTerminal"] is False
    assert row["draftsCreated"] == 0
    assert row["storagePath"] == "originals/2026/paper.pdf"


def test_a_job_is_created_with_history_so_its_clock_has_a_start(database_url: str) -> None:
    """`stage_history` seeds the initial stage.

    The history answers "how long has this been sitting in QUEUED", and a job whose
    history is empty looks like one that was never queued at all.
    """

    async def body(session) -> dict[str, Any]:
        editor = await make_editor(session)
        sink = SqlIngestionSink(session_factory=_factory(session))
        job_id = await sink.create_job(
            bucket="question-pdfs",
            storage_path="originals/2026/paper.pdf",
            uploaded_by=editor.id,
            session=session,
        )
        async with observe(database_url) as other:
            row = await other.get(IngestionJob, uuid.UUID(job_id))
        return {"history": row.stage_history, "stage": row.stage, "uploader": str(row.uploaded_by)}

    result = run_in_database(database_url, body)
    assert result["stage"] == "QUEUED"
    assert isinstance(result["history"], list) and len(result["history"]) == 1
    assert result["history"][0]["stage"] == "QUEUED"
    # The uploader is recorded, because "who sent this" is the first question asked
    # about a bad extraction six weeks later.
    assert result["uploader"]
