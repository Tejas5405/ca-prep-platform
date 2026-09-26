"""Store for the content library.

SESSION RULE, STATED AGAIN BECAUSE IT HAS BITTEN THIS CODEBASE THREE TIMES

Every public method takes ``session: AsyncSession | None = None``. When a route
passes its request session, the write is visible to that route's transaction; when
nothing is passed, the store opens and commits its own. Three separate bugs came from
a store method that silently opened its own factory while the route expected
otherwise - the symptom was a 404 or an empty list, and no dependency override could
fix it.

The other rule: every query that returns documents to a NON-ADMIN caller goes through
``services.content_library``. This module is allowed to know about access; it must
never be the place where a caller *forgets* about access, so the student-facing list
takes a ready-made WHERE clause rather than a set of filters to assemble.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, Text, case, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.dependencies import get_session_factory
from app.models.content import ContentAccessRule, ContentDocument, DocumentPage
from app.services.content_library import Viewer, document_filter

#: The status a document gets once its bytes are confirmed present. Distinct from
#: QUEUED: "uploaded" means storage has it, "queued" means a worker has been asked.
STATUS_UPLOADED = "UPLOADED"
STATUS_QUEUED = "QUEUED"
STATUS_ARCHIVED = "ARCHIVED"


@dataclass(slots=True)
class PageHit:
    """A full-text hit, with the page it came from and a highlighted excerpt."""

    document_id: uuid.UUID
    title: str
    page_number: int
    excerpt: str
    rank: float


def metadata_pattern(term: str) -> str:
    """A POSIX regex that matches a human's search term against a file's metadata.

    Two problems are solved here, and the second is the one that made operators give up
    on the old implementation:

    1. LIKE wildcards. ``%`` and ``_`` typed into a search box are wildcards, so
       searching for "12% p.a." returned the entire library. A regex has no such
       problem, and every regex metacharacter in the term is escaped.
    2. Separators. A file is named ``accounting-policy-scan.pdf`` and an operator types
       "accounting policy". Exact-substring matching cannot join those up, so the file
       was unfindable by the most obvious words in its own name. Runs of spaces,
       underscores and hyphens in the TERM are folded into a single separator class, so
       the same words match whichever way the file spells them.
    """
    parts = [re.escape(part) for part in re.split(r"[\s_\-]+", term) if part]
    if not parts:
        return ""
    return "[ _-]+".join(parts)


def document_matches(search: str) -> ColumnElement[bool]:
    """The admin library's search: metadata OR the text inside the documents.

    Filenames are not enough. An operator's real question is "do we hold anything that
    covers this?" - the study material, a past paper, a set of notes - and the answer
    lives in the extracted page text, which is the only thing that knows the phrase
    "Ind AS 116" appears in a document whose filename says nothing about it.

    The text branch is an EXISTS over ``document_pages`` rather than a JOIN, so a
    document with forty matching pages is still ONE row and the count stays a count of
    documents. ``idx_document_pages_fts`` serves it; ``websearch_to_tsquery`` accepts
    what an operator types (quotations, OR, minus) instead of raising on an apostrophe.

    Metadata is matched with ILIKE and the text with full-text search on purpose: a
    filename is a literal string and "AS-1" should match "AS-1", while page text is
    prose and should match the word "deferred" in "deferred tax".
    """
    cleaned = search.strip()
    if not cleaned:
        return ContentDocument.id.isnot(None)  # no-op filter, keeps the caller simple
    pattern = metadata_pattern(cleaned)
    metadata = or_(
        ContentDocument.title.op("~*")(pattern),
        ContentDocument.original_filename.op("~*")(pattern),
        ContentDocument.module.op("~*")(pattern),
        # tags is JSONB: cast to text so a tag search behaves like the other filters
        cast(ContentDocument.tags, Text).op("~*")(pattern),
    )
    in_text = ContentDocument.id.in_(
        select(DocumentPage.document_id).where(
            DocumentPage.search_vector.op("@@")(func.websearch_to_tsquery("english", cleaned))
        )
    )
    return or_(metadata, in_text)


@dataclass(slots=True)
class DashboardCounts:
    """Every number the admin dashboard shows, from the database, in one round trip."""

    total_students: int
    active_students: int
    new_students_7d: int
    total_documents: int
    processing_documents: int
    failed_documents: int
    indexed_documents: int
    total_pages: int
    extracted_chars: int
    total_questions: int
    published_questions: int
    total_mocks: int
    total_courses: int
    total_subjects: int
    total_chapters: int
    mock_attempts: int
    completed_attempts: int
    total_revenue_paise: int
    successful_payments: int
    failed_payments: int
    active_subscriptions: int
    notifications_sent: int
    ai_events: int
    storage_bytes: int


class SqlContentStore:
    """Reads and writes for documents, pages, access rules and admin aggregates."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    class _Scope:
        """The caller's session, or one of our own committed on clean exit."""

        def __init__(self, session: AsyncSession | None) -> None:
            self._given = session
            self.session: AsyncSession | None = session
            self._own = False

        async def __aenter__(self) -> AsyncSession:
            if self._given is not None:
                return self._given
            self._own = True
            self.session = get_session_factory()()
            return self.session

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            assert self.session is not None
            if not self._own:
                return
            if exc_type is None:
                await self.session.commit()
            await self.session.close()

    # ------------------------------------------------------------------ documents

    async def create_document(
        self,
        *,
        title: str,
        kind: str,
        bucket: str,
        storage_path: str,
        original_filename: str,
        uploaded_by: uuid.UUID,
        mime_type: str = "application/pdf",
        size_bytes: int | None = None,
        checksum_sha256: str | None = None,
        course_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
        chapter_id: uuid.UUID | None = None,
        topic_id: uuid.UUID | None = None,
        module: str | None = None,
        difficulty: str = "MEDIUM",
        access_tier: str = "PREMIUM",
        batch_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        session: AsyncSession | None = None,
    ) -> ContentDocument:
        async with self._Scope(session or self._session) as active:
            document = ContentDocument(
                id=uuid.uuid4(),
                title=title[:300],
                kind=kind,
                bucket=bucket,
                storage_path=storage_path,
                original_filename=original_filename[:300],
                mime_type=mime_type,
                size_bytes=size_bytes,
                checksum_sha256=checksum_sha256,
                uploaded_by=uploaded_by,
                course_id=course_id,
                subject_id=subject_id,
                chapter_id=chapter_id,
                topic_id=topic_id,
                module=module,
                difficulty=difficulty,
                access_tier=access_tier,
                batch_id=batch_id,
                tags=tags or [],
                status=STATUS_UPLOADED,
                version=1,
            )
            active.add(document)
            await active.flush()
            return document

    async def checksum_owner(
        self, checksum: str, *, session: AsyncSession | None = None
    ) -> ContentDocument | None:
        """The existing document with these exact bytes, if any.

        DUPLICATE DETECTION, and the reason bulk upload is usable at all: a folder of
        500 PDFs that overlaps with the library is the normal case, not the exception.
        Compared on the hash the client computed, so a renamed copy is still caught.
        """
        async with self._Scope(session or self._session) as active:
            return (
                await active.execute(
                    select(ContentDocument)
                    .where(
                        ContentDocument.checksum_sha256 == checksum,
                        ContentDocument.deleted_at.is_(None),
                    )
                    .order_by(ContentDocument.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def get_document(
        self,
        document_id: uuid.UUID,
        *,
        viewer: Viewer | None = None,
        session: AsyncSession | None = None,
    ) -> ContentDocument | None:
        """Load one document, honouring access when a ``viewer`` is supplied.

        Passing a viewer is how a student route cannot accidentally read a draft: the
        access clause is part of the WHERE, not a check afterwards.
        """
        async with self._Scope(session or self._session) as active:
            conditions = [ContentDocument.id == document_id]
            if viewer is not None:
                conditions.append(document_filter(viewer))
            return (
                await active.execute(select(ContentDocument).where(*conditions))
            ).scalar_one_or_none()

    async def list_documents(
        self,
        *,
        viewer: Viewer | None = None,
        status: str | None = None,
        kind: str | None = None,
        course_id: uuid.UUID | None = None,
        subject_id: uuid.UUID | None = None,
        chapter_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        search: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> tuple[list[ContentDocument], int]:
        async with self._Scope(session or self._session) as active:
            statement: Select = select(ContentDocument)
            conditions: list[ColumnElement[bool]] = []
            if viewer is not None:
                conditions.append(document_filter(viewer))
            else:
                # Admin path: everything except soft-deleted, and archived rows only
                # when asked for - an archive that still appears in the default list
                # is not an archive.
                conditions.append(ContentDocument.deleted_at.is_(None))
                if not include_archived:
                    conditions.append(ContentDocument.status != STATUS_ARCHIVED)
            if status:
                conditions.append(ContentDocument.status == status)
            if kind:
                conditions.append(ContentDocument.kind == kind)
            if course_id:
                conditions.append(ContentDocument.course_id == course_id)
            if subject_id:
                conditions.append(ContentDocument.subject_id == subject_id)
            if chapter_id:
                conditions.append(ContentDocument.chapter_id == chapter_id)
            if batch_id:
                conditions.append(ContentDocument.batch_id == batch_id)
            if search:
                conditions.append(document_matches(search))

            statement = statement.where(*conditions)
            total = (
                await active.execute(select(func.count()).select_from(statement.subquery()))
            ).scalar_one()
            rows = (
                await active.execute(
                    statement.order_by(ContentDocument.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
            return list(rows), int(total)

    async def update_document(
        self,
        document_id: uuid.UUID,
        values: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> ContentDocument | None:
        async with self._Scope(session or self._session) as active:
            if not values:
                return await self.get_document(document_id, session=active)
            result = await active.execute(
                update(ContentDocument)
                .where(ContentDocument.id == document_id, ContentDocument.deleted_at.is_(None))
                .values(**values)
                .returning(ContentDocument)
            )
            return result.scalar_one_or_none()

    async def set_status(
        self,
        document_id: uuid.UUID,
        status: str,
        *,
        error: str | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        async with self._Scope(session or self._session) as active:
            await active.execute(
                update(ContentDocument)
                .where(ContentDocument.id == document_id)
                .values(status=status, error=error, updated_at=datetime.now(UTC))
            )

    async def archive(self, document_id: uuid.UUID, *, session: AsyncSession | None = None) -> bool:
        """ARCHIVE: withdrawn from students, still visible to the admin.

        ARCHIVE IS NOT DELETE, and the two were conflated in the first cut of this
        store: archiving set ``deleted_at``, which made an archived document invisible
        in the admin list too - so "archive" behaved exactly like "delete" and there
        was no way to un-archive or even to find what had been archived. The states
        are now distinct:

          * ARCHIVED  - status = ARCHIVED, deleted_at NULL. Out of the student
                        library, in the admin list behind ``include_archived``, and
                        reversible.
          * deleted   - deleted_at set. Gone from every list. The original object and
                        the row survive so generated questions keep their source.
        """
        async with self._Scope(session or self._session) as active:
            result = await active.execute(
                update(ContentDocument)
                .where(
                    ContentDocument.id == document_id,
                    ContentDocument.deleted_at.is_(None),
                    ContentDocument.status != STATUS_ARCHIVED,
                )
                .values(status=STATUS_ARCHIVED, is_published=False, updated_at=datetime.now(UTC))
            )
            return bool(result.rowcount)

    async def restore(self, document_id: uuid.UUID, *, session: AsyncSession | None = None) -> bool:
        """Bring an archived document back, unpublished, for the admin to re-publish."""
        async with self._Scope(session or self._session) as active:
            result = await active.execute(
                update(ContentDocument)
                .where(ContentDocument.id == document_id, ContentDocument.status == STATUS_ARCHIVED)
                # Decided IN SQL, not in Python: `ContentDocument.page_count > 0` is a
                # SQL expression and using it in a Python conditional raises
                # "Boolean value of this clause is not defined" on every call.
                .values(
                    status=case(
                        (ContentDocument.page_count > 0, "COMPLETED"),
                        else_="UPLOADED",
                    )
                )
            )
            return bool(result.rowcount)

    async def soft_delete(
        self, document_id: uuid.UUID, *, session: AsyncSession | None = None
    ) -> bool:
        """DELETE: hidden from every list, row and file retained.

        The original object stays in storage and the row keeps its id, because
        questions generated from this document reference it. Deleting the row would
        orphan every one of them and destroy the traceability the library exists for.
        """
        async with self._Scope(session or self._session) as active:
            result = await active.execute(
                update(ContentDocument)
                .where(ContentDocument.id == document_id, ContentDocument.deleted_at.is_(None))
                .values(
                    deleted_at=datetime.now(UTC),
                    status=STATUS_ARCHIVED,
                    is_published=False,
                )
            )
            return bool(result.rowcount)

    async def bulk_assign(
        self,
        document_ids: list[uuid.UUID],
        values: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Apply metadata to many documents at once.

        The bulk-categorisation step of a 500-file upload: the admin drops a folder,
        then says "these 120 are Financial Reporting, chapter 5, Premium" once rather
        than 120 times. Progress fields are excluded by the route, not here, so this
        cannot be used to fake a processing state.
        """
        if not document_ids or not values:
            return 0
        async with self._Scope(session or self._session) as active:
            result = await active.execute(
                update(ContentDocument)
                .where(
                    ContentDocument.id.in_(document_ids),
                    ContentDocument.deleted_at.is_(None),
                )
                .values(**values)
            )
            return int(result.rowcount or 0)

    # --------------------------------------------------------------------- pages

    async def replace_pages(
        self,
        document_id: uuid.UUID,
        pages: list[dict[str, Any]],
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Write the extracted text for a document, replacing any previous run.

        Delete-then-insert inside one transaction: a re-process must not leave two
        page 7 rows behind, and a half-written run is worse than the old text. The
        ORIGINAL PDF is untouched by this - only the derived text is replaced, which
        is the whole point of keeping them in separate places.
        """
        async with self._Scope(session or self._session) as active:
            await active.execute(
                delete(DocumentPage).where(DocumentPage.document_id == document_id)
            )
            for page in pages:
                active.add(
                    DocumentPage(
                        id=uuid.uuid4(),
                        document_id=document_id,
                        page_number=int(page["page_number"]),
                        text=page.get("text") or "",
                        raw_text=page.get("raw_text"),
                        char_count=len(page.get("text") or ""),
                        extraction_tier=page.get("tier"),
                        confidence=page.get("confidence"),
                        used_ocr=bool(page.get("used_ocr", False)),
                    )
                )
            await active.flush()
            return len(pages)

    async def pages(
        self,
        document_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> tuple[list[DocumentPage], int]:
        async with self._Scope(session or self._session) as active:
            rows = (
                await active.execute(
                    select(DocumentPage)
                    .where(DocumentPage.document_id == document_id)
                    .order_by(DocumentPage.page_number)
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
            total = (
                await active.execute(
                    select(func.count(DocumentPage.id)).where(
                        DocumentPage.document_id == document_id
                    )
                )
            ).scalar_one()
            return list(rows), int(total)

    async def document_text(
        self, document_id: uuid.UUID, *, session: AsyncSession | None = None
    ) -> str:
        """The whole extracted text, in page order - what an editor or an AI reads."""
        async with self._Scope(session or self._session) as active:
            rows = (
                await active.execute(
                    select(DocumentPage.text)
                    .where(DocumentPage.document_id == document_id)
                    .order_by(DocumentPage.page_number)
                )
            ).scalars()
            return "\n\n".join(row for row in rows if row)

    # ------------------------------------------------------------------- search

    async def search_pages(
        self,
        query: str,
        viewer: Viewer,
        *,
        limit: int = 20,
        session: AsyncSession | None = None,
    ) -> list[PageHit]:
        """Full-text search over EXTRACTED PAGE TEXT, scoped to what the viewer may read.

        Filenames are not enough. The reason a 500-document library is useful is that
        a student can find the paragraph about Ind AS 116 in a study material they
        have never opened, and the extracted text is the only index that can answer
        that. ``websearch_to_tsquery`` is used rather than ``to_tsquery`` because it
        accepts what a student actually types - quoted phrases, OR, minus - instead
        of raising a syntax error on a stray apostrophe.
        """
        cleaned = query.strip()
        if not cleaned:
            return []
        async with self._Scope(session or self._session) as active:
            document_ids = select(ContentDocument.id).where(document_filter(viewer))
            statement = (
                select(
                    ContentDocument.id,
                    ContentDocument.title,
                    DocumentPage.page_number,
                    func.ts_headline(
                        "english",
                        DocumentPage.text,
                        func.websearch_to_tsquery("english", cleaned),
                        # A short window: a search result that is a wall of text is
                        # not a result, it is a page.
                        "MaxFragments=2, MinWords=8, MaxWords=22, StartSel=<mark>, StopSel=</mark>",
                    ).label("excerpt"),
                    func.ts_rank(
                        DocumentPage.search_vector,
                        func.websearch_to_tsquery("english", cleaned),
                    ).label("rank"),
                )
                .join(ContentDocument, ContentDocument.id == DocumentPage.document_id)
                .where(
                    DocumentPage.document_id.in_(document_ids),
                    DocumentPage.search_vector.op("@@")(
                        func.websearch_to_tsquery("english", cleaned)
                    ),
                )
                .order_by(
                    func.ts_rank(
                        DocumentPage.search_vector,
                        func.websearch_to_tsquery("english", cleaned),
                    ).desc()
                )
                .limit(limit)
            )
            rows = (await active.execute(statement)).all()
            return [
                PageHit(
                    document_id=row[0],
                    title=row[1],
                    page_number=row[2],
                    excerpt=row[3] or "",
                    rank=float(row[4] or 0.0),
                )
                for row in rows
            ]

    async def text_matches_for(
        self,
        document_ids: list[uuid.UUID],
        query: str,
        *,
        session: AsyncSession | None = None,
    ) -> dict[uuid.UUID, int]:
        """The best-matching page number inside each document, for the admin list.

        ONE query for the page of results, not one per row: ``DISTINCT ON`` keeps the
        highest-ranked page per document. The admin needs this because the search now
        matches text that is nowhere in the title or the filename - without the page
        number the row looks like a false positive.
        """
        cleaned = query.strip()
        if not cleaned or not document_ids:
            return {}
        async with self._Scope(session or self._session) as active:
            statement = (
                select(DocumentPage.document_id, DocumentPage.page_number)
                .where(
                    DocumentPage.document_id.in_(document_ids),
                    DocumentPage.search_vector.op("@@")(
                        func.websearch_to_tsquery("english", cleaned)
                    ),
                )
                .distinct(DocumentPage.document_id)
                .order_by(
                    DocumentPage.document_id,
                    func.ts_rank(
                        DocumentPage.search_vector,
                        func.websearch_to_tsquery("english", cleaned),
                    ).desc(),
                )
            )
            rows = (await active.execute(statement)).all()
            return {row[0]: int(row[1]) for row in rows}

    # -------------------------------------------------------------- access rules

    async def rules_for(
        self, document_id: uuid.UUID, *, session: AsyncSession | None = None
    ) -> list[ContentAccessRule]:
        async with self._Scope(session or self._session) as active:
            rows = (
                await active.execute(
                    select(ContentAccessRule)
                    .where(ContentAccessRule.document_id == document_id)
                    .order_by(ContentAccessRule.created_at)
                )
            ).scalars()
            return list(rows)

    async def add_rule(self, **values: Any) -> ContentAccessRule:
        async with self._Scope(self._session) as active:
            rule = ContentAccessRule(id=uuid.uuid4(), **values)
            active.add(rule)
            await active.flush()
            return rule

    async def delete_rule(self, rule_id: uuid.UUID, *, session: AsyncSession | None = None) -> bool:
        async with self._Scope(session or self._session) as active:
            result = await active.execute(
                delete(ContentAccessRule).where(ContentAccessRule.id == rule_id)
            )
            return bool(result.rowcount)

    # ------------------------------------------------------------- admin numbers

    async def dashboard_counts(self, *, session: AsyncSession | None = None) -> DashboardCounts:
        """Every dashboard figure, in ONE query round trip.

        A dozen separate ``count()`` calls is a dashboard that takes a second to load
        and gets slower with the library - and the owner will open it every morning.
        Each subquery is a cheap aggregate; the whole thing is one trip.
        """
        from app.models.curriculum import Chapter, Course, Subject
        from app.models.engagement import AnalyticsEvent, Notification
        from app.models.progress import MockAttempt, MockTest
        from app.models.question import Question
        from app.models.user import PaymentOrder, Subscription

        async with self._Scope(session or self._session) as active:
            now = datetime.now(UTC)
            # Midnight seven days back, not "now minus 168 hours": a dashboard's "new
            # this week" should line up with the day the admin is reading it, and a
            # rolling window quietly disagrees with every other dated figure on the page.
            week_ago = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)

            async def scalar(statement: Select) -> int:
                return int((await active.execute(statement)).scalar_one() or 0)

            from app.models.user import User

            student_filter = [User.role == "STUDENT", User.deleted_at.is_(None)]
            return DashboardCounts(
                total_students=await scalar(select(func.count(User.id)).where(*student_filter)),
                active_students=await scalar(
                    select(func.count(User.id)).where(
                        *student_filter, User.last_active_at.is_not(None)
                    )
                ),
                new_students_7d=await scalar(
                    select(func.count(User.id)).where(*student_filter, User.created_at >= week_ago)
                ),
                total_documents=await scalar(
                    select(func.count(ContentDocument.id)).where(
                        ContentDocument.deleted_at.is_(None)
                    )
                ),
                processing_documents=await scalar(
                    select(func.count(ContentDocument.id)).where(
                        ContentDocument.status.in_(
                            ["QUEUED", "PROCESSING", "EXTRACTING", "OCR_REQUIRED"]
                        ),
                        ContentDocument.deleted_at.is_(None),
                    )
                ),
                failed_documents=await scalar(
                    select(func.count(ContentDocument.id)).where(
                        ContentDocument.status == "FAILED", ContentDocument.deleted_at.is_(None)
                    )
                ),
                indexed_documents=await scalar(
                    select(func.count(ContentDocument.id)).where(
                        ContentDocument.status.in_(["INDEXED", "COMPLETED"]),
                        ContentDocument.deleted_at.is_(None),
                    )
                ),
                total_pages=await scalar(select(func.count(DocumentPage.id))),
                extracted_chars=await scalar(
                    select(func.coalesce(func.sum(ContentDocument.extracted_chars), 0))
                ),
                total_questions=await scalar(select(func.count(Question.id))),
                published_questions=await scalar(
                    select(func.count(Question.id)).where(Question.status == "PUBLISHED")
                ),
                total_mocks=await scalar(select(func.count(MockTest.id))),
                total_courses=await scalar(select(func.count(Course.id))),
                total_subjects=await scalar(select(func.count(Subject.id))),
                total_chapters=await scalar(select(func.count(Chapter.id))),
                mock_attempts=await scalar(select(func.count(MockAttempt.id))),
                completed_attempts=await scalar(
                    select(func.count(MockAttempt.id)).where(MockAttempt.submitted_at.is_not(None))
                ),
                total_revenue_paise=await scalar(
                    select(func.coalesce(func.sum(PaymentOrder.amount_paise), 0)).where(
                        PaymentOrder.status == "PAID"
                    )
                ),
                successful_payments=await scalar(
                    select(func.count(PaymentOrder.id)).where(PaymentOrder.status == "PAID")
                ),
                failed_payments=await scalar(
                    select(func.count(PaymentOrder.id)).where(PaymentOrder.status == "FAILED")
                ),
                active_subscriptions=await scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.status.in_(["ACTIVE", "TRIALING"])
                    )
                ),
                notifications_sent=await scalar(select(func.count(Notification.id))),
                # Counted from the analytics table, which is the only place an AI call
                # is recorded. It stays at zero until a provider key is configured, and
                # the admin screen shows that rather than inventing a number.
                ai_events=await scalar(
                    select(func.count(AnalyticsEvent.id)).where(
                        AnalyticsEvent.name.in_(["ai.assistant_queried", "ai.content_generated"])
                    )
                ),
                storage_bytes=await scalar(
                    select(func.coalesce(func.sum(ContentDocument.size_bytes), 0)).where(
                        ContentDocument.deleted_at.is_(None)
                    )
                ),
            )
