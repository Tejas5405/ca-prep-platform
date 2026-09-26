"""Collections - a student's own grouping of questions.

WHY THIS TABLE EXISTS AT ALL, GIVEN THE BOOKMARK ALREADY DID

The per-question bookmark ("mark for review") answers one question well: *bring
this back to me*. It does not answer the question a student actually asks in week
three of preparation - "everything I keep losing marks on in Costing, in one
place, that I can work through before the mock". That is a collection: a named set
the student owns and curates.

The distinction is deliberate and worth keeping in the model rather than
flattening:

  * the bookmark is a FLAG on a question the student has already met. It is written
    by the practice loop itself, it lives on ``user_question_progress``, and one
    boolean is the whole of its state.
  * a collection is a CONTAINER the student creates, names, and can put questions
    into before or after answering them. It has its own identity, its own
    membership rows and its own ordering.

A single table with a nullable ``collection_id`` on the progress row was the
tempting shortcut, and it fails on the first real use: the same question belongs in
two collections ("revise before mock 3" and "weak in Ind AS 116") but there is one
progress row per student/question, so the second membership has nowhere to go.

SMART COLLECTIONS

``kind = SMART`` stores a validated filter predicate in ``filters`` (see
``app/services/collections.py``, which validates the JSON against an allowlist
before it ever reaches SQLAlchemy) and resolves its members at read time.
``kind = MANUAL`` stores explicit rows in ``collection_questions``. Both are
surfaced through the same route shape, because from the student's side they are the
same thing: a set of questions with a name on it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidMixin


class Collection(Base, UuidMixin, TimestampMixin):
    """A named set of questions belonging to one student."""

    __tablename__ = "collections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="MANUAL",
        server_default=sa_text("'MANUAL'::character varying"),
    )
    #: Validated smart-collection predicate. NULL for a manual collection.
    #:
    #: `none_as_null=True` IS NOT DECORATION. SQLAlchemy's JSON types store a Python
    #: ``None`` as the JSON value ``null`` unless told otherwise, and JSON ``null`` is
    #: not SQL NULL - so `filters IS NULL` was false for every manual collection and
    #: `ck_collection_filters_match_kind` rejected the insert. The failure is worth
    #: recording because the database caught it and the request did not: the route
    #: returned a 409 "name already used", which is what a violation of the OTHER
    #: constraint on this table would produce, and every create of a MANUAL
    #: collection looked like a duplicate-name bug.
    filters: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    #: Reserved for shared collections; nothing publishes one yet, and the column is
    #: here so the route can refuse rather than promise.
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    #: Denormalised count of explicit members, maintained in the same transaction as
    #: the membership rows. A list of twenty collections must not run twenty counts.
    item_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    #: System collections (the LDR list is seeded as one) cannot be renamed or
    #: deleted by the student, because the product depends on them existing.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )

    __table_args__ = (
        # One name per student. Two collections called "weak chapters" is a
        # student who cannot find anything, which is the problem the feature exists
        # to solve.
        UniqueConstraint("user_id", "name", name="uq_collection_user_name"),
        CheckConstraint("kind IN ('MANUAL','SMART')", name="ck_collection_kind"),
        # A smart collection without filters matches the entire bank, which is a
        # surprising thing to create by accident.
        CheckConstraint(
            "(kind = 'SMART' AND filters IS NOT NULL) OR (kind = 'MANUAL' AND filters IS NULL)",
            name="ck_collection_filters_match_kind",
        ),
        CheckConstraint("item_count >= 0", name="ck_collection_item_count"),
        Index("idx_collections_user", "user_id", "created_at"),
    )


class CollectionQuestion(Base, UuidMixin, TimestampMixin):
    """One question in one collection.

    A join table with its own primary key rather than a composite key on
    ``(collection_id, question_id)``: it carries ``note`` and ``position``, which
    are per-membership data, and the audit trail needs a stable row identity to
    point at when a student asks why something moved.
    """

    __tablename__ = "collection_questions"

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    #: The student's own note about why this question is here. This is the feature,
    #: not decoration: "got the treatment right, lost the marks on presentation" is
    #: what makes a collection worth returning to a week later.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )

    __table_args__ = (
        UniqueConstraint("collection_id", "question_id", name="uq_collection_question"),
        Index("idx_collection_questions_list", "collection_id", "position", "created_at"),
        # Reverse lookup: "which of my collections is this question in?" - asked
        # whenever a question screen renders its bookmark state.
        Index("idx_collection_questions_question", "question_id"),
    )
