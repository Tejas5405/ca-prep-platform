"""CA Intermediate paper splits.

The combined papers stay in the database. Students at Intermediate do not pick
them. Direct Tax and Indirect Tax replace Taxation. Financial Management and
Strategic Management replace the combined Paper 6. Foundation and Final are not
in this table, and this module does not invent a chapter mapping for a paper it
does not name.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import Chapter, Subject, SubjectComponent

# Chapter codes are the seeded syllabus, not a guess about an unseen paper.
# A chapter that is not listed stays unassigned, and an unassigned question does
# not appear in either split.
INTERMEDIATE_SPLITS: tuple[dict[str, Any], ...] = (
    {
        "parent_code": "INT_TAX",
        "code": "DIRECT_TAX",
        "display_name": "Direct Tax",
        "slug": "direct-tax",
        "sort_order": 1,
        "chapter_codes": ("INT_TAX_01", "INT_TAX_02"),
    },
    {
        "parent_code": "INT_TAX",
        "code": "INDIRECT_TAX",
        "display_name": "Indirect Tax",
        "slug": "indirect-tax",
        "sort_order": 2,
        "chapter_codes": ("INT_TAX_03",),
    },
    {
        "parent_code": "INT_FMSM",
        "code": "FINANCIAL_MANAGEMENT",
        "display_name": "Financial Management",
        "slug": "financial-management",
        "sort_order": 1,
        "chapter_codes": ("INT_FMSM_01",),
    },
    {
        "parent_code": "INT_FMSM",
        "code": "STRATEGIC_MANAGEMENT",
        "display_name": "Strategic Management",
        "slug": "strategic-management",
        "sort_order": 2,
        "chapter_codes": ("INT_FMSM_02",),
    },
)


async def ensure_intermediate_components(session: AsyncSession) -> None:
    """Create the four splits and point the known chapters at them.

    Idempotent. A database that was seeded before this table existed gets the
    same rows as a fresh seed. It does not rename or delete the parent papers.
    """
    for split in INTERMEDIATE_SPLITS:
        subject_id = (
            await session.execute(select(Subject.id).where(Subject.code == split["parent_code"]))
        ).scalar_one_or_none()
        if subject_id is None:
            continue
        stmt = (
            pg_insert(SubjectComponent)
            .values(
                id=uuid.uuid4(),
                parent_subject_id=subject_id,
                code=split["code"],
                display_name=split["display_name"],
                slug=split["slug"],
                sort_order=split["sort_order"],
                is_filterable=True,
                is_active=True,
            )
            .on_conflict_do_update(
                constraint="uq_subject_component_code",
                set_={
                    "display_name": split["display_name"],
                    "slug": split["slug"],
                    "sort_order": split["sort_order"],
                    "is_active": True,
                    "is_filterable": True,
                },
            )
            .returning(SubjectComponent.id)
        )
        component_id = (await session.execute(stmt)).scalar_one()
        if split["chapter_codes"]:
            await session.execute(
                update(Chapter)
                .where(
                    Chapter.subject_id == subject_id,
                    Chapter.code.in_(split["chapter_codes"]),
                )
                .values(subject_component_id=component_id)
            )
