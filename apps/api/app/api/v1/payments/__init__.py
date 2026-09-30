"""Payments: plans, orders, confirmation, webhook, gateway config.

Split from the former single-file app/api/v1/payments.py. `router` carries the same
paths, methods, tags, names and status codes as before, in the same order.

RE-EXPORTS. Beyond the moved defs, this package re-exports `get_settings`, which
was an IMPORT in the original module. tests/integration/test_postgres_routes.py
does `app.dependency_overrides[payments_route.get_settings]`; without the
re-export that line raises AttributeError and the whole payments integration
group fails. An import is part of a module public surface just as much as a def.

TESTS. `SqlBillingStore` and `RazorpayClient` are bound per sub-module, so tests
patch each sub-module that holds one. The fakes and assertions are unchanged."""

from __future__ import annotations

from fastapi import APIRouter

# Re-exported imports: these were part of this module's public surface
# before the split, and code outside the package still refers to them
# through it. The name is bound here, not in any sub-module.
from app.core.config import get_settings

from . import confirm as _confirm
from . import gateway as _gateway
from . import orders as _orders
from . import plans as _plans
from . import webhooks as _webhooks
from ._shared import (
    ORDER_TTL,
    SIGNATURE_HEADER,
    ConfirmPaymentIn,
    CreateOrderIn,
    get_billing_user,
    logger,
)
from .confirm import confirm_payment
from .gateway import GatewayIn, read_gateway, write_gateway
from .orders import create_order, get_subscription
from .plans import list_plans
from .webhooks import razorpay_webhook

router = APIRouter()
router.routes.extend(_plans.router.routes)
router.routes.extend(_orders.router.routes)
router.routes.extend(_confirm.router.routes)
router.routes.extend(_webhooks.router.routes)
router.routes.extend(_gateway.router.routes)

__all__ = [
    "ORDER_TTL",
    "SIGNATURE_HEADER",
    "ConfirmPaymentIn",
    "CreateOrderIn",
    "GatewayIn",
    "confirm_payment",
    "create_order",
    "get_billing_user",
    "get_settings",
    "get_subscription",
    "list_plans",
    "logger",
    "razorpay_webhook",
    "read_gateway",
    "router",
    "write_gateway",
]
