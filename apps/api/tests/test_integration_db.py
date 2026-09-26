"""Tests that require a real PostgreSQL.

WHY THIS FILE EXISTS SEPARATELY

Everything else in this suite runs against pure functions or in-memory doubles. That
is fast, and it verifies nothing about the database: not that the migrations apply,
not that the ORM and the schema agree, not that a CHECK constraint actually rejects
the row a repository might write. A test double accepts whatever Python sends it.

These tests run only when ``TEST_DATABASE_URL`` is set, pointing at a DISPOSABLE
database. There is deliberately no fallback to ``DATABASE_URL``: a fallback means
one careless environment variable away from a test truncating a real database.

    createdb caprep_test
    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/caprep_test \\
      DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/caprep_test \\
      pytest tests/test_integration_db.py

The schema must already be migrated (``alembic upgrade head``); the round-trip test
is the one exception and only runs against a database whose name ends in ``_test``.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import uuid

import pytest

asyncpg = pytest.importorskip("asyncpg")

from app.models import Base  # noqa: E402  (after the importorskip on purpose)

#: Name of the migrator's own bookkeeping table - the one table the ORM does not
#: declare and must not adopt.
ALEMBIC_TABLE = "alembic_version"

pytestmark = pytest.mark.usefixtures("db_available")


def _url() -> str | None:
    """asyncpg wants a plain DSN; the app's SQLAlchemy URL is normalised here."""
    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        return None
    for prefix in ("postgresql+psycopg://", "postgresql+asyncpg://", "postgres://"):
        if raw.startswith(prefix):
            return raw.replace(prefix, "postgresql://", 1)
    return raw


@pytest.fixture(scope="module")
def db_available() -> str:
    """Skip the whole module when there is no database, rather than erroring."""
    url = _url()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set - database tests skipped")
    try:
        import asyncio

        async def _ping() -> None:
            conn = await asyncpg.connect(url)
            try:
                await conn.execute("SELECT 1")
            finally:
                await conn.close()

        asyncio.run(_ping())
    except Exception as exc:  # noqa: BLE001 - any failure means "cannot test"
        pytest.skip(f"database unreachable: {type(exc).__name__}: {exc}")
    return url


@pytest.fixture
def conn(db_available: str):
    """A connection whose every write is rolled back.

    Each test runs inside `BEGIN ... ROLLBACK`, so the constraint-violation tests
    can insert freely without leaving rows behind for the next test to trip over.
    The blocks are `async with conn.transaction()` nested inside this outer one, so
    a violation rolls back only the inner block.
    """
    import asyncio

    class _Connection:
        def __init__(self) -> None:
            self.loop = asyncio.new_event_loop()
            self.connection = self.loop.run_until_complete(asyncpg.connect(db_available))
            self.transaction = self.connection.transaction()
            self.loop.run_until_complete(self.transaction.start())

        def run(self, coro):
            return self.loop.run_until_complete(coro)

        def close(self) -> None:
            # A test may have ended the transaction itself; rolling back an
            # already-closed transaction is not an error worth reporting here.
            with contextlib.suppress(Exception):
                self.loop.run_until_complete(self.transaction.rollback())
            self.loop.run_until_complete(self.connection.close())
            self.loop.close()

    holder = _Connection()
    try:
        yield holder
    finally:
        holder.close()


def _run(conn_obj, coro):
    return conn_obj.run(coro)


class _Savepoint:
    """`with savepoint(conn):` - runs the block in a savepoint that is always rolled back.

    asyncpg's Transaction object is NOT a context manager, and a statement that
    violates a constraint leaves the surrounding transaction aborted: every later
    statement fails with "current transaction is aborted" and the test that should
    have proved a constraint works instead proves the harness is broken.
    """

    def __init__(self, conn_obj) -> None:
        self._conn_obj = conn_obj
        self._tx = None

    def __enter__(self) -> _Savepoint:
        self._tx = self._conn_obj.connection.transaction()
        self._conn_obj.run(self._tx.start())
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._conn_obj.run(self._tx.rollback())
        return False


def savepoint(conn_obj) -> _Savepoint:
    return _Savepoint(conn_obj)


# --------------------------------------------------------------- migrations


class TestMigrations:
    def test_every_declared_table_exists_in_the_database(self, conn) -> None:
        rows = _run(
            conn,
            conn.connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"),
        )
        present = {row["tablename"] for row in rows} - {ALEMBIC_TABLE}
        declared = set(Base.metadata.tables)

        # Both directions are asserted. "In the ORM but not in the database" is a
        # forgotten migration; "in the database but not in the ORM" is a table
        # somebody created by hand, which the next autogenerate would DROP.
        missing = declared - present
        undeclared = present - declared
        assert missing == set(), f"declared but missing from the database: {missing}"
        assert undeclared == set(), f"in the database but not declared: {undeclared}"

    def test_the_schema_has_no_drift_from_the_models(self, db_available: str) -> None:
        """`alembic check` compares metadata against the live database.

        This is the only check that catches a model edited without a migration, and
        the only one that sees the deliberately raw SQL (the GIN and trigram
        indexes), because those exist in the database and cannot be expressed as
        SQLAlchemy `Index` objects.
        """
        env = {**os.environ, "DATABASE_URL": db_available}
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert "No new upgrade operations detected" in proc.stdout + proc.stderr, (
            "alembic detected drift between the models and the database:\n"
            + proc.stdout
            + proc.stderr
        )

    def test_downgrade_and_upgrade_return_to_the_same_schema(self, conn, db_available: str) -> None:
        """The rollback path is part of the migration, not an afterthought.

        DESTRUCTIVE, so it is gated on the database being a throwaway: the name must
        end in `_test`. A `downgrade base` against a development database would drop
        every row in it.
        """
        if not db_available.rstrip("/").endswith("_test"):
            pytest.skip("destructive round trip needs a database named *_test")

        env = {**os.environ, "DATABASE_URL": db_available}
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def alembic(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-m", "alembic", *args],
                capture_output=True,
                text=True,
                env=env,
                cwd=cwd,
            )

        tables = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        before = _run(conn, conn.connection.fetch(tables))
        expected = {row["tablename"] for row in before}

        # Commit the read transaction first: ALTER TABLE from another connection
        # would block behind it otherwise.
        _run(conn, conn.transaction.rollback())

        assert alembic("downgrade", "base").returncode == 0
        upgraded = alembic("upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

        after = _run(conn, conn.connection.fetch(tables))
        assert {row["tablename"] for row in after} == expected


# ------------------------------------------------------------------- search


class TestFullTextSearch:
    """The search feature is the reason PostgreSQL was chosen over SQLite."""

    def test_a_gin_index_over_to_tsvector_exists(self, conn) -> None:
        rows = _run(
            conn,
            conn.connection.fetch(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND indexdef ILIKE '%using gin%'"
            ),
        )
        definitions = " ".join(row["indexdef"] for row in rows)
        assert "to_tsvector" in definitions, definitions

    def test_the_search_query_can_actually_use_the_index(self, conn) -> None:
        """EXPLAIN with sequential scans disabled.

        The index expression has to match the query EXACTLY - `to_tsvector('english',
        COALESCE(search_text, text))`, not `to_tsvector(text)`. A mismatch still
        returns correct rows, which is why the bug survives: it only shows up as a
        sequential scan over the whole question bank, and only at scale.
        """
        _run(conn, conn.connection.execute("SET LOCAL enable_seqscan = off"))
        plan = _run(
            conn,
            conn.connection.fetch(
                "EXPLAIN SELECT id FROM questions "
                "WHERE to_tsvector('english', COALESCE(search_text, text)) "
                "@@ websearch_to_tsquery('english', 'depreciation')"
            ),
        )
        rendered = "\n".join(row["QUERY PLAN"] for row in plan)
        assert "idx_questions_fts" in rendered, rendered

    def test_a_phrase_matches_only_the_documents_that_contain_it(self, conn) -> None:
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        verifier = _run(conn, _user(conn))
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO questions (id, course_id, subject_id, text, question_type, marks, "
                "correct_answer, status, verified_by) "
                "VALUES ($1, $2, $3, $4, 'MCQ', 2, 'A', 'PUBLISHED', $5)",
                uuid.uuid4(),
                course_id,
                subject_id,
                "Compute depreciation under the straight line method",
                verifier,
            ),
        )
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO questions (id, course_id, subject_id, text, question_type, marks, "
                "correct_answer, status, verified_by) "
                "VALUES ($1, $2, $3, $4, 'MCQ', 2, 'B', 'PUBLISHED', $5)",
                uuid.uuid4(),
                course_id,
                subject_id,
                "State the provisions on amalgamation",
                verifier,
            ),
        )

        found = _run(
            conn,
            conn.connection.fetch(
                "SELECT text FROM questions "
                "WHERE to_tsvector('english', COALESCE(search_text, text)) "
                "@@ websearch_to_tsquery('english', 'depreciation')"
            ),
        )
        assert len(found) == 1
        assert "depreciation" in found[0]["text"].lower()

        nothing = _run(
            conn,
            conn.connection.fetch(
                "SELECT text FROM questions "
                "WHERE to_tsvector('english', COALESCE(search_text, text)) "
                "@@ websearch_to_tsquery('english', 'xylophone')"
            ),
        )
        assert nothing == []

    def test_search_text_is_preferred_over_the_rendered_text(self, conn) -> None:
        """`COALESCE(search_text, text)` is what makes stemming/expansion possible.

        A row with a curated `search_text` must match on the curated form, which is
        how a question whose text contains LaTeX or a formula stays searchable by
        its plain-language keywords.
        """
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        verifier = _run(conn, _user(conn))
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO questions (id, course_id, subject_id, text, search_text, "
                "question_type, marks, correct_answer, status, verified_by) "
                "VALUES ($1, $2, $3, $4, $5, 'MCQ', 2, 'C', 'PUBLISHED', $6)",
                uuid.uuid4(),
                course_id,
                subject_id,
                "Compute $\\frac{D}{W}$",
                "inventory turnover ratio",
                verifier,
            ),
        )
        found = _run(
            conn,
            conn.connection.fetch(
                "SELECT id FROM questions WHERE to_tsvector('english', "
                "COALESCE(search_text, text)) @@ websearch_to_tsquery('english', 'inventory')"
            ),
        )
        assert len(found) == 1


# ------------------------------------------------------------- constraints


async def _course_and_subject(conn_obj):
    """One course + subject for tests that need real foreign keys."""
    course_id, subject_id = uuid.uuid4(), uuid.uuid4()
    base = f"T{uuid.uuid4().hex[:6].upper()}"
    await conn_obj.connection.execute(
        "INSERT INTO courses (id, code, name, level) VALUES ($1, $2, $3, 'INTERMEDIATE')",
        course_id,
        f"C{base}",
        "Integration Course",
    )
    await conn_obj.connection.execute(
        "INSERT INTO subjects (id, course_id, code, name) VALUES ($1, $2, $3, $4)",
        subject_id,
        course_id,
        f"S{base}",
        "Integration Subject",
    )
    return course_id, subject_id


async def _user(conn_obj) -> uuid.UUID:
    user_id = uuid.uuid4()
    await conn_obj.connection.execute(
        "INSERT INTO users (id, auth_user_id, email) VALUES ($1, $2, $3)",
        user_id,
        f"uid_{uuid.uuid4().hex}",
        f"{uuid.uuid4().hex[:8]}@example.com",
    )
    return user_id


class TestConstraints:
    """Each of these is a guarantee the application relies on being enforced.

    They are asserted against the DATABASE, not the repository, because the
    repository is what might later be changed by someone who assumes the column is
    already safe.
    """

    def test_only_one_active_subscription_per_user(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        insert = (
            "INSERT INTO subscriptions (id, user_id, tier, status, started_at, "
            "expires_at) VALUES ($1, $2, 'PREMIUM', $3, now(), now() + interval '1 year')"
        )
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, "ACTIVE"))

        with pytest.raises(asyncpg.UniqueViolationError):
            with savepoint(conn):
                _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, "ACTIVE"))

        # A dead subscription is history and must NOT be blocked by the index: a
        # user who cancels and re-subscribes has two rows, one of them CANCELLED.
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, "CANCELLED"))

        count = _run(
            conn,
            conn.connection.fetchval(
                "SELECT count(*) FROM subscriptions WHERE user_id = $1", user_id
            ),
        )
        assert count == 2

    def test_a_payment_event_id_can_only_be_stored_once(self, conn) -> None:
        insert = (
            "INSERT INTO payment_events (id, provider, event_id, event_type, "
            "signature_verified, payload) VALUES ($1, 'razorpay', $2, 'payment.captured', "
            "true, '{}'::jsonb)"
        )
        event_id = f"evt_{uuid.uuid4().hex}"
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), event_id))
        with pytest.raises(asyncpg.UniqueViolationError):
            with savepoint(conn):
                # The webhook idempotency guarantee is this index, not the code
                # path: a retry that races the first delivery still loses.
                _run(conn, conn.connection.execute(insert, uuid.uuid4(), event_id))

    def test_auth_user_id_is_unique_across_accounts(self, conn) -> None:
        auth_id = f"uid_{uuid.uuid4().hex}"
        insert = "INSERT INTO users (id, auth_user_id, email) VALUES ($1, $2, $3)"
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), auth_id, "a@example.com"))
        with pytest.raises(asyncpg.UniqueViolationError):
            with savepoint(conn):
                # Two `users` rows for one authenticated identity would fork a student's
                # progress in half at the next sign-in.
                _run(conn, conn.connection.execute(insert, uuid.uuid4(), auth_id, "b@example.com"))

    def test_a_published_question_must_have_a_verifier(self, conn) -> None:
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        with pytest.raises(asyncpg.CheckViolationError):
            with savepoint(conn):
                _run(
                    conn,
                    conn.connection.execute(
                        "INSERT INTO questions (id, course_id, subject_id, text, question_type, "
                        "marks, status) VALUES ($1, $2, $3, $4, 'MCQ', 2, 'PUBLISHED')",
                        uuid.uuid4(),
                        course_id,
                        subject_id,
                        "Unverified but published",
                    ),
                )

    def test_an_objective_question_must_have_an_answer(self, conn) -> None:
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        with pytest.raises(asyncpg.CheckViolationError):
            with savepoint(conn):
                # A MCQ with no correct answer is ungradeable, and the student sees
                # the failure as "the app marked me wrong" rather than as bad data.
                _run(
                    conn,
                    conn.connection.execute(
                        "INSERT INTO questions (id, course_id, subject_id, text, question_type, "
                        "marks, status) VALUES ($1, $2, $3, $4, 'MCQ', 2, 'DRAFT')",
                        uuid.uuid4(),
                        course_id,
                        subject_id,
                        "MCQ with no answer",
                    ),
                )

    def test_a_question_cannot_be_worth_zero_marks(self, conn) -> None:
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        with pytest.raises(asyncpg.CheckViolationError):
            with savepoint(conn):
                _run(
                    conn,
                    conn.connection.execute(
                        "INSERT INTO questions (id, course_id, subject_id, text, question_type, "
                        "marks, status) VALUES ($1, $2, $3, $4, 'DESCRIPTIVE', 0, 'DRAFT')",
                        uuid.uuid4(),
                        course_id,
                        subject_id,
                        "Zero mark question",
                    ),
                )

    def test_the_ledger_refuses_a_zero_point_entry(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        with pytest.raises(asyncpg.CheckViolationError):
            with savepoint(conn):
                _run(
                    conn,
                    conn.connection.execute(
                        "INSERT INTO points_ledger (id, user_id, points, reason) "
                        "VALUES ($1, $2, 0, 'QUESTION_CORRECT')",
                        uuid.uuid4(),
                        user_id,
                    ),
                )

    def test_the_ledger_refuses_an_unknown_reason(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        with pytest.raises(asyncpg.CheckViolationError):
            with savepoint(conn):
                # The reason vocabulary is closed, so a typo'd award fails loudly at
                # write time instead of quietly entering the analytics.
                _run(
                    conn,
                    conn.connection.execute(
                        "INSERT INTO points_ledger (id, user_id, points, reason) "
                        "VALUES ($1, $2, 10, 'QUESTION_CORRECTED')",
                        uuid.uuid4(),
                        user_id,
                    ),
                )

    def test_an_idempotency_key_can_carry_one_award_only(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        key = f"k_{uuid.uuid4().hex}"
        insert = (
            "INSERT INTO points_ledger (id, user_id, points, reason, idempotency_key) "
            "VALUES ($1, $2, 10, 'QUESTION_CORRECT', $3)"
        )
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, key))
        with pytest.raises(asyncpg.UniqueViolationError):
            with savepoint(conn):
                # This is what stops a student farming points by re-answering the
                # same question correctly; the code path is only a friendly check.
                _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, key))

    def test_one_progress_row_per_user_and_question(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        question_id = uuid.uuid4()
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO questions (id, course_id, subject_id, text, question_type, marks, "
                "correct_answer, status) VALUES ($1, $2, $3, $4, 'MCQ', 1, 'A', 'DRAFT')",
                question_id,
                course_id,
                subject_id,
                "Answerable question",
            ),
        )
        insert = (
            "INSERT INTO user_question_progress (id, user_id, question_id, attempts_count, "
            "correct_count) VALUES ($1, $2, $3, 1, 1)"
        )
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, question_id))
        with pytest.raises(asyncpg.UniqueViolationError):
            with savepoint(conn):
                _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, question_id))

    def test_correct_count_can_never_exceed_attempts(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        question_id = uuid.uuid4()
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO questions (id, course_id, subject_id, text, question_type, marks, "
                "correct_answer, status) VALUES ($1, $2, $3, $4, 'MCQ', 1, 'A', 'DRAFT')",
                question_id,
                course_id,
                subject_id,
                "Answerable question",
            ),
        )
        with pytest.raises(asyncpg.CheckViolationError):
            with savepoint(conn):
                # An impossible ratio would render as ">100% accuracy" on the
                # dashboard, which students read as the app being broken.
                _run(
                    conn,
                    conn.connection.execute(
                        "INSERT INTO user_question_progress (id, user_id, question_id, "
                        "attempts_count, correct_count) VALUES ($1, $2, $3, 2, 5)",
                        uuid.uuid4(),
                        user_id,
                        question_id,
                    ),
                )

    def test_one_daily_activity_row_per_user_and_day(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        insert = (
            "INSERT INTO daily_activities (id, user_id, activity_date, questions_attempted) "
            "VALUES ($1, $2, CURRENT_DATE, 5)"
        )
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id))
        with pytest.raises(asyncpg.UniqueViolationError):
            with savepoint(conn):
                # The streak is derived from distinct days; two rows for one day
                # would let a student "extend" a streak by reloading a page.
                _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id))

    def test_a_spaced_repetition_card_is_unique_per_question(self, conn) -> None:
        user_id = _run(conn, _user(conn))
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        question_id = uuid.uuid4()
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO questions (id, course_id, subject_id, text, question_type, marks, "
                "correct_answer, status) VALUES ($1, $2, $3, $4, 'MCQ', 1, 'A', 'DRAFT')",
                question_id,
                course_id,
                subject_id,
                "Queued question",
            ),
        )
        insert = (
            "INSERT INTO spaced_repetition_cards (id, user_id, question_id, next_review_at) "
            "VALUES ($1, $2, $3, now())"
        )
        _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, question_id))
        with pytest.raises(asyncpg.UniqueViolationError):
            with savepoint(conn):
                _run(conn, conn.connection.execute(insert, uuid.uuid4(), user_id, question_id))

    def test_deleting_a_user_takes_their_learning_history_with_them(self, conn) -> None:
        """FK cascades are a data-protection feature, not a tidiness one."""
        user_id = _run(conn, _user(conn))
        course_id, subject_id = _run(conn, _course_and_subject(conn))
        question_id = uuid.uuid4()
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO questions (id, course_id, subject_id, text, question_type, marks, "
                "correct_answer, status) VALUES ($1, $2, $3, $4, 'MCQ', 1, 'A', 'DRAFT')",
                question_id,
                course_id,
                subject_id,
                "Attempted question",
            ),
        )
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO practice_attempts "
                "(id, user_id, question_id, chosen_option, is_correct) "
                "VALUES ($1, $2, $3, 'A', true)",
                uuid.uuid4(),
                user_id,
                question_id,
            ),
        )
        _run(conn, conn.connection.execute("DELETE FROM users WHERE id = $1", user_id))
        remaining = _run(
            conn,
            conn.connection.fetchval(
                "SELECT count(*) FROM practice_attempts WHERE user_id = $1", user_id
            ),
        )
        assert remaining == 0


class TestServerDefaults:
    """The defaults live in the DATABASE, so a raw INSERT - a migration, a backfill,
    a psql script - produces the same row the ORM would.
    """

    def test_a_minimal_insert_gets_the_documented_defaults(self, conn) -> None:
        course_id = uuid.uuid4()
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO courses (id, code, name, level) VALUES ($1, $2, $3, 'FINAL')",
                course_id,
                f"C{uuid.uuid4().hex[:6].upper()}",
                "Defaults Course",
            ),
        )
        row = _run(
            conn,
            conn.connection.fetchrow(
                "SELECT syllabus_scheme, is_active, created_at, updated_at, effective_from "
                "FROM courses WHERE id = $1",
                course_id,
            ),
        )
        assert row["syllabus_scheme"] == "UNMAPPED"
        assert row["is_active"] is True
        assert row["created_at"] is not None
        assert row["updated_at"] is not None
        # Nullable with no default stays null: a default on a date column would
        # invent an effective-from that nobody chose.
        assert row["effective_from"] is None

    def test_a_chapter_defaults_to_a_five_percent_weight_and_two_hours(self, conn) -> None:
        _, subject_id = _run(conn, _course_and_subject(conn))
        chapter_id = uuid.uuid4()
        _run(
            conn,
            conn.connection.execute(
                "INSERT INTO chapters (id, subject_id, code, name) VALUES ($1, $2, $3, $4)",
                chapter_id,
                subject_id,
                f"CH{uuid.uuid4().hex[:6]}",
                "Defaults Chapter",
            ),
        )
        row = _run(
            conn,
            conn.connection.fetchrow(
                "SELECT weightage, estimated_minutes, sequence, is_active "
                "FROM chapters WHERE id = $1",
                chapter_id,
            ),
        )
        assert row["weightage"] == 5
        assert row["estimated_minutes"] == 120
        assert row["sequence"] == 0
        assert row["is_active"] is True
