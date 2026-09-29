"""Response envelopes and error shape.

Blueprint v3 §7.2 defines these exactly:

    success:  {"data": {...}, "meta": {"requestId": "..."}}
    list:     {"data": [...], "meta": {"total": 150, "page": 1, "limit": 20,
                                       "hasMore": true}}
    error:    {"type": "...", "title": "...", "status": 400,
               "detail": "...", "errors": [{"field": ..., "message": ...}]}

NOTE ON PAGINATION: v3 specifies page/limit/offset with a ``hasMore`` flag. The
superseded blueprint used cursor pagination, so any client code or PRD written
against the old contract is wrong and must be updated. Offset pagination is a
deliberate trade here: it is simpler for the React table UIs and acceptable while
the question bank is small, but it drifts if rows are inserted mid-pagination.
Revisit if the question bank grows past the PostgreSQL FTS scale trigger.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

T = TypeVar("T")


class Meta(BaseModel):
    requestId: str = "-"


class SuccessEnvelope(BaseModel, Generic[T]):  # noqa: UP046
    data: T
    meta: Meta = Meta()


class ListMeta(BaseModel):
    requestId: str = "-"
    total: int = 0
    page: int = 1
    limit: int = 20
    hasMore: bool = False


class ListEnvelope(BaseModel, Generic[T]):  # noqa: UP046
    data: list[T]
    meta: ListMeta = ListMeta()


def success(data: Any, request_id: str = "-") -> dict[str, Any]:
    return {"data": data, "meta": {"requestId": request_id}}


def paginated(
    items: list[Any],
    total: int,
    page: int,
    limit: int,
    request_id: str = "-",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A list envelope.

    ``extra`` carries page-level facts a screen needs alongside the rows - the unread
    count next to a notification list, for instance. It is an extension of the EXISTING
    shape rather than a second one: adding ``dataWithMeta`` would mean two envelopes in
    one API, and a client that has to detect which one it received is a client with a
    bug waiting in it.
    """
    meta: dict[str, Any] = {
        "requestId": request_id,
        "total": total,
        "page": page,
        "limit": limit,
        "hasMore": page * limit < total,
    }
    if extra:
        meta.update(extra)
    return {"data": items, "meta": meta}


def problem(
    *,
    status: int,
    title: str,
    detail: str | None = None,
    type_slug: str = "about:blank",
    errors: list[dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """RFC 7807-style error body.

    ``status`` in the body and the HTTP status line are set from the SAME value,
    so a client reading either one gets the same answer. (The superseded
    implementation once returned a body claiming 422 on a 400 response.)

    The media type is ``application/problem+json``, which is what RFC 7807
    specifies for this document. It was ``application/json`` - the JSONResponse
    default - which meant the shape was documented as RFC 7807 in prose while
    being announced as an ordinary JSON body on the wire. A client can now branch
    on the content type instead of guessing from the presence of a ``title`` key.
    """
    body: dict[str, Any] = {
        "type": f"https://api.caprep.in/errors/{type_slug}",
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    # `extra` exists for the handful of errors that carry a machine-readable
    # companion field the RFC does not define - `retry_after` on a 429 is the
    # only current caller. It is merged last so a caller cannot overwrite the
    # type/title/status trio that callers branch on.
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")
