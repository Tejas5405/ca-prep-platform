"""Study tools that record what happened and do not pretend otherwise.

Nothing in this module sends email, charges a card, opens a camera, syncs a
calendar, or rewrites a published answer. Where a boolean could be flipped into
that claim, the table constraint refuses it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.core.permissions import Permission, require_permission
from app.models.campus import (
    FormulaEntry,
    GlossaryEntry,
)
from app.models.user import User
from app.schemas.base import StrictRequest

from ._shared import (
    _NOTE,
)

router = APIRouter(tags=["campus"])

"""Formula and glossary reference data."""


class FormulaIn(StrictRequest):
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=2, max_length=2000)
    subject_label: str | None = Field(default=None, max_length=80)
    published: bool = False


class GlossaryIn(StrictRequest):
    term: str = Field(min_length=2, max_length=120)
    definition: str = Field(min_length=2, max_length=1000)
    published: bool = False


@router.get("/campus/formulas", summary="Published study notes")
async def list_formulas(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(FormulaEntry)
                .where(FormulaEntry.published.is_(True))
                .order_by(FormulaEntry.title)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "entries": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "body": row.body,
                    "subjectLabel": row.subject_label,
                }
                for row in rows
            ],
            "authority": "editor_note",
            "note": _NOTE,
        },
        request_id=get_request_id(),
    )


@router.post("/admin/formulas", summary="Add a study note")
async def create_formula(
    payload: FormulaIn,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    row = FormulaEntry(
        id=uuid.uuid4(),
        title=payload.title.strip(),
        body=payload.body.strip(),
        subject_label=payload.subject_label,
        published=payload.published,
        created_by=actor.id,
    )
    session.add(row)
    await session.commit()
    return success(
        {"id": str(row.id), "published": row.published, "authority": "editor_note", "note": _NOTE},
        request_id=get_request_id(),
    )


@router.get("/campus/glossary", summary="Published glossary")
async def list_glossary(
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(GlossaryEntry)
                .where(GlossaryEntry.published.is_(True))
                .order_by(GlossaryEntry.term)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "entries": [
                {"term": row.term, "definition": row.definition, "source": row.source}
                for row in rows
            ],
            "note": "Platform entries describe this product. Editor entries are notes, not statutes.",
        },
        request_id=get_request_id(),
    )


@router.post("/admin/glossary", summary="Add a glossary entry")
async def create_glossary(
    payload: GlossaryIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_CONTENT)),
) -> Any:
    row = GlossaryEntry(
        id=uuid.uuid4(),
        term=payload.term.strip(),
        definition=payload.definition.strip(),
        source="editor",
        published=payload.published,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return problem(
            status=409,
            title="Term already exists",
            detail="That glossary term is already saved.",
            type_slug="campus",
        )
    return success({"id": str(row.id), "source": "editor"}, request_id=get_request_id())
