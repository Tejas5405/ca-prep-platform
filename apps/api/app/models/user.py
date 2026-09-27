"""Users, profiles, subscriptions and referral state.

Blueprint v3 §8.2, and the most important rule in the whole schema:

    "Do not store or manage student passwords in PostgreSQL.
     Supabase Auth owns password credentials."

Consequences, applied here:
  * there is no ``password_hash`` column, and there must never be one
  * ``auth_user_id`` is the Supabase ``sub`` claim, unique and indexed
  * the email is held for display and notification routing only; authentication
    never reads it

A second, subtler rule: this table is NOT the source of truth for entitlements
either. Subscriptions are, and they are a separate row so that a lapsed payment
does not require mutating the user record (v3 §10: "entitlements come from
PostgreSQL and cache is invalidated on subscription change").
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UuidMixin
from app.models.enums import (
    SubscriptionStatus,
    SubscriptionTier,
    UserRole,
)


class User(Base, UuidMixin, TimestampMixin, SoftDeleteMixin):
    """Application user, linked to a Supabase Auth identity."""

    __tablename__ = "users"

    #: Supabase ``sub`` claim. Nullable ONLY to allow server-created service
    #: accounts; every student row must have one.
    auth_user_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UserRole.STUDENT.value,
        server_default=sa_text("'STUDENT'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("true")
    )

    # ---- study context ---------------------------------------------------
    target_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    syllabus_scheme: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NEW_2024", server_default=sa_text("'NEW_2024'")
    )
    daily_goal_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120, server_default=sa_text("120")
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Asia/Kolkata",
        server_default=sa_text("'Asia/Kolkata'"),
    )
    locale: Mapped[str] = mapped_column(
        String(10), nullable=False, default="en-IN", server_default=sa_text("'en-IN'")
    )

    # ---- onboarding / consent -------------------------------------------
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Referral code this user owns and can share.
    referral_code: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True)
    #: Referral code this user was signed up with.
    referred_by_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('STUDENT','EDITOR','CONTENT_MANAGER','MODERATOR','ADMIN','SUPER_ADMIN')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "target_level IS NULL OR target_level IN ('FOUNDATION','INTERMEDIATE','FINAL')",
            name="ck_users_target_level",
        ),
        CheckConstraint(
            "syllabus_scheme IN ('OLD_2016','NEW_2024','UNMAPPED')",
            name="ck_users_scheme",
        ),
        CheckConstraint("daily_goal_minutes BETWEEN 15 AND 900", name="ck_users_daily_goal"),
        Index("idx_users_role_active", "role", "is_active"),
        Index("idx_users_target_exam", "target_exam_date"),
    )


class UserProfile(Base, UuidMixin, TimestampMixin):
    """Extended profile, split out so the hot ``users`` row stays narrow."""

    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    college: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempt_number: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Aggregate accuracy snapshot, recomputed by a nightly job. Denormalised
    #: because the planner needs it on every plan generation.
    overall_accuracy: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    current_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    longest_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    total_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa_text("0")
    )
    current_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=sa_text("1")
    )

    __table_args__ = (
        # Streak and points are cached aggregates of the ledger and the activity
        # table. They are NEVER the source of truth - see app.services.gamification.
        CheckConstraint("current_streak >= 0", name="ck_profile_streak_non_negative"),
        CheckConstraint("total_points >= 0", name="ck_profile_points_non_negative"),
    )


class Subscription(Base, UuidMixin, TimestampMixin):
    """Subscription state. The source of truth for entitlements."""

    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tier: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubscriptionTier.FREE.value,
        server_default=sa_text("'FREE'::character varying"),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubscriptionStatus.ACTIVE.value,
        server_default=sa_text("'ACTIVE'::character varying"),
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_renew: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )

    # ---- payment linkage -------------------------------------------------
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)  # razorpay
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Amount in paise. Stored as an integer because floating-point currency is
    #: how a rounding error becomes a support ticket.
    amount_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default=sa_text("'INR'")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        CheckConstraint("tier IN ('FREE','PREMIUM','PREMIUM_PLUS')", name="ck_subscriptions_tier"),
        CheckConstraint(
            "status IN ('ACTIVE','EXPIRED','CANCELLED','PAST_DUE','TRIAL')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint(
            "amount_paise IS NULL OR amount_paise >= 0",
            name="ck_subscriptions_amount",
        ),
        # At most one ACTIVE subscription per user. Enforced in the database so a
        # concurrent webhook cannot create a second entitlement row - this is the
        # kind of double-write that grants free access.
        Index(
            "uq_active_subscription_per_user",
            "user_id",
            unique=True,
            postgresql_where=sa_text("status IN ('ACTIVE','TRIAL')"),
        ),
        Index("idx_subscriptions_expiry", "status", "expires_at"),
    )


class PaymentEvent(Base, UuidMixin, TimestampMixin):
    """Raw payment gateway events, for idempotency and reconciliation.

    Blueprint v3 §13.1 requires webhook handling to be idempotent. Idempotency
    needs a record of what has already been processed, keyed on the provider's
    event id, which is what this table is. Without it a retried webhook
    double-credits a subscription or double-counts revenue.
    """

    __tablename__ = "payment_events"

    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    #: Provider's event id. Unique - the actual idempotency guarantee.
    event_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("idx_payment_events_unprocessed", "processed_at"),
        Index("idx_payment_events_type", "event_type", "created_at"),
    )


class PaymentOrder(Base, UuidMixin, TimestampMixin):
    """A checkout intent: what a student is buying, and for how much.

    Blueprint v3 §13.1 requires the order to be created server-side and its
    provider identifiers persisted "for auditability". This table is where that
    happens, and it is the counterpart to ``subscriptions``:

        payment_orders   intent   - created before the gateway is called
        payment_events   evidence - the signed webhook, deduplicated
        subscriptions    outcome  - the entitlement that results

    WHY THE AMOUNT IS STORED HERE RATHER THAN LOOKED UP LATER

    The amount charged is frozen at creation. When a payment is claimed, the
    figure Razorpay reports is compared against THIS row. If it were instead
    compared against the current plan price, then a price change between checkout
    and settlement would make a legitimate payment look like fraud - and, in the
    other direction, an attacker who could write to the plan catalogue would be
    able to re-price a pending order.

    Note that ``status`` here is the ORDER's lifecycle. It is deliberately not the
    same vocabulary as ``subscriptions.status``: an order is PAID, a subscription
    is ACTIVE, and conflating them is how "paid but not entitled" becomes
    undiagnosable. Entitlement is read from ``subscriptions`` alone.
    """

    __tablename__ = "payment_orders"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_code: Mapped[str] = mapped_column(String(40), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Paise. Integer, because floating-point currency is how a rounding error
    #: becomes a support ticket.
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default=sa_text("'INR'")
    )
    #: Our opaque receipt id, echoed back by the provider. Unique, so a replayed
    #: create-order call cannot produce two orders for one payment.
    receipt: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)

    # ---- gateway linkage -------------------------------------------------
    provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default="razorpay", server_default=sa_text("'razorpay'")
    )
    provider_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Whatever the gateway last told us. For reconciliation only - entitlement
    #: never reads this column.
    provider_status: Mapped[str | None] = mapped_column(String(40), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="CREATED", server_default=sa_text("'CREATED'")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        CheckConstraint("amount_paise >= 0", name="ck_payment_orders_amount"),
        CheckConstraint(
            "status IN ('CREATED','PAID','FAILED','EXPIRED')", name="ck_payment_orders_status"
        ),
        CheckConstraint("tier IN ('FREE','PREMIUM','PREMIUM_PLUS')", name="ck_payment_orders_tier"),
        # Partial unique indexes: they collide only on the rows that actually
        # carry a gateway id, so many orders may be pending while a payment is
        # being processed, but one gateway identifier can never name two orders.
        Index(
            "uq_payment_orders_provider_order_id",
            "provider_order_id",
            unique=True,
            postgresql_where=sa_text("provider_order_id IS NOT NULL"),
        ),
        Index(
            "uq_payment_orders_provider_payment_id",
            "provider_payment_id",
            unique=True,
            postgresql_where=sa_text("provider_payment_id IS NOT NULL"),
        ),
        Index("idx_payment_orders_user", "user_id", "created_at"),
        Index("idx_payment_orders_open", "status", "expires_at"),
    )


class PaymentGatewayConfig(Base, UuidMixin, TimestampMixin):
    """Razorpay credentials saved by the owner. Not a settings row.

    ``platform_settings`` is readable, and some of it is public. A key in that
    table would be a key on an API. This table has no public read. The secret
    columns are written by the owner route and read by the payment routes.
    Nothing serialises them back to a client.
    """

    __tablename__ = "payment_gateway_config"

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    key_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    key_secret: Mapped[str | None] = mapped_column(String(200), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (UniqueConstraint("provider", name="uq_payment_gateway_provider"),)
