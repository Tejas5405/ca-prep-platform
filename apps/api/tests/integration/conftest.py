"""Fixtures for the tests that require a live PostgreSQL.

WHY THIS PACKAGE EXISTS

Until now, a live database had never been reachable from a development
environment, so the whole suite ran against either pure functions or hand-written
fakes. That split let three classes of defect survive:

  * migrations that were only ever rendered to SQL, never executed;
  * repositories asserted through a fake, which is a test of the fake;
  * ORM-versus-database drift, which only ``alembic check`` against a real server
    can see. (It has already found three: 30 columns whose server defaults lived
    only in the migrations, two raw-SQL indexes that autogenerate wanted to drop,
    and a redundant pair of unique objects on ``users.auth_user_id``.)

They are skipped, not failed, when no database is configured, so the ordinary run
stays green on a machine with nothing installed.

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/caprep_v2_test pytest

``TEST_DATABASE_URL`` is preferred over ``DATABASE_URL`` so an integration run
cannot accidentally point at a database with real data in it. ``DATABASE_URL`` is
the fallback for CI, where the service container is disposable.
"""

from __future__ import annotations

import pytest

from ._db import async_url, ping, run

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="session")
def database_url() -> str:
    """The test database, or a skip explaining what to set.

    A MISSING DRIVER IS NOT AN ABSENT DATABASE.

    This fixture used to catch every exception from ``ping`` and skip, which meant
    a machine without ``asyncpg`` installed skipped the entire database suite and
    reported it as green - 87 tests, no SQL executed, no warning. The skip message
    said "PostgreSQL unreachable", which was actively misleading: the server was
    running the whole time.

    So the two conditions are now separated. No URL, or a server that refuses the
    connection, is a legitimate skip on a machine that has no database. A missing
    package is a broken environment, and it fails.
    """
    try:
        import asyncpg  # noqa: F401
    except ModuleNotFoundError as exc:
        pytest.fail(
            "The integration suite needs asyncpg, which is not installed "
            f"({exc}). It is declared in apps/api/requirements-dev.txt. Install it "
            "rather than skipping: a skipped database suite is a green run in "
            "which no SQL was executed."
        )

    url = async_url()
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run the PostgreSQL integration tests")
    try:
        run(ping(url))
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"PostgreSQL unreachable: {type(exc).__name__}: {exc}")
    return url
