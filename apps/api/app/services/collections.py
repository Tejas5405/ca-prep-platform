"""Collections - filter validation, LDR migration and quotas.

SECURITY BOUNDARY. Smart-collection filters arrive from the client as JSON. They
are validated against a strict allowlist and translated into a typed predicate.
Raw JSON is never forwarded to SQLAlchemy - an unchecked filters blob reaching
the query builder is a data-exfiltration vector (blueprint v3 §15, "Input
validation with Pydantic").

The blueprint models collections as ``collections`` + ``collection_questions``
(§9.1). The existing LDR list is retained as a lazily-seeded system collection
rather than replaced, so no user data is destroyed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SYSTEM_COLLECTION_NAME = "Marked for later resolution"
MAX_FILTER_VALUE_LENGTH = 64

Difficulty = Literal["EASY", "MEDIUM", "HARD"]
UserAccuracy = Literal["wrong", "skipped", "correct"]
Weightage = Literal["HIGH", "MEDIUM", "LOW"]
SyllabusScheme = Literal["OLD_2016", "NEW_2024", "UNMAPPED"]


class CollectionFilters(BaseModel):
    """Validated smart-collection predicate.

    TWO config flags are load-bearing here, and both guard against a silent
    downgrade of validation strength during the stack migration.

    ``extra="forbid"`` - Pydantic v2 IGNORES unknown fields by default, so
    without this a client could post ``{"password_hash": ...}`` or an unexpected
    operator key and have it silently dropped, or forwarded by a careless
    repository. Forbidding extras turns a silent accept into a 422.

    This is the direct analogue of a bug found in the superseded TypeScript
    implementation, where plain interfaces let ``{"role":"ADMIN"}`` through the
    request-validation layer unchallenged.

    ``strict=True`` - in its default lax mode Pydantic COERCES types:
    ``"yes" -> True``, ``"0" -> False``, ``"5" -> 5``. The validator this
    replaced used an explicit ``typeof value !== 'boolean'`` check and rejected
    those inputs. A naive Pydantic port therefore accepts strictly more than the
    code it replaces, which is a validation downgrade dressed up as a rewrite.
    Strict mode restores the original contract: a JSON boolean must be a JSON
    boolean.

    Adopt the same convention for every inbound request schema in this service.
    """

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    subject_id: str | None = Field(default=None, max_length=MAX_FILTER_VALUE_LENGTH)
    chapter_id: str | None = Field(default=None, max_length=MAX_FILTER_VALUE_LENGTH)
    difficulty: Difficulty | None = None
    user_accuracy: UserAccuracy | None = None
    weightage: Weightage | None = None
    syllabus_scheme: SyllabusScheme | None = None
    finance_act_year: str | None = Field(default=None, max_length=MAX_FILTER_VALUE_LENGTH)
    is_historical: bool | None = None

    @field_validator("subject_id", "chapter_id", "finance_act_year")
    @classmethod
    def reject_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("filter value must not be blank")
        return v


class FilterValidationError(ValueError):
    """Raised when client-supplied filters are not acceptable."""

    def __init__(self, message: str, field_name: str | None = None) -> None:
        super().__init__(message)
        self.field_name = field_name


def validate_filters(raw: Any) -> CollectionFilters:
    """Validate untrusted filter input, converting Pydantic errors to ours."""
    if raw is None:
        return CollectionFilters()
    if not isinstance(raw, dict):
        raise FilterValidationError("filters must be a JSON object")
    try:
        return CollectionFilters.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ())) or None
        raise FilterValidationError(first.get("msg", "invalid filters"), location) from exc


def canonical_filters(filters: CollectionFilters) -> str:
    """Stable string form so cache keys do not depend on key order."""
    items = filters.model_dump(exclude_none=True)
    return "&".join(f"{k}={items[k]}" for k in sorted(items))


# --------------------------------------------------------------- LDR migration


@dataclass(frozen=True)
class LdrRow:
    user_id: str
    question_id: str
    marked_at: datetime
    note: str | None = None


@dataclass(frozen=True)
class SeedItem:
    question_id: str
    note: str | None
    added_at: datetime


@dataclass(frozen=True)
class SeedCollection:
    name: str
    kind: str
    is_system: bool
    items: list[SeedItem] = field(default_factory=list)


def seed_ldr_collection(rows: list[LdrRow]) -> SeedCollection:
    """Build the system collection that supersedes the standalone LDR list.

    Pure and idempotent: duplicate question ids collapse to the earliest mark,
    merging any non-empty note. Running this twice yields identical output, so a
    retried migration cannot duplicate rows.
    """
    by_question: dict[str, SeedItem] = {}
    for row in sorted(rows, key=lambda r: r.marked_at):
        existing = by_question.get(row.question_id)
        if existing is None:
            by_question[row.question_id] = SeedItem(
                question_id=row.question_id,
                note=row.note,
                added_at=row.marked_at,
            )
        elif not existing.note and row.note:
            by_question[row.question_id] = SeedItem(
                question_id=existing.question_id,
                note=row.note,
                added_at=existing.added_at,
            )

    return SeedCollection(
        name=SYSTEM_COLLECTION_NAME,
        kind="MANUAL",
        is_system=True,
        items=list(by_question.values()),
    )


# --------------------------------------------------------------- quotas

FREE_TIER_MAX_COLLECTIONS = 5
FREE_TIER_MAX_QUESTIONS = 200


@dataclass(frozen=True)
class QuotaCheck:
    allowed: bool
    reason: str | None = None
    upgrade_required: bool = False


def check_collection_quota(
    tier: str,
    current_collections: int,
    current_questions: int,
    adding: int,
) -> QuotaCheck:
    if tier.upper() == "PREMIUM":
        return QuotaCheck(allowed=True)
    if adding > 0 and current_collections >= FREE_TIER_MAX_COLLECTIONS:
        return QuotaCheck(False, "COLLECTION_LIMIT", True)
    if current_questions + adding > FREE_TIER_MAX_QUESTIONS:
        return QuotaCheck(False, "QUESTION_LIMIT", True)
    return QuotaCheck(allowed=True)
