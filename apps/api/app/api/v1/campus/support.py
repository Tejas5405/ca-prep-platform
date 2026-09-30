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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.core.permissions import Permission, require_permission
from app.models.campus import (
    SupportTicket,
)
from app.models.user import User
from app.schemas.base import StrictRequest

from ._shared import (
    _uuid,
)

router = APIRouter(tags=["campus"])

"""Student support tickets."""


class TicketIn(StrictRequest):
    subject: str = Field(min_length=2, max_length=140)
    body: str = Field(min_length=2, max_length=2000)


class TicketStatusIn(StrictRequest):
    status: str = Field(pattern="^(OPEN|ACKNOWLEDGED|CLOSED)$")


@router.post("/campus/support", summary="Save a support ticket")
async def create_ticket(
    payload: TicketIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    row = SupportTicket(
        id=uuid.uuid4(),
        user_id=user.id,
        subject=payload.subject.strip(),
        body=payload.body.strip(),
        status="OPEN",
        email_sent=False,
    )
    session.add(row)
    await session.commit()
    return success(
        {
            "id": str(row.id),
            "status": "OPEN",
            "emailSent": False,
            "note": "Saved for an operator to read. No email was sent.",
        },
        request_id=get_request_id(),
    )


@router.get("/campus/support", summary="Your support tickets")
async def my_tickets(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    rows = (
        (
            await session.execute(
                select(SupportTicket)
                .where(SupportTicket.user_id == user.id)
                .order_by(SupportTicket.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "tickets": [
                {
                    "id": str(row.id),
                    "subject": row.subject,
                    "status": row.status,
                    "emailSent": False,
                }
                for row in rows
            ]
        },
        request_id=get_request_id(),
    )


@router.get("/admin/support", summary="Support queue")
async def admin_tickets(
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> Any:
    rows = (
        (
            await session.execute(
                select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return success(
        {
            "tickets": [
                {
                    "id": str(row.id),
                    "subject": row.subject,
                    "body": row.body,
                    "status": row.status,
                    "emailSent": False,
                }
                for row in rows
            ],
            "note": "No email is sent from this queue.",
        },
        request_id=get_request_id(),
    )


@router.patch("/admin/support/{ticket_id}", summary="Change a ticket status")
async def update_ticket(
    ticket_id: str,
    payload: TicketStatusIn,
    session: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> Any:
    parsed = _uuid(ticket_id)
    row = await session.get(SupportTicket, parsed) if parsed else None
    if row is None:
        return problem(
            status=404,
            title="Ticket not found",
            detail="That ticket does not exist.",
            type_slug="campus",
        )
    row.status = payload.status
    await session.commit()
    return success(
        {"id": str(row.id), "status": row.status, "emailSent": False}, request_id=get_request_id()
    )
