"""Collections and the LDR list - blueprint v3 §7.3's last named route.

WHY THIS FILE EXISTS

`POST /collections` was the only endpoint in the §7.3 inventory with nothing behind
it. `app/services/collections.py` validated smart-collection filters and normalised
the legacy LDR rows, and no route ever called it, so the filter validator was code
in search of a caller. That is the same shape as the empty list pages and the
publishing endpoint that wrote nothing: a feature that exists everywhere except
where a student would touch it.

WHAT A "COLLECTION" IS HERE, AND WHAT IT IS NOT

It is NOT a synonym for the bookmark ("marked for review"), which is a single
boolean on the progress row written by the practice loop. A collection is a named
container the student owns, can put questions into before or after answering them,
and can attach a note to. Both are surfaced, because they answer different
questions, and this file is where the difference is enforced:

  * ``GET /ldr`` returns what the practice loop flagged, and is read-only apart from
    the existing bookmark route.
  * everything under ``/collections`` is the student's own structure.

EVERY ROUTE IS FILTERED BY THE CALLER'S ``users.id``.

Not checked afterwards - the predicate is in the query. A route that loads a row
and then compares owners is one refactor away from leaking another student's
collection, and the leak would look like a working feature to everyone involved.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import paginated, problem, success
from app.core.identity import get_current_user
from app.models.user import User
from app.repositories.collections import CollectionItem, SqlCollectionStore
from app.schemas.base import StrictRequest, UuidRef
from app.services.collections import (
    FilterValidationError,
    canonical_filters,
    validate_filters,
)

router = APIRouter(tags=["collections"])

MAX_NAME = 120
MAX_NOTE = 500
MAX_ADD_BATCH = 100


class CreateCollectionIn(StrictRequest):
    """A new collection.

    ``filters`` is accepted only for a SMART collection, and is validated against an
    allowlist before it is stored - raw JSON is never handed to SQLAlchemy. The
    model enforces the same rule at the database level
    (``ck_collection_filters_match_kind``), so a bug here cannot produce a smart
    collection with no predicate.
    """

    name: str = Field(min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=1000)
    kind: str = Field(default="MANUAL", pattern="^(MANUAL|SMART)$")
    filters: dict[str, Any] | None = None


class UpdateCollectionIn(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=1000)


class AddQuestionsIn(StrictRequest):
    """Add questions to a collection.

    A batch, not one call per question: a student selecting twelve questions and
    pressing add is one intention, and twelve round trips is how one of them fails
    silently and leaves a half-built collection.
    """

    question_ids: list[UuidRef] = Field(min_length=1, max_length=MAX_ADD_BATCH)
    note: str | None = Field(default=None, max_length=MAX_NOTE)


def _collection_payload(row: Any) -> dict[str, Any]:
    collection = row.collection
    return {
        "id": str(collection.id),
        "name": collection.name,
        "description": collection.description,
        "kind": collection.kind,
        "filters": collection.filters,
        "isSystem": collection.is_system,
        "isPublic": collection.is_public,
        "questionCount": row.question_count,
        "createdAt": collection.created_at,
        "updatedAt": collection.updated_at,
    }


def _item_payload(item: CollectionItem) -> dict[str, Any]:
    """A question inside a collection.

    THE ANSWER IS NOT IN HERE, and that is the same rule as everywhere else: a
    collection is a reading list, not an answer key. The question's options are
    returned because a student recognises a question by its choices, but
    ``correct_answer`` and ``is_correct`` are not read from the row at all - so
    there is no filter here that a later edit could forget.
    """
    return {
        "questionId": str(item.question_id),
        "text": item.text,
        "questionType": item.question_type,
        "difficulty": item.difficulty,
        "marks": item.marks,
        "subjectId": str(item.subject_id),
        "chapterId": str(item.chapter_id) if item.chapter_id else None,
        "isHistorical": item.is_historical,
        "financeActYear": item.finance_act_year,
        # The disclaimer travels with the question even in a list: a collection is
        # where a student revises from, and revising a repealed provision without
        # noticing is the failure this field exists to prevent.
        "disclaimer": item.disclaimer_text,
        "note": item.note,
        "options": [{"label": option.label, "text": option.text} for option in item.options],
    }


# ------------------------------------------------------------------- write side


@router.post("/collections", status_code=status.HTTP_201_CREATED, summary="Create a collection")
async def create_collection(
    payload: CreateCollectionIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Create a collection for the signed-in student."""
    # The caller's id is read ONCE, before any branch that can roll the transaction
    # back. After a rollback the identity row is expired, and touching `user.id`
    # afterwards would issue a lazy load - a MissingGreenlet 500 rather than the
    # error response this function is trying to build.
    user_id = user.id
    filters: dict[str, Any] | None = None
    if payload.kind == "SMART":
        try:
            filters = validate_filters(payload.filters).model_dump(exclude_none=True)
        except FilterValidationError as exc:
            return problem(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Invalid filters",
                detail=str(exc),
                type_slug="collections",
                errors=[{"field": exc.field_name or "filters", "message": str(exc)}],
            )
    elif payload.filters is not None:
        # Refused rather than ignored: a manual collection with filters would look
        # like it filtered, and would filter nothing.
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Filters on a manual collection",
            detail="A MANUAL collection holds the questions you add. Create a SMART one to filter.",
            type_slug="collections",
            errors=[{"field": "filters", "message": "not allowed when kind is MANUAL"}],
        )

    store = SqlCollectionStore(session)
    from sqlalchemy.exc import IntegrityError

    try:
        collection = await store.create(
            user_id=user_id,
            name=payload.name.strip(),
            kind=payload.kind,
            description=payload.description,
            filters=filters,
        )
        await session.commit()
    except IntegrityError:
        # `uq_collection_user_name`: two collections with one name is the state the
        # feature exists to prevent, so the database refuses it and this turns that
        # refusal into a sentence a student can act on.
        await session.rollback()
        return problem(
            status=status.HTTP_409_CONFLICT,
            title="Name already used",
            detail=f"You already have a collection called “{payload.name.strip()}”.",
            type_slug="collections",
            errors=[{"field": "name", "message": "already used"}],
        )

    return success(
        {
            "id": str(collection.id),
            "name": collection.name,
            "description": collection.description,
            "kind": collection.kind,
            "filters": collection.filters,
            "isSystem": False,
            "questionCount": 0,
            "filterKey": canonical_filters(validate_filters(filters)) if filters else None,
        },
        request_id=get_request_id(),
    )


@router.patch("/collections/{collection_id}", summary="Rename a collection")
async def update_collection(
    collection_id: uuid.UUID,
    payload: UpdateCollectionIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    user_id = user.id
    store = SqlCollectionStore(session)
    updated = await store.rename(
        collection_id,
        user_id,
        name=payload.name.strip() if payload.name else None,
        description=payload.description,
    )
    if updated is None:
        await session.rollback()
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Collection not found",
            detail="No collection with that id belongs to you, or it is a system collection.",
            type_slug="collections",
        )
    await session.commit()
    return success(
        {
            "id": str(updated.id),
            "name": updated.name,
            "description": updated.description,
            "kind": updated.kind,
            "isSystem": updated.is_system,
        },
        request_id=get_request_id(),
    )


@router.delete("/collections/{collection_id}", summary="Delete a collection")
async def delete_collection(
    collection_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Delete a collection. The questions are untouched - only the container goes."""
    user_id = user.id
    store = SqlCollectionStore(session)
    if not await store.delete(collection_id, user_id):
        await session.rollback()
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Collection not found",
            detail="No collection with that id belongs to you, or it is a system collection.",
            type_slug="collections",
        )
    await session.commit()
    return success(
        {"deleted": True, "collectionId": str(collection_id)}, request_id=get_request_id()
    )


@router.post("/collections/{collection_id}/questions", summary="Add questions to a collection")
async def add_questions(
    collection_id: uuid.UUID,
    payload: AddQuestionsIn,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Add a batch of questions. Idempotent: adding one twice is not an error."""
    user_id = user.id
    store = SqlCollectionStore(session)
    collection = await store.get(collection_id, user_id)
    if collection is None:
        await session.rollback()
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Collection not found",
            detail="No collection with that id belongs to you.",
            type_slug="collections",
        )

    added = await store.add_questions(collection_id, list(payload.question_ids), note=payload.note)
    await session.commit()
    return success(
        {
            "collectionId": str(collection_id),
            # `added` is what actually changed; `requested` is what was asked for.
            # A client that shows "12 added" when three were already there has
            # told the student something untrue.
            "added": added,
            "requested": len(payload.question_ids),
            "alreadyPresent": len(payload.question_ids) - added,
        },
        request_id=get_request_id(),
    )


@router.delete(
    "/collections/{collection_id}/questions/{question_id}",
    summary="Remove a question from a collection",
)
async def remove_question(
    collection_id: uuid.UUID,
    question_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    user_id = user.id
    store = SqlCollectionStore(session)
    if await store.get(collection_id, user_id) is None:
        await session.rollback()
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Collection not found",
            detail="No collection with that id belongs to you.",
            type_slug="collections",
        )
    if not await store.remove_question(collection_id, question_id):
        await session.rollback()
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Question not in this collection",
            detail="Nothing to remove.",
            type_slug="collections",
        )
    await session.commit()
    return success(
        {"collectionId": str(collection_id), "questionId": str(question_id), "removed": True},
        request_id=get_request_id(),
    )


# -------------------------------------------------------------------- read side


@router.get("/collections", summary="List your collections")
async def list_collections(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    store = SqlCollectionStore(session)
    rows, total = await store.list_for_user(user.id, limit=limit, offset=(page - 1) * limit)

    return paginated(
        [_collection_payload(row) for row in rows],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )


@router.get("/collections/{collection_id}", summary="Read a collection")
async def get_collection(
    collection_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    user_id = user.id
    store = SqlCollectionStore(session)
    collection = await store.get(collection_id, user_id)
    if collection is None:
        return problem(
            status=status.HTTP_404_NOT_FOUND,
            title="Collection not found",
            detail="No collection with that id belongs to you.",
            type_slug="collections",
        )

    items, total = await store.items(collection_id, limit=limit, offset=(page - 1) * limit)
    # The detail route nests its questions (like `/practice/questions`) rather than
    # returning a bare list: a collection is an object with a name and a kind, and
    # flattening it into `data: [...]` would lose that on every client.
    return success(
        {
            "id": str(collection.id),
            "name": collection.name,
            "description": collection.description,
            "kind": collection.kind,
            "filters": collection.filters,
            "isSystem": collection.is_system,
            "questions": [_item_payload(item) for item in items],
            "count": len(items),
            "total": total,
            "page": page,
            "limit": limit,
            "hasMore": page * limit < total,
        },
        request_id=get_request_id(),
    )


@router.get("/questions/{question_id}/collections", summary="Which collections hold this question")
async def question_collections(
    question_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """Membership for one question, so "add to collection" can show its own state."""
    store = SqlCollectionStore(session)
    ids = await store.collections_for_question(user.id, question_id)
    return success(
        {"questionId": str(question_id), "collectionIds": [str(value) for value in ids]},
        request_id=get_request_id(),
    )


@router.get("/ldr", summary="Questions you marked for later")
async def list_marked_for_review(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """The LDR list: the practice loop's own flag, newest first.

    Read-only by design. Flagging happens at the moment a student meets a question
    (``POST /practice/questions/{id}/bookmark``), and this endpoint is the place
    they come back to; a second write path here would be a second answer to "is
    this flagged".
    """
    store = SqlCollectionStore(session)
    rows, total = await store.marked_for_review(user.id, limit=limit, offset=(page - 1) * limit)
    return paginated(
        [
            {
                "questionId": str(question.id),
                "text": question.text,
                "questionType": question.question_type,
                "difficulty": question.difficulty,
                "marks": question.marks,
                "subjectId": str(question.subject_id),
                "chapterId": str(question.chapter_id) if question.chapter_id else None,
                "isHistorical": bool(question.is_historical),
                "financeActYear": question.finance_act_year,
                "disclaimer": question.disclaimer_text,
                "attempts": progress.attempts_count,
                "accuracy": float(progress.accuracy) if progress.accuracy is not None else None,
                "markedAt": progress.updated_at,
            }
            for question, progress in rows
        ],
        total=total,
        page=page,
        limit=limit,
        request_id=get_request_id(),
    )
