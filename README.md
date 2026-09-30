# CA Prep Platform

Structured preparation for the ICAI Chartered Accountancy examinations: a
question bank, mock attempts, a document library, and a study planner.

A stateless FastAPI service over PostgreSQL and Redis, with a browser SPA in
front. The backend in this repository is complete and verified; the UI is not
built here.

## Documentation

| | |
|---|---|
| [01 — Overview](docs/01-overview.md) | what the product is, course levels, the learning loops |
| [02 — Architecture](docs/02-architecture.md) | service shape, the auth split, middleware order, module layout |
| [03 — Data Model](docs/03-data-model.md) | 59 tables, constraints, indexes, migrations |
| [04 — API Reference](docs/04-api-reference.md) | envelopes, keyset pagination, route namespaces |
| [05 — Development](docs/05-development.md) | local setup, test suites, quality gates |
| [06 — Deployment](docs/06-deployment.md) | Render blueprint, required configuration, smoke test |
| [07 — Security](docs/07-security.md) | rate limiting, fail-closed webhooks, RBAC, Sentry |
| [08 — Content Ops](docs/08-content-ops.md) | copyright posture, quality control, OCR ingestion |
| [09 — Roadmap](docs/09-roadmap.md) | what is built, what is not, what is next |

## Quick start

```bash
cd apps/api
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
pytest -x -q
```

Full instructions, including the PostgreSQL integration suite, are in
[05 — Development](docs/05-development.md).

## The published API contract

`apps/docs/api/openapi.json` is generated from the running application and
asserted byte-for-byte by `tests/test_api_contract.py`. If you change a route,
regenerate it in the same change.

## Licence

MIT. See [LICENSE](LICENSE).

Original study material only — see
[08 — Content Operations](docs/08-content-ops.md) for the copyright posture.
