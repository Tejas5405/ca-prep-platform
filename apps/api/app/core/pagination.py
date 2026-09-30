"""Keyset (cursor) pagination for append-heavy, never-quite-finally-sorted tables.

WHY NOT OFFSET

    SELECT ... ORDER BY created_at DESC LIMIT 50 OFFSET 5000

Offset pagination re-sorts and re-counts from zero on every page, so page 100
costs 100x page 1. That is acceptable on a 200-row list and ruinous on an audit
log, which is exactly the table a user opens precisely because something is
wrong.

Worse, offset pagination is not merely slow, it is WRONG under concurrency.
A row inserted between two page requests shifts every later row by one, so the
client sees the same row twice and silently misses another. On an audit log
"silently misses a row" means an admin concludes an action did not happen when
it did. A cursor is a position in the sort order rather than an offset into it,
so a row inserted at the front cannot move the window - new rows are simply
never returned, which is the correct behaviour for a log being read top-down.

THE CURSOR IS A SORT KEY, NOT AN OFFSET

A cursor carries (created_at, id) of the last row returned. Both halves are
load-bearing:

  * created_at alone is ambiguous. Timestamps collide - two audit rows written
    in the same microsecond, or a whole batch sharing a statement timestamp -
    and `created_at < X` would skip every row sharing the boundary timestamp.
  * id alone is not a sort key the caller can be handed.

Ordering by both and filtering on the tuple comparison

    (created_at, id) < (cursor_created_at, cursor_id)

is what makes the page boundary exact. Postgres evaluates that as a single
range comparison, so `idx_<table>_created_id_desc` serves both the filter and
the ORDER BY with no sort step - which is the entire point of the index.

WHY A CURSOR IS OPAQUE

The value is base64url, not JSON, so a client cannot construct one and the
shape can change without breaking anyone. It is not encrypted and must not be
treated as a secret: a client can read its own cursor, and the only thing it
can do with it is read rows it could already read. It is tamper-EVIDENT rather
than tamper-proof - a modified cursor either fails to decode or decodes to a
position outside the caller's filter, and the query still only returns rows
that pass the ordinary permission checks, which run independently of the
cursor.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

#: Hard ceiling on a single page. A cursor makes large pages cheap, but a client
#: asking for 100000 rows is a bug, and an unbounded limit is a denial-of-service
#: lever on a table that grows without bound.
MAX_LIMIT = 100
DEFAULT_LIMIT = 50


class InvalidCursor(ValueError):
    """A cursor that cannot be decoded.

    Subclasses ValueError so a route can let a plain validation layer turn it
    into a 400 without importing this module's error type. The distinction that
    matters operationally is that this is CLIENT error: a bad cursor means the
    client is holding something it did not get from `encode_cursor`, and the
    remedy is to restart from the first page, never to retry.
    """


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    # Restore the stripped padding. urlsafe_b64decode requires the '='s back, and
    # a cursor that arrived without them is still a well-formed cursor.
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode_cursor(created_at: datetime, row_id: Any) -> str:
    """Encode the sort position of the last row on a page."""
    payload = {"t": created_at.isoformat(), "i": str(row_id)}
    return _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def decode_cursor(cursor: str) -> tuple[datetime, Any]:
    """Decode a cursor back into (created_at, id).

    Raises InvalidCursor on anything malformed. It MUST NOT raise a bare
    binascii/JSON/Unicode error: those surface as a 500, and a 500 for a
    mistyped query parameter is indistinguishable from the server being broken.
    """
    if not isinstance(cursor, str) or not cursor:
        raise InvalidCursor("cursor must be a non-empty string")
    try:
        raw = _b64decode(cursor)
    except (binascii.Error, ValueError) as exc:
        raise InvalidCursor("cursor is not valid base64") from exc
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidCursor("cursor is not a valid payload") from exc
    if not isinstance(payload, dict) or "t" not in payload or "i" not in payload:
        raise InvalidCursor("cursor is missing its sort key")
    try:
        return datetime.fromisoformat(str(payload["t"])), payload["i"]
    except (TypeError, ValueError) as exc:
        raise InvalidCursor("cursor timestamp is not a valid datetime") from exc


class CursorPage[T](BaseModel):
    """One page of a cursor-paginated collection.

    `has_more` is explicit rather than inferred from `next_cursor is None`,
    because "there is another page" and "here is a cursor" are separate
    questions and a client should not have to know that the second implies the
    first.
    """

    items: list[T] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


def _sort_key(model: type[Any]) -> tuple[Any, Any]:
    """The (created_at, id) column pair this module paginates by."""
    return model.__table__.c.created_at, model.__table__.c.id


def _coerce_to_column_type(column: Any, value: Any) -> Any:
    """Convert a decoded cursor value to the column's Python type.

    A cursor is text, so `id` arrives as a string. Comparing a uuid column to a
    string does not work - Postgres raises `operator does not exist: uuid <
    character varying`, which is a 500 from a mistyped query parameter. Casting
    here means the failure mode is a clean InvalidCursor instead.
    """
    python_type = getattr(column.type, "python_type", None)
    if python_type is None or not isinstance(value, str):
        return value
    try:
        return python_type(value)
    except (TypeError, ValueError) as exc:
        raise InvalidCursor("cursor value does not match the column type") from exc


async def paginate_cursor(
    session: AsyncSession,
    model: type[Any],
    *,
    query: Select[Any] | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[Any], str | None, bool]:
    """Run one keyset page. Returns (rows, next_cursor, has_more).

    `query` is any pre-filtered select against `model` - permissions, a user id,
    a status. The keyset window is ANDed onto it, so a cursor can never widen
    the caller's own filter: it selects a position inside it, not around it.

    Fetches limit + 1 rows and drops the extra. That is how `has_more` is known
    without a second COUNT(*), which on an audit log is the expensive query
    this whole exercise exists to avoid.
    """
    capped = max(1, min(int(limit), MAX_LIMIT))
    created_at, row_id = _sort_key(model)

    statement = query if query is not None else model.__table__.select()  # type: ignore[attr-defined]
    statement = statement.order_by(created_at.desc(), row_id.desc()).limit(capped + 1)

    if cursor:
        cursor_time, cursor_id = decode_cursor(cursor)
        cursor_id = _coerce_to_column_type(row_id, cursor_id)
        # Row-value comparison, not `created_at < t OR (created_at = t AND id < i)`.
        # Same result, one range comparison, and the composite index serves it
        # directly. The expanded OR forces the planner to consider a bitmap OR
        # of two scans and frequently sorts instead.
        statement = statement.where(tuple_(created_at, row_id) < (cursor_time, cursor_id))

    result = await session.execute(statement)
    rows = list(result.scalars().all())

    has_more = len(rows) > capped
    page = rows[:capped]

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(last.created_at, getattr(last, "id", None) or row_id)
    return page, next_cursor, has_more
