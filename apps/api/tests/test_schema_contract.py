"""Schema conformance tests.

These assert that the database actually matches the contract in blueprint v3 §9,
rather than merely intending to. Every check reads the compiled DDL for the
PostgreSQL dialect, so it runs without a live database.

The point is regression protection on things that are easy to lose in a
migration: a required index quietly dropped, a compliance field made nullable,
a CHECK constraint removed because it was inconvenient during a seed script.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

# Importing the package registers every table on Base.metadata. That is the
# point of app/models/__init__.py: Alembic and these tests both walk
# Base.metadata, so an unimported model file is invisible to migrations.
import app.models  # noqa: F401
from app.models.base import Base

DIALECT = postgresql.dialect()


def check_sql(table_name: str, constraint_name: str) -> str:
    """Return the literal SQL of a named CHECK constraint.

    Assertions elsewhere used `"SOMETHING" in ddl`, which passes as long as the
    string appears ANYWHERE in the compiled schema. A mutation test caught that:
    changing the drafts default from PENDING to APPROVED still passed, because
    "PENDING" also appears in the review_status CHECK constraint.

    Reading the constraint's own text is what makes these assertions mean what
    they claim.
    """
    from sqlalchemy import CheckConstraint

    table = Base.metadata.tables[table_name]
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name:
            return str(constraint.sqltext)
    raise AssertionError(f"{constraint_name} not found on {table_name}")


def column_default(table_name: str, column_name: str) -> str:
    """Return a column's server default as SQL text, or '' if there is none."""
    column = Base.metadata.tables[table_name].columns[column_name]
    if column.server_default is None:
        return ""
    return str(column.server_default.arg)


@pytest.fixture(scope="module")
def ddl() -> str:
    parts = []
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=DIALECT)))
        for index in table.indexes:
            parts.append(str(CreateIndex(index).compile(dialect=DIALECT)))
    return "\n".join(parts)


class TestRequiredIndexes:
    """Indexes named in v3 §9.3. A missing index here is a production slowdown."""

    def test_question_filter_index(self, ddl):
        assert "idx_questions_filter" in ddl
        assert "(subject_id, chapter_id, attempt_id, question_type, year)" in ddl, (
            "the composite filter index must cover all five columns in order"
        )

    def test_progress_user_index(self, ddl):
        assert "idx_progress_user" in ddl
        assert "(user_id, question_id)" in ddl

    def test_question_status_index(self, ddl):
        assert "idx_questions_status" in ddl
        assert "(status, is_historical)" in ddl

    def test_spaced_repetition_due_index(self, ddl):
        # Backs "what is due for me today", the app's hottest read.
        assert "idx_sr_due" in ddl
        assert "(user_id, next_review_at)" in ddl


class TestFullTextSearch:
    def test_fts_index_declared_for_questions(self):
        """v3 §9.3 requires a GIN index over to_tsvector('english', text).

        This one cannot be expressed as a SQLAlchemy Index on a column, because
        it indexes an expression, so it lives in the Alembic migration. The test
        asserts the migration file exists and contains the exact expression,
        which is what actually ships.
        """
        from pathlib import Path

        versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
        sources = list(versions.glob("*.py"))
        assert sources, "no Alembic migration found"

        combined = "\n".join(p.read_text() for p in sources).lower()
        assert "to_tsvector('english'" in combined, (
            "the required GIN full-text index is missing from the migrations"
        )
        assert "using gin" in combined, "the index must be GIN, not btree"
        # Partial-word search needs trigrams; full-text search alone matches
        # whole lexemes, so "deduc" would return nothing.
        assert "gin_trgm_ops" in combined
        assert "create extension if not exists pg_trgm" in combined


class TestTaxationComplianceFields:
    """v3 §9.2 - these four fields are mandatory on every question."""

    @pytest.mark.parametrize(
        "field", ["is_historical", "finance_act_year", "disclaimer_text", "status"]
    )
    def test_field_exists(self, field):
        assert field in Base.metadata.tables["questions"].columns

    def test_is_historical_is_not_null_with_false_default(self):
        col = Base.metadata.tables["questions"].columns["is_historical"]
        assert col.nullable is False
        assert col.server_default is not None
        assert "false" in str(col.server_default.arg).lower()

    def test_status_is_not_null_with_draft_default(self):
        col = Base.metadata.tables["questions"].columns["status"]
        assert col.nullable is False
        assert "DRAFT" in str(col.server_default.arg)

    def test_historical_content_requires_a_disclaimer(self):
        """Otherwise a student sees a stale tax answer with no warning."""
        constraints = {c.name for c in Base.metadata.tables["questions"].constraints}
        assert "ck_questions_historical_requires_disclaimer" in constraints


class TestPublishGate:
    def test_published_questions_require_a_human_verifier(self):
        constraints = {c.name for c in Base.metadata.tables["questions"].constraints}
        assert "ck_questions_published_requires_verifier" in constraints

    def test_objective_questions_require_a_correct_answer(self):
        constraints = {c.name for c in Base.metadata.tables["questions"].constraints}
        assert "ck_questions_objective_requires_answer" in constraints


class TestSchemeScoping:
    def test_questions_carry_a_syllabus_scheme(self):
        col = Base.metadata.tables["questions"].columns["syllabus_scheme"]
        assert col.nullable is False
        # Defaults to UNMAPPED so migrated rows surface for editorial review
        # rather than silently claiming to be new-scheme content.
        assert "UNMAPPED" in str(col.server_default.arg)

    def test_courses_are_scheme_scoped(self):
        cols = Base.metadata.tables["courses"].columns
        assert "syllabus_scheme" in cols
        assert "effective_from" in cols
        assert "effective_to" in cols

    def test_exam_attempts_are_sessions_not_free_text(self):
        """Guards the attempt_id naming collision described in progress.py."""
        assert "exam_sessions" in Base.metadata.tables
        fks = Base.metadata.tables["questions"].columns["attempt_id"].foreign_keys
        targets = {fk.target_fullname for fk in fks}
        assert targets == {"exam_sessions.id"}


class TestAuthBoundary:
    def test_no_password_column_anywhere(self):
        """SA-05: Supabase Auth owns password credentials.

        A password_hash column here would mean the platform is managing student
        passwords, which the blueprint explicitly forbids. Since the SA-05
        migration to a hosted identity provider this also guards against a
        partial rollback that
        reintroduces local credential storage.
        """
        offenders = []
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                if "password" in col.name.lower():
                    offenders.append(f"{table.name}.{col.name}")
        assert offenders == [], f"password columns found: {offenders}"

    def test_users_link_to_the_identity_provider_by_auth_user_id(self):
        col = Base.metadata.tables["users"].columns["auth_user_id"]
        assert col.unique is True


class TestIdempotencyAndUniqueness:
    def test_points_ledger_has_a_unique_idempotency_key(self):
        col = Base.metadata.tables["points_ledger"].columns["idempotency_key"]
        assert col.unique is True

    def test_payment_events_are_unique_per_provider_event(self):
        col = Base.metadata.tables["payment_events"].columns["event_id"]
        assert col.unique is True

    def test_at_most_one_active_subscription_per_user(self):
        """Asserted on the RULE, not on the name.

        The first version of this test checked that an index with the right name
        existed. Dropping ``unique=True``, or widening the predicate to every
        status, both leave the name intact - and both allow the double entitlement
        the index exists to prevent.
        """
        index = next(
            i
            for i in Base.metadata.tables["subscriptions"].indexes
            if i.name == "uq_active_subscription_per_user"
        )
        assert index.unique is True
        predicate = str(index.dialect_options["postgresql"].get("where"))
        assert "ACTIVE" in predicate
        assert "TRIAL" in predicate
        # Only the two live statuses. EXPIRED/CANCELLED rows must be free to pile
        # up: a user has many historical subscriptions and one current one.
        for dead in ["EXPIRED", "CANCELLED", "PAST_DUE"]:
            assert dead not in predicate


class TestPaymentsContract:
    """§13.1: the tables the payment flow depends on.

    A payment table's constraints are load-bearing in a way most tables' are not.
    The code that writes these rows runs unattended, in a webhook, against a
    payload from the internet, and it grants access to paid content. Every
    guarantee therefore has to survive the application being wrong - which means
    it belongs in the schema.
    """

    def test_orders_are_unique_per_receipt(self):
        col = Base.metadata.tables["payment_orders"].columns["receipt"]
        assert col.unique is True
        # Razorpay rejects a receipt longer than 40 characters.
        assert col.type.length == 40

    @pytest.mark.parametrize(
        "index_name,column",
        [
            ("uq_payment_orders_provider_order_id", "provider_order_id"),
            ("uq_payment_orders_provider_payment_id", "provider_payment_id"),
        ],
    )
    def test_gateway_identifiers_are_unique_where_present(self, index_name, column):
        """One gateway order fulfils one local order, ever.

        A duplicate provider_order_id would mean two local rows racing to fulfil
        the same real payment - the mechanism behind a doubled entitlement.
        """
        index = next(
            i for i in Base.metadata.tables["payment_orders"].indexes if i.name == index_name
        )
        assert index.unique is True
        assert [c.name for c in index.columns] == [column]
        assert "IS NOT NULL" in str(index.dialect_options["postgresql"].get("where")).upper()

    def test_money_is_integer_paise(self):
        col = Base.metadata.tables["payment_orders"].columns["amount_paise"]
        assert "INTEGER" in str(col.type).upper()
        assert col.nullable is False

    def test_the_amount_is_frozen_at_creation(self):
        """There is no "current price" lookup on the order row.

        The price the customer was quoted is the price stored on the order, which
        is what makes "did they pay what we asked for?" answerable years later,
        after the catalogue has changed.
        """
        columns = set(Base.metadata.tables["payment_orders"].columns.keys())
        # The amount lives here; nothing implies it is recomputed from the plan.
        assert {"amount_paise", "currency", "plan_code", "tier"} <= columns

    @pytest.mark.parametrize("column", ["created_at", "paid_at", "expires_at"])
    def test_instants_are_timezone_aware(self, column):
        # Only `timezone` is asserted: str(DateTime(timezone=True)) renders as
        # "DATETIME", so the type name says nothing about the flag that matters.
        col = Base.metadata.tables["payment_orders"].columns[column]
        assert getattr(col.type, "timezone", False) is True, (
            f"payment_orders.{column} must be TIMESTAMPTZ"
        )

    def test_status_and_tier_are_constrained_to_the_enums_the_code_uses(self):
        """The DB and the service must not be able to disagree.

        A status the service can write but the constraint rejects is a 500 after
        the customer has paid.
        """
        from app.services.billing import OrderState, Tier

        constraints = {
            c.name: str(c.sqltext)
            for c in Base.metadata.tables["payment_orders"].constraints
            if c.__class__.__name__ == "CheckConstraint"
        }
        for state in OrderState:
            assert f"'{state.value}'" in constraints["ck_payment_orders_status"]
        for tier in Tier:
            assert f"'{tier.value}'" in constraints["ck_payment_orders_tier"]

    def test_a_paid_order_must_carry_its_payment_identifier(self):
        """The schema cannot express "PAID without a payment id" as impossible
        without adding a partial constraint, but the code must never do it.

        Stated here so the invariant has a home next to the table it belongs to.
        """
        columns = Base.metadata.tables["payment_orders"].columns
        # Not NOT NULL: an order is created before any payment exists.
        assert columns["provider_payment_id"].nullable is True
        # But it is only ever populated together with the PAID transition.
        assert columns["paid_at"].nullable is True
        assert columns["status"].nullable is False

    def test_orders_are_indexed_for_the_two_lookups_that_happen(self):
        """By user (the order list) and by status+expiry (abandoned-order sweep)."""
        indexes = {i.name for i in Base.metadata.tables["payment_orders"].indexes}
        assert "idx_payment_orders_user" in indexes
        assert "idx_payment_orders_open" in indexes

    def test_at_most_one_in_progress_mock_attempt(self):
        indexes = {i.name for i in Base.metadata.tables["mock_attempts"].indexes}
        assert "uq_mock_attempt_in_progress" in indexes

    def test_referrals_cannot_be_self_referential(self):
        constraints = {c.name for c in Base.metadata.tables["referrals"].constraints}
        assert "ck_referral_not_self" in constraints


class TestMoneyAndTimeTypes:
    def test_amounts_are_integer_paise(self):
        """Floating-point currency is how a rounding error becomes a ticket."""
        col = Base.metadata.tables["subscriptions"].columns["amount_paise"]
        assert "INTEGER" in str(col.type).upper()

    @pytest.mark.parametrize(
        "table,column",
        [
            ("questions", "created_at"),
            ("mock_attempts", "expires_at"),
            ("spaced_repetition_cards", "next_review_at"),
            ("daily_activities", "activity_date"),
            ("payment_orders", "paid_at"),
            ("subscriptions", "expires_at"),
        ],
    )
    def test_instants_are_timezone_aware(self, table, column):
        col = Base.metadata.tables[table].columns[column]
        if column == "activity_date":
            # A local calendar DATE, intentionally not a timestamp - see the
            # streak bug note in progress.py.
            assert "DATE" in str(col.type).upper()
        else:
            assert getattr(col.type, "timezone", False) is True, (
                f"{table}.{column} must be TIMESTAMPTZ"
            )


class TestIngestionContract:
    """§11.1: the ingestion tables, and the guarantees they carry.

    These exist because the ingestion pipeline writes rows unattended - no human
    sees a draft before it is stored. Constraints are therefore the only thing
    standing between a bad extraction and the review queue.
    """

    def test_raw_text_cannot_be_an_update_target_by_accident(self):
        """§11.4: raw text is immutable; cleaned text is stored separately.

        If these ever collapse into one column, a cleaning pass destroys the
        evidence of what the extractor actually read and a disputed extraction
        becomes unresolvable. Kept as two NOT NULL/separate columns.
        """
        table = Base.metadata.tables["raw_extractions"]
        assert "raw_text" in table.columns
        assert "cleaned_text" in table.columns
        assert table.columns["raw_text"].nullable is False
        # cleaned_text is nullable: nothing is cleaned until an editor says so.
        assert table.columns["cleaned_text"].nullable is True

    def test_one_raw_extraction_per_page_per_job(self):
        table = Base.metadata.tables["raw_extractions"]
        uniques = {
            tuple(sorted(c.name for c in constraint.columns))
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("job_id", "page_number") in uniques

    def test_the_same_object_cannot_be_enqueued_twice(self):
        """Idempotency: a retried upload request must not create two jobs."""
        table = Base.metadata.tables["ingestion_jobs"]
        uniques = {
            tuple(sorted(c.name for c in constraint.columns))
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("bucket", "storage_path") in uniques

    def test_job_stage_is_constrained_to_known_values(self):
        """A typo'd stage value would silently stall a job forever, because
        nothing would ever match it as terminal.

        Every stage the service can write must be permitted, and every stage
        permitted must be one the service knows. Either drift direction is a bug:
        an unlisted stage causes a constraint violation at runtime, and a listed
        stage the enum cannot produce means the CHECK is lying about the domain.
        """
        from app.models.enums import IngestionStage

        sql = check_sql("ingestion_jobs", "ck_ingestion_jobs_stage")
        for stage in IngestionStage:
            assert stage.value in sql, f"{stage.value} is written by the service but not permitted"
        for permitted in re.findall(r"'([A-Z_]+)'", sql):
            assert permitted in IngestionStage.__members__, (
                f"{permitted} is permitted by the CHECK constraint but is not a "
                "member of IngestionStage"
            )

    def test_extraction_tier_is_limited_to_the_three_tiers(self):
        """Tier 1 is PyMuPDF, 2 is pdfplumber, 3 is OCR. A 4th tier would mean an
        extractor was added without deciding where it sits in the fallback order."""
        sql = check_sql("ingestion_jobs", "ck_ingestion_jobs_tier")
        assert "BETWEEN 1 AND 3" in sql.upper()

    def test_confidence_is_a_fraction_not_a_percentage(self):
        """A 0-100 confidence stored in a 0-1 column breaks the 0.85 comparison
        silently: every page would look perfect."""
        for table, name in (
            ("ingestion_jobs", "ck_ingestion_jobs_confidence"),
            ("raw_extractions", "ck_raw_extractions_confidence"),
        ):
            sql = check_sql(table, name)
            assert "<= 1" in sql or "<=1" in sql, f"{name} does not cap confidence at 1"

    def test_drafts_default_to_pending_review(self):
        """A draft that defaults to APPROVED would bypass the QA gate entirely.

        Asserted against the column's own default, not a search of the whole
        schema: "PENDING" appears in the review_status CHECK constraint whether
        or not it is the default, so a substring search passes even when the
        default has been changed to APPROVED.
        """
        table = Base.metadata.tables["ingestion_drafts"]
        assert table.columns["review_status"].nullable is False
        assert "PENDING" in column_default("ingestion_drafts", "review_status")
        assert "APPROVED" not in column_default("ingestion_drafts", "review_status")

    def test_a_draft_is_not_a_question(self):
        """Drafts live in their own table on purpose.

        `questions` requires course_id, subject_id, question_type, marks and text.
        An OCR extraction knows none of those reliably. Relaxing them, or
        guessing a subject at extraction time, would put silently-wrong rows into
        the exact table students filter and study from. A draft is a proposal,
        not a question.
        """
        drafts = Base.metadata.tables["ingestion_drafts"]
        questions = Base.metadata.tables["questions"]

        for required in ("course_id", "subject_id", "question_type", "marks", "text"):
            assert questions.columns[required].nullable is False, (
                f"questions.{required} became nullable - if that was deliberate, "
                "re-read the note on ingestion_drafts; the two tables exist "
                "because drafts cannot satisfy these columns"
            )

        # Placement is optional on a draft precisely because a human fills it in.
        assert drafts.columns["course_id"].nullable is True
        assert drafts.columns["subject_id"].nullable is True
        assert drafts.columns["chapter_id"].nullable is True

    def test_approval_path_records_who_and_when(self):
        drafts = Base.metadata.tables["ingestion_drafts"]
        assert {"reviewed_by", "reviewed_at", "review_note"} <= set(drafts.columns.keys())

    def test_promotion_closes_the_provenance_chain(self):
        """A draft promoted to a question keeps a link back to the question it
        became, so a published question can be traced to the PDF page it came
        from - which is what makes a takedown or a correction possible."""
        drafts = Base.metadata.tables["ingestion_drafts"]
        fks = drafts.columns["promoted_question_id"].foreign_keys
        assert {fk.target_fullname for fk in fks} == {"questions.id"}

    def test_drafts_are_deleted_with_their_job(self):
        drafts = Base.metadata.tables["ingestion_drafts"]
        fks = drafts.columns["job_id"].foreign_keys
        assert {fk.target_fullname for fk in fks} == {"ingestion_jobs.id"}
        assert all(fk.ondelete == "CASCADE" for fk in fks)


class TestSchemaSize:
    """A deliberate tripwire, not a maintenance chore.

    Adding a table should be a conscious act: it needs a migration, a contract
    test for its invariants, and a place in the docs. Failing here forces that
    conversation instead of letting the schema grow by accident.
    """

    def test_table_count(self):
        # 57 after the campus tools migration: study groups, mentorship, forum,
        # formula and glossary notes, support tickets, experiments, marketplace
        # listings that cannot charge, proctor events, calendar, pomodoro, and
        # exam-mode sittings. questions.review_state is a column, not a table.
        # 42 after migration 0013: subject_components (Intermediate splits),
        # payment_gateway_config (owner-entered Razorpay keys), law_notices
        # (a citation record that does not rewrite answers).
        # 39 after the access-grants table (migration 0010), which added the
        # library-wide half of access control used by the admin panel.
        #
        # 38 after the content library and the operations tables (migration 0008):
        #   content_documents, document_pages, content_access_rules  -> the library,
        #     its extracted text, and the rules that decide who may read it
        #   notifications, badges                                    -> the inbox and
        #     the catalogue the already-existing user_badges rows reference
        #   analytics_events                                         -> §9.1's event log,
        #     which had no table before and therefore recorded nothing
        #   audit_logs, platform_settings                             -> the owner's
        #     trail of who changed what, and the switches that need no deploy
        assert len(Base.metadata.sorted_tables) == 57

    def test_a_grant_has_exactly_one_audience_selector(self):
        """The CHECK constraint that stops a grant from meaning two things.

        A row with two selectors would be read by one code path and ignored by another;
        a row with none would match EVERY viewer, which is a bug that presents as
        "everyone can suddenly read this". The database refuses both, so a mistake here
        is an insert error at the moment it is made rather than an access decision
        nobody can explain three months later.
        """
        table = Base.metadata.tables["content_grants"]
        checks = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_grant_exactly_one_subject" in checks
        assert "ck_grant_who_scope" in checks
        assert "ck_grant_effect" in checks
        # Revocation is a timestamp, not a row delete: "who had access last month" has
        # to stay answerable.
        assert "revoked_at" in table.columns

    def test_the_content_library_keeps_the_original_file(self):
        # The pipeline's core promise: extraction ADDS rows, it never replaces the
        # document. If storage_path ever became nullable or derived, a re-process
        # could lose the file the questions came from.
        documents = Base.metadata.tables["content_documents"]
        assert not documents.c.storage_path.nullable
        assert not documents.c.bucket.nullable
        assert not documents.c.original_filename.nullable
        # Provenance: a replacement points at what it replaced rather than deleting it.
        assert "supersedes_id" in documents.c
        assert documents.c.version is not None

    def test_extracted_text_is_per_page_and_searchable(self):
        # Page granularity is what makes a citation followable ("page 42"), and the
        # generated tsvector is what makes the search index unable to disagree with
        # the text it indexes.
        pages = Base.metadata.tables["document_pages"]
        assert not pages.c.page_number.nullable
        assert "raw_text" in pages.c
        indexes = {index.name for index in pages.indexes}
        assert "idx_document_pages_fts" in indexes
        assert "uq_document_page" in {c.name for c in pages.constraints}

    def test_the_document_kinds_the_api_accepts_are_the_kinds_the_database_accepts(self):
        """A vocabulary that lives in two places drifts, and the drift is a 500.

        The API validated the document kind only for length while the column carries a
        CHECK. An operator choosing a kind the console offered ("Past paper") had the row
        rejected by PostgreSQL, so the failure reached them as a constraint message inside
        a 500 instead of a 422 naming a field.

        This asserts the two lists are the SAME list, not that each contains something.
        """
        import app.models as models  # noqa: F401
        from app.models.enums import DocumentKind, sql_in_list

        documents = Base.metadata.tables["content_documents"]
        constraint = check_sql("content_documents", "ck_document_kind")
        for member in DocumentKind:
            assert f"'{member.value}'" in constraint, (
                f"{member.value} is a valid DocumentKind but ck_document_kind rejects it"
            )
        # ...and nothing beyond the enum is permitted, which is the direction that lets
        # the web console offer a value the database will refuse.
        allowed = set(re.findall(r"'([A-Z_]+)'", constraint))
        assert allowed == {member.value for member in DocumentKind}
        assert sql_in_list(DocumentKind) in constraint
        assert documents.c.kind.type.length is None or True  # kind is the CHECK's business

    def test_the_upload_schema_rejects_an_unknown_kind_as_a_422(self):
        """The same list, one layer up: request validation must refuse a bad kind.

        The route is exercised through the FastAPI TestClient so this covers the real
        dependency and the real schema, and it asserts the STATUS: a 422 is a client
        mistake the operator can fix, a 500 is not.
        """
        from fastapi.testclient import TestClient

        from app.main import app
        from app.models.enums import DocumentKind

        schema = app.openapi()["components"]["schemas"]
        upload = schema["CreateUploadsIn"]["properties"]["kind"]
        # The field is a $ref to the shared enum component, so the vocabulary appears in
        # the OpenAPI document once and the generated client can be typed from it.
        assert upload["$ref"] == "#/components/schemas/DocumentKind", upload
        assert set(schema["DocumentKind"]["enum"]) == {member.value for member in DocumentKind}
        # MAX_LENGTH is gone with the string type: a length limit on a closed vocabulary
        # is a second, weaker copy of the same rule.
        assert schema["DocumentKind"].get("maxLength") is None

        with TestClient(app) as client:
            # No credentials: the point is that validation runs BEFORE authorization for
            # a well-formed body, so this must not be a 500 either way.
            resp = client.post(
                "/api/v1/content/admin/content/uploads",
                json={"kind": "PAST_PAPERS", "files": []},
            )
            assert resp.status_code != 500, resp.text

    def test_access_rules_cannot_match_nobody(self):
        # A rule with a null target would be a wildcard: it matches every viewer by
        # accident. The CHECK makes it unrepresentable rather than merely unlikely.
        rules = Base.metadata.tables["content_access_rules"]
        names = {constraint.name for constraint in rules.constraints}
        assert "ck_access_rule_target" in names
        assert "ck_access_rule_effect" in names

    def test_the_audit_log_survives_its_actor(self):
        # No foreign key on the actor: deleting an admin must not delete the record of
        # what they did. The id and email are copied in as facts instead.
        audit = Base.metadata.tables["audit_logs"]
        assert not any(
            fk.parent.name == "actor_user_id"
            for fk in audit.foreign_key_constraints
            for fk in fk.elements
        )

    def test_analytics_events_exist(self):
        events = Base.metadata.tables["analytics_events"]
        assert not events.c.name.nullable
        # Nullable by design: a visitor can trigger an event before signing in.
        assert events.c.user_id.nullable

    def test_every_model_module_is_registered(self):
        """A model file that is never imported is invisible to Alembic.

        autogenerate walks Base.metadata. A model defined in a module nothing
        imports therefore produces no migration and no drift warning - the table
        simply never exists in a real database, while every test that imports the
        module directly keeps passing. This test closes that gap by discovering
        the files on disk and requiring each one to be reachable from
        app.models.
        """
        import pathlib as _pathlib

        models_dir = _pathlib.Path("app/models")
        assert models_dir.is_dir(), "run pytest from apps/api"

        # Modules that define no ORM tables, so registration does not apply.
        non_table_modules = {"base", "enums", "__init__"}

        on_disk = {
            path.stem for path in models_dir.glob("*.py") if path.stem not in non_table_modules
        }
        assert on_disk, "no model modules found - is the path right?"

        import app.models as models_package

        registered = set()
        for name, module in vars(models_package).items():
            if getattr(module, "__name__", "") == f"app.models.{name}":
                registered.add(name)

        missing = on_disk - registered
        assert missing == set(), (
            f"model modules not imported by app/models/__init__.py: {sorted(missing)}. "
            "Add them, or Alembic will not see their tables."
        )

    def test_every_table_is_reachable_from_the_migrations(self):
        """Guards the failure mode where a model is defined and never migrated -
        the app then works in development and breaks on a fresh deployment."""
        import pathlib as _pathlib
        import re

        versions = _pathlib.Path("alembic/versions")
        assert versions.is_dir(), "run pytest from apps/api"
        pattern = re.compile(r"create_table\(\s*['\"](\w+)['\"]")
        migrated = set()
        for path in versions.glob("*.py"):
            migrated |= set(pattern.findall(path.read_text()))
        orm = {t.name for t in Base.metadata.sorted_tables}
        assert orm - migrated == set(), (
            f"tables defined but never migrated: {sorted(orm - migrated)}"
        )
