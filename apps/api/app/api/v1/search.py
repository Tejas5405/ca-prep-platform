"""Full-text search over the question bank (blueprint build order D4).

THE EXPRESSION IS COPIED FROM THE INDEX, CHARACTER FOR CHARACTER:

    to_tsvector('english', COALESCE(search_text, text))

``idx_questions_fts`` is a GIN index over exactly that expression. Write
``to_tsvector('english', text)`` instead and the query is still CORRECT - it
returns the right rows - but it cannot use the index, so every search becomes a
sequential scan over the whole bank. That is the kind of bug that survives review
and testing and shows up as "search got slow" a year later, which is why the
integration suite asserts the plan uses the index with sequential scans disabled.

``COALESCE(search_text, text)`` is also why ``search_text`` exists: a question
whose text is full of formulas can carry plain-language keywords written by an
editor, and it stays searchable by them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import desc, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_request_id
from app.core.envelope import problem, success
from app.core.identity import get_current_user
from app.models.curriculum import Chapter, Subject
from app.models.question import Question
from app.models.user import User

router = APIRouter(tags=["search"])

MIN_QUERY_LENGTH = 2
MAX_RESULTS = 50


#: The text-search configuration, as SQL TEXT rather than a bind parameter.
#:
#: THIS IS NOT A STYLE CHOICE. ``idx_questions_fts`` is a GIN index over
#: ``to_tsvector('english', ...)``. A bind parameter (``to_tsvector($1, ...)``) is
#: not a constant expression, so PostgreSQL cannot match it against the index
#: expression and quietly falls back to a sequential scan. Sending the language as
#: a literal keeps the query text identical to the index definition.
_LANGUAGE = literal_column("'english'")


def _tsvector() -> Any:
    return func.to_tsvector(_LANGUAGE, func.coalesce(Question.search_text, Question.text))


def _tsquery(query: str) -> Any:
    # websearch_to_tsquery accepts what a person actually types - quoted phrases,
    # OR, -exclusion - and cannot throw a syntax error the way to_tsquery can, so a
    # stray quote in the search box is not a 500.
    return func.websearch_to_tsquery(_LANGUAGE, query)


def build_search_statement(query: str, *, limit: int) -> Any:
    """The search, as a statement. Extracted so the test can EXPLAIN THIS EXACT SQL.

    A test that EXPLAINs a hand-written copy of the query proves nothing: the copy
    is the thing that would stay correct while the route drifted off the index. The
    route calls this function and the integration test compiles the same call, so
    the plan the test sees is the plan production runs.
    """
    rank = func.ts_rank(_tsvector(), _tsquery(query)).label("rank")
    return (
        select(
            Question.id,
            Question.text,
            Question.question_type,
            Question.difficulty,
            Question.marks,
            Question.subject_id,
            Question.chapter_id,
            Subject.name.label("subject_name"),
            Chapter.name.label("chapter_name"),
            rank,
            func.ts_headline(
                _LANGUAGE,
                Question.text,
                _tsquery(query),
                "MaxWords=28, MinWords=8",
            ).label("snippet"),
        )
        .outerjoin(Subject, Subject.id == Question.subject_id)
        .outerjoin(Chapter, Chapter.id == Question.chapter_id)
        .where(
            Question.status == "PUBLISHED",
            Question.deleted_at.is_(None),
            _tsvector().op("@@")(_tsquery(query)),
        )
        .order_by(desc("rank"))
        .limit(limit)
    )


@router.get("/search", summary="Search published questions")
async def search_questions(
    q: str,
    limit: int = 20,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    query = q.strip()
    if len(query) < MIN_QUERY_LENGTH:
        # One character matches most of the bank, which is slower than not
        # searching and less useful than searching.
        return problem(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Query too short",
            detail=f"Provide at least {MIN_QUERY_LENGTH} characters.",
            type_slug="search",
        )

    stmt = build_search_statement(query, limit=min(MAX_RESULTS, max(1, limit)))

    rows = (await session.execute(stmt)).all()
    return success(
        {
            "query": query,
            "count": len(rows),
            "results": [
                {
                    "id": str(row.id),
                    "snippet": row.snippet,
                    "text": row.text,
                    "questionType": row.question_type,
                    "difficulty": row.difficulty,
                    "marks": row.marks,
                    "subjectId": str(row.subject_id),
                    "subjectName": row.subject_name,
                    "chapterId": str(row.chapter_id) if row.chapter_id else None,
                    "chapterName": row.chapter_name,
                    "rank": float(row.rank),
                }
                for row in rows
            ],
        },
        request_id=get_request_id(),
    )
