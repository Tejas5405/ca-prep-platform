"""Shared Pydantic base classes.

STACK-WIDE CONVENTION: every inbound request schema inherits ``StrictRequest``.

The reason is a migration hazard found while porting the previous
implementation. Pydantic v2 in its DEFAULT lax mode coerces types:

    "yes"  -> True      "0"    -> False
    "5"    -> 5         "3.7"  -> 3.7

The validators being replaced used explicit runtime type checks and rejected
those inputs, so a plain Pydantic port silently accepts MORE than the code it
replaces. For fields like payment amounts, marks, and quality scores, coercion is
exactly how a string ends up in an integer column, or how ``"0"`` becomes a
truthy-looking ``False`` in a permission check.

Two flags are therefore mandatory on inbound schemas:

  extra="forbid"  unknown fields are rejected, not silently dropped
  strict=True     a JSON boolean must be a boolean; no coercion

Response schemas are not constrained this way - they are built by us, not sent
by a client.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    """Base for every inbound request body."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


#: A UUID reference sent in a request body.
#:
#: THE ONE DELIBERATE EXEMPTION FROM strict=True, and it is not a loophole.
#:
#: JSON has no UUID type, so every client MUST send a UUID as a string. Under
#: strict mode Pydantic refuses that with "Input should be an instance of UUID"
#: (``is_instance_of``), which makes any endpoint with a UUID body field unusable
#: over HTTP - while passing every test that constructs the model with a real
#: UUID object.
#:
#: ``strict=False`` here relaxes ONE thing: a UUID-shaped string becomes a UUID.
#: It does not re-open the coercions the strict convention exists to block - an
#: int field still rejects ``"5"`` and a bool field still rejects ``"yes"`` (both
#: asserted in tests/test_api_contract.py). A malformed string is still refused;
#: this accepts form, not nonsense.
#:
#: Use this for every UUID in an inbound schema. Writing a bare ``uuid.UUID``
#: will look correct and fail only against a real HTTP client.
UuidRef = Annotated[uuid.UUID, Field(strict=False)]


#: A calendar date sent in a request body.
#:
#: THE SAME TRAP AS UuidRef, and it was live: JSON has no date type, so every
#: client sends ``"2027-05-01"``. Under ``strict=True`` Pydantic refuses that with
#: ``date_type`` - which made ``POST /planner/generate`` and
#: ``POST /mock-attempts/{id}/submit`` impossible to call from any real HTTP
#: client, the frontend included, while passing every test that built the model
#: with a real ``date`` object. Lax parsing here accepts the ISO form ONLY; an
#: unparseable string is still refused.
DateRef = Annotated[date, Field(strict=False)]

#: A timestamp sent in a request body. Same reasoning as DateRef: ``started_at``
#: arrives as an ISO 8601 string from every client, never as a Python datetime.
TimestampRef = Annotated[datetime, Field(strict=False)]


class ResponseModel(BaseModel):
    """Base for outbound payloads.

    ``populate_by_name`` lets us expose snake_case internally while emitting the
    camelCase the documented API contract uses.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
