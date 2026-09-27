"""HTTP-level contract tests.

Verifies the wire contract from blueprint v3 §7.2 with FastAPI's TestClient, so
the React client can be built against a shape that is actually asserted:
envelopes, pagination meta, RFC 7807 errors and the strict-input convention.

The checked-in OpenAPI document is verified here too. It exists so the client can
be written against a file rather than a running server; the drift test is what
keeps it from becoming a stale lie.
"""

from __future__ import annotations

import inspect
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.envelope import paginated, problem, success
from app.main import app


@pytest.fixture
def client() -> TestClient:
    # No auth override: unauthenticated behaviour is part of the contract.
    return TestClient(app)


class TestEnvelopeHelpers:
    def test_success_shape(self):
        body = success({"id": "q1"}, request_id="abc123")
        assert set(body) == {"data", "meta"}
        assert body["data"] == {"id": "q1"}
        assert body["meta"]["requestId"] == "abc123"

    def test_paginated_computes_has_more(self):
        body = paginated([1, 2], total=10, page=1, limit=2)
        assert body["meta"]["hasMore"] is True
        assert body["meta"]["total"] == 10

    def test_last_page_has_more_false(self):
        body = paginated([9, 10], total=10, page=5, limit=2)
        assert body["meta"]["hasMore"] is False

    def test_exact_final_page_has_more_false(self):
        # page*limit == total must not report another page.
        body = paginated([9, 10], total=10, page=1, limit=10)
        assert body["meta"]["hasMore"] is False

    def test_problem_body_status_matches_http_status(self):
        resp = problem(status=400, title="Bad Request", detail="nope")
        assert resp.status_code == 400
        assert b'"status":400' in resp.body

    def test_problem_includes_field_errors(self):
        resp = problem(
            status=422,
            title="Validation Failed",
            errors=[{"field": "marks", "message": "must be positive"}],
        )
        assert b"marks" in resp.body


class TestHealthContract:
    def test_liveness_does_not_touch_dependencies(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["process"]["status"] == "up"

    def test_health_db_returns_503_when_the_database_is_unreachable(self, client, monkeypatch):
        """The regression that matters.

        A health check returning 200 while naming a dependency 'down' makes every
        uptime monitor useless. The HTTP status must agree with the body.
        """

        def exploding_engine(_settings=None):
            raise RuntimeError("connection refused")

        monkeypatch.setattr("app.core.dependencies.get_engine", exploding_engine, raising=True)
        resp = client.get("/health/db")
        assert resp.status_code == 503, "a down database must not report healthy"
        body = resp.json()
        assert body["status"] == "error"
        assert body["checks"]["postgres"]["status"] == "down"

    def test_health_redis_returns_503_when_unreachable(self, client, monkeypatch):
        """Deterministic whether or not a developer happens to run Redis.

        The patch used to target ``app.core.dependencies.get_redis_client``,
        which does NOTHING here: ``app/api/v1/health.py`` binds that name at
        import time, so the route kept calling the real client. The test then
        passed only because this machine had no Redis - with one running it
        returned 200 and the ``assert 503`` failed. Patch the name the route
        actually resolves.
        """

        def exploding_client(_settings=None):
            raise RuntimeError("redis refused")

        monkeypatch.setattr("app.api.v1.health.get_redis_client", exploding_client, raising=True)
        resp = client.get("/health/redis")
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"]["status"] == "down"

    def test_health_storage_distinguishes_unconfigured_from_broken(self, client):
        """Missing credentials are 'not_configured' (200), not an outage (503)."""
        resp = client.get("/health/storage")
        assert resp.status_code == 200
        assert resp.json()["checks"]["storage"]["status"] == "not_configured"


class TestAuthContract:
    def test_missing_token_is_401(self, client):
        resp = client.get("/api/v1/mocks")
        assert resp.status_code == 401

    def test_malformed_header_is_401(self, client):
        resp = client.get("/api/v1/mocks", headers={"Authorization": "Token abc"})
        assert resp.status_code in (401, 403)

    def test_invalid_bearer_token_is_401(self, client):
        resp = client.get("/api/v1/mocks", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_planner_requires_authentication(self, client):
        resp = client.post(
            "/api/v1/planner/generate",
            json={"exam_date": "2027-05-01", "daily_hours": 4, "subjects": []},
        )
        assert resp.status_code == 401


class TestRoot:
    def test_root_is_public(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["health"] == "/health"


class TestOpenApi:
    def test_schema_generates(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200

    def test_health_endpoints_are_documented(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for path in ("/health", "/health/db", "/health/redis", "/health/storage"):
            assert path in paths, f"{path} is missing from the OpenAPI schema"


class TestIngestionEndpoints:
    """§11.1: the ingestion surface.

    These tests pin the CONTRACT - paths, methods, and who is allowed in - without
    a database or storage backend. The handlers talk to Supabase and Postgres, so
    a live test would need both; the routing and authorization layer, which is
    where a mistake exposes source papers to students, is checkable here.
    """

    def test_the_ingestion_paths_are_documented(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        expected = {
            "/api/v1/ingestion/uploads": "post",
            "/api/v1/ingestion/jobs": "get",
            "/api/v1/ingestion/jobs/{job_id}": "get",
            "/api/v1/ingestion/jobs/{job_id}/start": "post",
            "/api/v1/ingestion/drafts": "get",
            "/api/v1/ingestion/drafts/{draft_id}/review": "post",
        }
        for path, method in expected.items():
            assert path in paths, f"{path} is missing from the OpenAPI schema"
            assert method in paths[path], f"{method.upper()} {path} is missing"

    def test_every_ingestion_route_requires_auth(self, client):
        """No ingestion route may be reachable anonymously.

        The backend holds the Supabase service-role key, so these routes are the
        only thing between an anonymous caller and the question bank's source
        material.
        """
        for path, method in (
            ("/api/v1/ingestion/uploads", "post"),
            ("/api/v1/ingestion/jobs", "get"),
            ("/api/v1/ingestion/drafts", "get"),
        ):
            resp = client.request(method, path)
            assert resp.status_code in (401, 403), (
                f"{method.upper()} {path} returned {resp.status_code} without a token"
            )

    def test_a_malformed_job_id_is_a_client_error_not_a_500(self, client):
        """A 500 here would look like a server fault and page an on-call engineer
        for what is a typo'd URL."""
        resp = client.get("/api/v1/ingestion/jobs/not-a-uuid")
        assert resp.status_code in (401, 403, 400, 404)
        assert resp.status_code != 500

    def test_upload_requires_manage_content_not_just_a_role_name(self):
        """Uploading source papers is content work, expressed as a PERMISSION.

        This test used to assert ``require_role`` was callable and that EDITOR is not
        STUDENT - both true and neither about the route, so it would have passed with
        the endpoint unprotected. It now reads the permission the route really
        requires, out of the dependency closure FastAPI was handed, and checks the
        matrix the same closure consults.
        """
        from app.api.v1 import ingestion
        from app.core.permissions import ROLE_PERMISSIONS, Permission
        from app.core.security import Role

        def required(route) -> set[str]:
            found: set[str] = set()

            def walk(dependant) -> None:
                for dependency in getattr(dependant, "dependencies", []):
                    call = getattr(dependency, "call", None)
                    # `call` is not always a function: HTTPBearer and friends are
                    # instances with a __call__. Only functions carry closure vars.
                    if call is not None and inspect.isfunction(call):
                        for value in inspect.getclosurevars(call).nonlocals.values():
                            if isinstance(value, tuple) and value and hasattr(value[0], "value"):
                                found.update(item.value for item in value)
                    walk(dependency)

            walk(route.dependant)
            return found

        upload = next(
            route for route in ingestion.router.routes if route.path == "/ingestion/uploads"
        )
        assert required(upload) == {"MANAGE_CONTENT"}, (
            "the upload route must ask for MANAGE_CONTENT - a role name would let a "
            "future role inherit upload rights by accident"
        )
        assert Permission.MANAGE_CONTENT in ROLE_PERMISSIONS[Role.EDITOR]
        assert ROLE_PERMISSIONS[Role.STUDENT] == frozenset(), (
            "a student holds no administrative permission at all"
        )


class TestStrictInputConvention:
    """The strict-input convention, and the one exemption inside it.

    Pydantic's lax mode coerces (``"yes"`` -> True, ``"5"`` -> 5), which is how a
    string reaches an integer column and how a permission check reads a truthy
    value. ``StrictRequest`` blocks that.

    UUIDs are exempt, because JSON has no UUID type: the only way a client can
    send one is as a string, so strict mode made every UUID body field unusable
    over HTTP while passing every test that built the model with a real UUID.
    These tests pin both halves - the exemption works, and it has not widened.
    """

    def test_strict_mode_rejects_a_boolean_lookalike(self):
        from app.api.v1.ingestion import ReviewDraftIn

        with pytest.raises(ValidationError) as excinfo:
            ReviewDraftIn(decision="REJECT", is_historical="yes")
        assert "is_historical" in str(excinfo.value)

    def test_strict_mode_rejects_a_numeric_string(self):
        from app.api.v1.ingestion import ReviewDraftIn

        with pytest.raises(ValidationError):
            ReviewDraftIn(decision="APPROVE", marks="10")

    def test_strict_mode_rejects_an_unknown_field(self):
        from app.api.v1.ingestion import ReviewDraftIn

        with pytest.raises(ValidationError) as excinfo:
            ReviewDraftIn(decision="REJECT", not_a_real_field=1)
        assert "not_a_real_field" in str(excinfo.value)

    def test_a_uuid_field_accepts_a_string_which_is_what_json_sends(self):
        """Without UuidRef this fails with is_instance_of, and the endpoint is
        unreachable from any real client."""
        import uuid as _uuid

        from app.api.v1.ingestion import ReviewDraftIn

        value = str(_uuid.uuid4())
        model = ReviewDraftIn(decision="APPROVE", subject_id=value)
        assert str(model.subject_id) == value

    def test_a_uuid_field_also_accepts_a_uuid_object(self):
        """Internal callers and tests construct the model directly."""
        import uuid as _uuid

        from app.api.v1.ingestion import ReviewDraftIn

        value = _uuid.uuid4()
        assert ReviewDraftIn(decision="APPROVE", subject_id=value).subject_id == value

    def test_a_malformed_uuid_string_is_still_refused(self):
        """The exemption accepts the WIRE FORM, not arbitrary rubbish."""
        from app.api.v1.ingestion import ReviewDraftIn

        with pytest.raises(ValidationError):
            ReviewDraftIn(decision="APPROVE", subject_id="not-a-uuid")

    def test_the_uuid_exemption_does_not_relax_neighbouring_fields(self):
        """The failure mode that would make UuidRef dangerous: if relaxing one
        field quietly relaxed the model, marks would start coercing again."""
        import uuid as _uuid

        from app.api.v1.ingestion import ReviewDraftIn

        with pytest.raises(ValidationError):
            ReviewDraftIn(decision="APPROVE", subject_id=str(_uuid.uuid4()), marks="10")

    def test_every_uuid_in_an_inbound_schema_uses_the_exemption(self):
        """A bare uuid.UUID in a request body is a bug waiting for a real client.

        Checked over the whole ingestion request surface rather than one schema,
        because the mistake is invisible in code review - it only shows up as a
        422 in production.
        """
        import typing
        import uuid as _uuid

        from app.schemas.base import StrictRequest

        def walk(model) -> list[str]:
            offenders = []
            for name, info in model.model_fields.items():
                annotation = info.annotation
                candidates = typing.get_args(annotation) or (annotation,)
                for candidate in candidates:
                    if candidate is _uuid.UUID:
                        offenders.append(f"{model.__name__}.{name}")
            return offenders

        from app.api.v1.ingestion import CreateUploadIn, ReviewDraftIn

        offenders = []
        for model in (CreateUploadIn, ReviewDraftIn):
            assert issubclass(model, StrictRequest)
            offenders += walk(model)
        assert offenders == [], (
            f"bare uuid.UUID in a request body: {offenders}. JSON sends UUIDs as "
            "strings, which strict mode rejects - use UuidRef."
        )


class TestPaymentsContract:
    """§11: the payment endpoints, as documented.

    Asserted from the generated schema rather than from the route table, because
    the schema is what a client consumes.
    """

    EXPECTED: ClassVar[list[str]] = [
        "/api/v1/payments/plans",
        "/api/v1/payments/order",
        "/api/v1/payments/confirm",
        "/api/v1/payments/subscription",
        "/api/v1/webhooks/razorpay",
    ]

    def test_every_payment_path_is_documented(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        for path in self.EXPECTED:
            assert path in paths, f"{path} is missing from the contract"

    def test_the_pricing_table_is_public(self, client: TestClient) -> None:
        """A plan catalogue behind auth is a catalogue nobody reads before
        signing up for anything."""
        operation = client.get("/openapi.json").json()["paths"]["/api/v1/payments/plans"]["get"]
        assert operation.get("security") is None

    @pytest.mark.parametrize(
        "path,method",
        [
            ("/api/v1/payments/order", "post"),
            ("/api/v1/payments/confirm", "post"),
            ("/api/v1/payments/subscription", "get"),
        ],
    )
    def test_everything_money_shaped_requires_a_bearer_token(
        self, client: TestClient, path: str, method: str
    ) -> None:
        """Declared in the schema, not just enforced at runtime: a client that
        generates its auth handling from this document must know it needs a token
        on the endpoints that spend money."""
        operation = client.get("/openapi.json").json()["paths"][path][method]
        assert operation.get("security") == [{"HTTPBearer": []}]

    def test_the_webhook_is_authenticated_by_signature_not_by_token(
        self, client: TestClient
    ) -> None:
        """Deliberate, and worth pinning.

        Razorpay cannot present a user access token, so a bearer requirement here
        would make every genuine webhook 401. Its authentication is the HMAC over
        the raw body, verified in the handler - so the absence of a security
        scheme is not an oversight.
        """
        operation = client.get("/openapi.json").json()["paths"]["/api/v1/webhooks/razorpay"]["post"]
        assert operation.get("security") is None

    def test_admin_payment_lists_require_a_bearer_token(self, client: TestClient) -> None:
        """The ledger is an admin route. A generated client must know it needs a token.

        These two paths are the reconciliation view. They are not the checkout
        routes, and they must not be public just because the plan catalogue is.
        """
        paths = client.get("/openapi.json").json()["paths"]
        for path in (
            "/api/v1/admin/payments/orders",
            "/api/v1/admin/payments/events",
            "/api/v1/admin/questions",
            "/api/v1/admin/plans",
            "/api/v1/admin/storage",
            "/api/v1/admin/ai",
        ):
            assert path in paths, f"{path} is missing from the contract"
            operation = paths[path]["get"]
            assert operation.get("security") == [{"HTTPBearer": []}]
        ask = paths["/api/v1/assistant/ask"]["post"]
        assert ask.get("security") == [{"HTTPBearer": []}]

    def test_the_webhook_documents_no_request_body(self, client: TestClient) -> None:
        """The raw body is signed; a declared body would be parsed first."""
        operation = client.get("/openapi.json").json()["paths"]["/api/v1/webhooks/razorpay"]["post"]
        assert "requestBody" not in operation


class TestOpenApiDocumentIsInSync:
    """The checked-in spec must equal what the app generates.

    A stale OpenAPI file is worse than none: a client generated from it compiles,
    runs, and calls endpoints that no longer exist. This test makes updating the
    file a deliberate act - it fails the moment a route, field or response shape
    changes without regenerating.
    """

    def test_the_committed_document_matches_the_app(self) -> None:
        import json
        import pathlib

        import app.main as main

        document = pathlib.Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"
        assert document.is_file(), (
            "docs/api/openapi.json is missing. Regenerate it with:\n"
            '  python3 -c "import json;from app.main import app;'
            'print(json.dumps(app.openapi(), indent=2, sort_keys=True))" > ../docs/api/openapi.json'
        )
        committed = json.loads(document.read_text())
        assert committed == main.app.openapi(), (
            "docs/api/openapi.json is out of date: regenerate it (see the message in "
            "this test) and commit the result."
        )
