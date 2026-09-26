"""SQLAlchemy declarative base and shared column conventions.

Blueprint v3 §9: PostgreSQL 16 is the system of record. Conventions applied to
every table:

  * UUID primary keys (``uuid_generate_v4``) - non-enumerable in URLs and safe to
    generate client-side without a round trip.
  * ``created_at`` / ``updated_at`` as ``TIMESTAMPTZ``, never naive timestamps.
  * Soft delete via ``deleted_at`` on user-generated content, so an accidental
    delete does not destroy a student's notes or a doubt thread.

TIMESTAMPTZ IS NOT OPTIONAL. A student in India and a student in Dubai must see
the same attempt history, and a naive ``TIMESTAMP`` column silently depends on the
server's timezone setting. Worse, the ``next_review_at`` bug in the superseded
spaced-repetition code came from exactly this class of mistake - building a UTC
instant through local-time arithmetic. Store instants as instants.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UuidMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
