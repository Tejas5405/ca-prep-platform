"""Payment orders.

Revision ID: 0003_payments
Revises: 0002_ingestion
Create Date: 2026-09-25

Adds ``payment_orders`` - the missing half of blueprint v3 §13.1.

WHY THIS TABLE IS NEEDED, GIVEN subscriptions AND payment_events ALREADY EXIST
=============================================================================
The initial schema has a ``subscriptions`` table (the entitlement record) and a
``payment_events`` table (raw provider webhooks, for idempotency). Neither can
answer the question the checkout flow actually asks:

    "User U claims they paid for plan P. Did they, and for how much?"

``payment_events`` is keyed on the provider's event id and its rows are written
WHEN a webhook arrives - so it does not exist yet at the moment the order is
created, which is precisely when the intent must be recorded. And
``subscriptions`` cannot hold it either: its status CHECK constraint allows only
ACTIVE/EXPIRED/CANCELLED/PAST_DUE/TRIAL, so there is no way to represent "order
placed, not yet paid" without inventing a status that means the opposite of what
the table is for.

So the order gets its own table, and the flow becomes verifiable at every step:

    payment_orders   intent   - created here, amount fixed HERE
    razorpay         gateway  - the money
    payment_events   evidence - the signed webhook, deduplicated on event id
    subscriptions    outcome  - the entitlement

The amount is stored on the order at creation time. When a payment is later
claimed, the amount Razorpay reports is compared against THIS row, not against
whatever the plan costs today - so a price change made between checkout and
webhook cannot retroactively invalidate a legitimate payment, and a client cannot
substitute a cheaper plan after the fact.

The unique constraint on ``provider_order_id`` is partial (NOT NULL only), because
the column is nullable until the gateway responds; a plain UNIQUE would allow any
number of NULLs, which is correct, but the partial form makes the intent explicit
and indexes only the rows that can collide.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_payments"
down_revision = "0002_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_code", sa.String(40), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        #: Amount in paise, fixed at creation. Integer, never float.
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        #: Our receipt id, echoed by the provider. Unique so a replay of the same
        #: receipt cannot create a second order.
        sa.Column("receipt", sa.String(40), nullable=False),
        #: Gateway identifiers, persisted for auditability (§13.1).
        sa.Column("provider", sa.String(30), nullable=False, server_default="razorpay"),
        sa.Column("provider_order_id", sa.String(120), nullable=True),
        sa.Column("provider_payment_id", sa.String(120), nullable=True),
        #: Observed gateway status, for reconciliation only. Entitlement never
        #: reads this column.
        sa.Column("provider_status", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("amount_paise >= 0", name="ck_payment_orders_amount"),
        sa.CheckConstraint(
            "status IN ('CREATED','PAID','FAILED','EXPIRED')", name="ck_payment_orders_status"
        ),
        sa.CheckConstraint(
            "tier IN ('FREE','PREMIUM','PREMIUM_PLUS')", name="ck_payment_orders_tier"
        ),
        sa.UniqueConstraint("receipt", name="uq_payment_orders_receipt"),
    )

    # Partial unique index: collides only on rows that actually carry a gateway id.
    op.create_index(
        "uq_payment_orders_provider_order_id",
        "payment_orders",
        ["provider_order_id"],
        unique=True,
        postgresql_where=sa.text("provider_order_id IS NOT NULL"),
    )
    # The gateway payment id is the lookup key when a webhook arrives before the
    # client callback, so it needs to be just as unique.
    op.create_index(
        "uq_payment_orders_provider_payment_id",
        "payment_orders",
        ["provider_payment_id"],
        unique=True,
        postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
    )
    op.create_index("idx_payment_orders_user", "payment_orders", ["user_id", "created_at"])
    op.create_index("idx_payment_orders_open", "payment_orders", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("idx_payment_orders_open", table_name="payment_orders")
    op.drop_index("idx_payment_orders_user", table_name="payment_orders")
    op.drop_index("uq_payment_orders_provider_payment_id", table_name="payment_orders")
    op.drop_index("uq_payment_orders_provider_order_id", table_name="payment_orders")
    op.drop_table("payment_orders")
