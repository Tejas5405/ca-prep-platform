"""DEV ONLY: serve the content console with a pre-authenticated admin principal.

WHY THIS EXISTS SEPARATELY

The console needs an authenticated caller, and the real one comes from Supabase
JWT verification. Adding a bypass to ``app/`` would put a permanent
authentication bypass in the production surface to serve a developer tool, so
the bypass lives HERE instead, in a file that is not imported by the API.

This mounts the real application object and overrides two FastAPI dependencies
in-process. Every route, permission check, service and query is the real code --
only the identity of the caller is fixed. Nothing here is imported by
``app.main``.

    python devtools/run_admin_ui.py            # http://127.0.0.1:8001

NEVER run this on a shared host, and never point it at a production database.
"""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path

import uvicorn
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from app.core.identity import get_current_user
from app.core.security import Principal, get_current_principal
from app.main import app
from app.models.user import User

DB = os.environ.get("DATABASE_URL", "postgresql+asyncpg://caprep@/caprep_v2_test?host=/tmp")


def main() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from starlette.responses import FileResponse, RedirectResponse

    engine = create_async_engine(DB)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse("/admin")

    @app.get("/admin", include_in_schema=False)
    async def _ui() -> FileResponse:
        return FileResponse(Path(__file__).with_name("admin_ui.html"))

    async def _bootstrap() -> User:
        async with maker() as s:
            u = (await s.execute(select(User).where(User.role == "ADMIN"))).scalars().first()
            if u is None:
                u = User(
                    auth_user_id=f"dev-admin-{uuid.uuid4().hex[:8]}",
                    email="dev-admin@localhost",
                    display_name="Dev Admin",
                    role="ADMIN",
                )
                s.add(u)
                await s.commit()
            return u

    admin = _run(_bootstrap())

    _session = maker()

    def _db():
        return _session

    app.dependency_overrides[get_current_principal] = lambda: Principal(
        auth_user_id=admin.auth_user_id,
        email=admin.email,
        role=admin.role,
        claims={"sub": admin.auth_user_id},
    )

    async def _user() -> User:
        return admin

    app.dependency_overrides[get_current_user] = _user

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    lg = logging.getLogger("devtools")
    lg.info("Content console -> http://127.0.0.1:8001/admin")
    lg.info("API docs        -> http://127.0.0.1:8001/docs")
    lg.info("acting as       -> %s (%s)", admin.email, admin.role)
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    main()
