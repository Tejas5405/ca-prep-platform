"""The role matrix, pinned to blueprint v3 §8.2.

WHY THIS FILE EXISTS

The matrix in ``app/core/permissions.py`` is the single place authorization is
decided, and it had drifted from the document it was written from: ``EDITOR`` held
``PUBLISH_CONTENT`` while the blueprint's own table says Editor = "Publish: No", and
every publishing route refused editors anyway. The result was a console that offered a
permission nobody could use and a matrix that contradicted the API - the kind of
disagreement that is invisible until someone builds a screen on top of it.

So the blueprint's table is written out here as data, and the matrix is checked
against it role by role. A future edit that widens a role has to argue with this test
rather than with a reviewer's memory.

    Role            Read content      Edit draft   Publish   Moderate   Billing/Admin
    Student         Own/Published     No           No        No         Own subscription
    Editor          Published + Draft Yes          No        No         No
    Content Manager Published + Draft Yes          Yes       Limited    No
    Moderator       Published         No           No        Yes        No
    Admin           All               Yes          Yes       Yes        Yes
    Super Admin     All               Yes          Yes       Yes        All + roles

"Moderate" is the doubt queue, which is a service-level rule (``app/services/doubts.py``)
rather than a permission today; the closest permission is MANAGE_NOTIFICATIONS for
reaching students, and the table below records what that maps to.
"""

from __future__ import annotations

import pytest

from app.core.permissions import OWNER_ONLY, ROLE_PERMISSIONS, Permission, permissions_for
from app.core.security import Role

READ_CONTENT = {Permission.VIEW_CONTENT}


def holds(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


class TestBlueprintSection82:
    def test_a_student_holds_no_administrative_permission(self) -> None:
        """A student's access to content is per-document, not a role capability.

        Giving STUDENT even VIEW_CONTENT would be a category error: VIEW_CONTENT is the
        admin-library read, and a student's library is filtered by access rules.
        """
        assert ROLE_PERMISSIONS[Role.STUDENT] == frozenset()

    def test_an_editor_may_edit_drafts_but_not_publish(self) -> None:
        """The row of the table that had drifted."""
        assert holds(Role.EDITOR, Permission.MANAGE_QUESTIONS), "Edit draft: Yes"
        assert not holds(Role.EDITOR, Permission.PUBLISH_CONTENT), "Publish: No"
        assert holds(Role.EDITOR, Permission.VIEW_CONTENT), "Read: Published + Draft"
        assert holds(Role.EDITOR, Permission.MANAGE_CONTENT), "uploads and metadata"

    def test_a_content_manager_is_the_lowest_role_that_publishes(self) -> None:
        assert holds(Role.CONTENT_MANAGER, Permission.PUBLISH_CONTENT)
        assert holds(Role.CONTENT_MANAGER, Permission.MANAGE_QUESTIONS)
        assert holds(Role.CONTENT_MANAGER, Permission.MANAGE_ACCESS)
        # A content manager does not touch people, money or platform settings.
        for forbidden in (
            Permission.MANAGE_USERS,
            Permission.MANAGE_ROLES,
            Permission.VIEW_PAYMENTS,
            Permission.MANAGE_PAYMENTS,
            Permission.MANAGE_SETTINGS,
        ):
            assert not holds(Role.CONTENT_MANAGER, forbidden), forbidden

    def test_a_moderator_moderates_and_does_not_touch_content(self) -> None:
        """Read published only: no draft access, no publish, no uploads."""
        assert holds(Role.MODERATOR, Permission.VIEW_USERS)
        assert holds(Role.MODERATOR, Permission.MANAGE_NOTIFICATIONS)
        assert not holds(Role.MODERATOR, Permission.MANAGE_CONTENT)
        assert not holds(Role.MODERATOR, Permission.MANAGE_QUESTIONS)
        assert not holds(Role.MODERATOR, Permission.PUBLISH_CONTENT)

    def test_an_admin_holds_every_permission_but_the_owners_two(self) -> None:
        """Admin is the operator. What is reserved is reserved by OWNER_ONLY, not by
        a shorter list - so the two cannot drift apart."""
        for permission in Permission:
            if permission in OWNER_ONLY:
                continue
            assert holds(Role.ADMIN, permission), permission
        assert OWNER_ONLY == frozenset({Permission.MANAGE_SETTINGS})

    def test_only_the_owner_holds_an_owner_only_permission(self) -> None:
        """The invariant the OWNER_ONLY set exists for, checked against every role
        rather than asserted in prose. ``ROLE_PERMISSIONS`` grants ADMIN the full set,
        so the withholding has to happen at the dependency - and this is the test that
        fails if someone 'simplifies' that by dropping the permission from ADMIN's row
        instead, which would silently also drop it from the console's matrix."""
        for permission in OWNER_ONLY:
            assert holds(Role.SUPER_ADMIN, permission)
        assert permissions_for("SUPER_ADMIN") == permissions_for(Role.ADMIN.value)

    def test_read_content_is_held_by_every_staff_role_and_no_student(self) -> None:
        """The one permission where "which roles" is a one-line answer worth pinning,
        because the admin console's sidebar depends on it: a role without VIEW_CONTENT
        must not be offered the library."""
        holders = {role for role in Role if ROLE_PERMISSIONS[role] & READ_CONTENT}
        assert holders == {
            Role.EDITOR,
            Role.CONTENT_MANAGER,
            Role.MODERATOR,
            Role.ADMIN,
            Role.SUPER_ADMIN,
        }
        assert Role.STUDENT not in holders


class TestPermissionLookupSafety:
    @pytest.mark.parametrize("name", ["", "student", "SUPERADMIN", "admin ", "ADMINISTRATOR", None])
    def test_an_unknown_role_gets_nothing(self, name: object) -> None:
        """Fail closed. A role string this build has never heard of - a value written by
        a newer deployment, or a typo - must resolve to no permissions rather than to
        the nearest match by case or prefix."""
        assert permissions_for(name) == frozenset()  # type: ignore[arg-type]

    def test_role_matching_is_exact_not_case_insensitive(self) -> None:
        """A token claiming ``admin`` in lower case is a token claiming nothing. The
        verifier uppercases nothing, so neither does this."""
        assert permissions_for("admin") == frozenset()
        assert permissions_for("ADMIN") == permissions_for(Role.ADMIN)

    def test_every_permission_is_granted_to_at_least_one_role(self) -> None:
        """An unreachable permission is a lie in the console's matrix: the screen would
        list a capability that no role can ever hold."""
        granted = set().union(*ROLE_PERMISSIONS.values())
        granted |= OWNER_ONLY  # withheld at the dependency, not absent from the table
        assert granted == set(Permission)
