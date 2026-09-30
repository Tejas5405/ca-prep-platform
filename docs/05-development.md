# Development

## Local setup

Requires **Python 3.12+** and a PostgreSQL 16 instance. Redis is optional
locally: without it, rate limiting fails closed on the fail-closed paths only.

```bash
cd apps/api
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp ../../.env.example .env          # local values; never commit a filled copy
```

`infra/docker/docker-compose.yml` brings up PostgreSQL and Redis.

## Tests

Two suites, and the split matters.

```bash
# Fast suite - no database required
pytest -x -q

# PostgreSQL integration suite
TEST_DATABASE_URL='postgresql://user@localhost:5432/caprep_v2_test' \
  pytest tests/integration -q
```

`TEST_DATABASE_URL` is preferred over `DATABASE_URL` so an integration run
cannot point at a database holding real data. `DATABASE_URL` is the CI
fallback, where the service container is disposable.

Integration tests are **skipped, not failed**, when no database is configured,
so the ordinary run stays green on a machine with nothing installed. A missing
`asyncpg` **fails** instead: a skipped database suite is a green run in which no
SQL was executed, and reporting that as "PostgreSQL unreachable" is actively
misleading.

A dedicated disposable database is required — the harness refuses to run against
anything that does not look like one.

## Quality gates

| Gate | Command | Bar |
|---|---|---|
| Lint | `ruff check app/ tests/` | clean |
| Format | `ruff format --check app/ tests/` | clean |
| Types | `scripts/mypy_ratchet.sh app/` | **≤ 35 errors** |
| Schema drift | `alembic check` | `No new upgrade operations detected` |
| Tests | `pytest -x -q` | all pass |

### The mypy ratchet

The count is a **ceiling that may fall, never rise silently**. If a change adds
errors the gate fails and says so. The fix is to repair the code or, if the
errors are genuinely unavoidable, raise the number deliberately in the same
commit and say why in the message.

This gate has already earned its place twice. It caught a destructive
`git checkout` that silently reverted an uncommitted fix, re-breaking a
`SupabaseAuthAdmin` bug that made role changes report `claimUpdated: false` on
every call. It also caught a `JSONB(astext_type=str)` mistake in a new model,
which was fixed rather than accommodated.

### alembic check

Run it against a real database. It is the only thing that sees a column or
index which exists in the database but not in the ORM — the case where the next
`--autogenerate` emits a migration that quietly drops it. That is not
hypothetical: it happened with the GIN indexes on `applicable_attempts`.

## Migrations

```bash
alembic revision --autogenerate -m "..."
alembic upgrade head
alembic downgrade -1 && alembic upgrade head    # prove it reverses
alembic check
```

Never edit an applied migration. The downgrade path is part of the contract, not
an afterthought.

## Conventions

- Line length 100, enforced by Ruff.
- No `# type: ignore` to silence a ratchet — repair or justify.
- Every `requires` dependency gets a comment explaining what breaks without it.
  Those comments are the only reason anyone still knows why Tesseract is
  installed.
