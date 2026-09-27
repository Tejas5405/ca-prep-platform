"""Request-id propagation contract for 200 / 4xx / 500.

Two defects are pinned here, both observed live before the fix:

  * every access-log line recorded ``request_id: "-"`` because the contextvar
    was reset before the log call;
  * an unhandled exception escaped the request-id middleware (the catch-all
    handler runs in Starlette's ServerErrorMiddleware, outside user middleware),
    so a 500 response had NO ``X-Request-Id`` header while its body claimed the
    id was in the headers - and the error log line recorded ``-``.

A request id is not a secret (16 hex characters, client-supplied or generated),
but it is the ONLY way to tie a user's error report to a server log entry, so the
same value must appear in the response and in the log.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_db
from app.main import app


def test_success_response_carries_the_client_request_id():
    with TestClient(app) as client:
        resp = client.get("/health", headers={"X-Request-Id": "test-200-id"})
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "test-200-id"


def test_generated_request_id_is_present_and_not_a_placeholder():
    with TestClient(app) as client:
        resp = client.get("/health")
    rid = resp.headers.get("x-request-id")
    assert rid and rid != "-"
    assert len(rid) == 16, "the generated id is 16 hex characters"


def test_4xx_response_carries_the_client_request_id():
    with TestClient(app) as client:
        resp = client.get("/api/v1/mocks", headers={"X-Request-Id": "test-401-id"})
    assert resp.status_code == 401
    assert resp.headers["x-request-id"] == "test-401-id"


def test_validation_path_carries_the_client_request_id():
    """A malformed body is a 4xx the operator can fix; the id must survive it."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/payments/order",
            json={},
            headers={"X-Request-Id": "test-422-id"},
        )
    # Auth is checked before the body, so this is 401 or 422 - either way it is
    # the 4xx path and the header must be attached.
    assert resp.status_code in (401, 422)
    assert resp.headers["x-request-id"] == "test-422-id"


@pytest.fixture()
def exploding_db():
    """A database dependency that fails the way an unforeseen bug would."""

    async def _boom():
        raise RuntimeError("deliberate failure for the request-id contract")

    app.dependency_overrides[get_db] = _boom
    yield
    app.dependency_overrides.pop(get_db, None)


def test_500_response_and_error_log_share_the_request_id(caplog, exploding_db):
    with caplog.at_level(logging.ERROR, logger="app.error"):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/payments/plans", headers={"X-Request-Id": "test-500-id"})

    assert resp.status_code == 500
    # The body tells the client to look in the headers - the header must exist.
    assert "request id is in the response headers" in resp.text
    assert resp.headers.get("x-request-id") == "test-500-id"

    records = [record for record in caplog.records if record.name == "app.error"]
    assert records, "an unhandled 500 must be logged"
    assert getattr(records[-1], "request_id", None) == "test-500-id"


def test_500_access_line_is_logged_with_the_request_id(caplog, exploding_db):
    with caplog.at_level(logging.INFO, logger="app.access"):
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/api/v1/payments/plans", headers={"X-Request-Id": "test-access-id"})

    access = [record for record in caplog.records if record.name == "app.access"]
    assert access, "a 500 must still produce an access-log line"
    assert "500" in access[-1].getMessage()
    assert getattr(access[-1], "request_id", None) == "test-access-id"
