"""The owner back office: platform numbers, people, permissions, settings, logs.

Split from the former single-file app/api/v1/admin.py. `router` carries the same
paths, methods, tags, names and status codes as before, in the same order. Every
`require_permission` check is byte-for-byte where it was."""

from __future__ import annotations

from fastapi import APIRouter

from . import badges as _badges
from . import dashboard as _dashboard
from . import notifications as _notifications
from . import payments as _payments
from . import permissions as _permissions
from . import settings as _settings
from . import users as _users
from ._shared import ASSIGNABLE_ROLES
from .badges import BadgeIn, award_badge, create_badge, list_badges, update_badge
from .dashboard import analytics_summary, audit_log, dashboard
from .notifications import BroadcastIn, notification_history, send_notification
from .payments import ORDER_STATUSES, list_payment_events, list_payment_orders
from .permissions import my_permissions, permission_matrix
from .settings import SettingIn, list_settings, put_setting
from .users import UpdateUserIn, get_user, list_users, update_user

router = APIRouter()
router.routes.extend(_dashboard.router.routes)
router.routes.extend(_users.router.routes)
router.routes.extend(_permissions.router.routes)
router.routes.extend(_settings.router.routes)
router.routes.extend(_notifications.router.routes)
router.routes.extend(_badges.router.routes)
router.routes.extend(_payments.router.routes)

__all__ = [
    "ASSIGNABLE_ROLES",
    "ORDER_STATUSES",
    "BadgeIn",
    "BroadcastIn",
    "SettingIn",
    "UpdateUserIn",
    "analytics_summary",
    "audit_log",
    "award_badge",
    "create_badge",
    "dashboard",
    "get_user",
    "list_badges",
    "list_payment_events",
    "list_payment_orders",
    "list_settings",
    "list_users",
    "my_permissions",
    "notification_history",
    "permission_matrix",
    "put_setting",
    "router",
    "send_notification",
    "update_badge",
    "update_user",
]
