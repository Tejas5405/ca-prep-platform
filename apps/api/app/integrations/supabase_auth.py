"""The one Supabase Auth Admin call this platform makes: setting a role claim.

WHY THIS EXISTS AT ALL

Roles are enforced from the token: ``require_role`` reads the principal's role, and
the principal's role comes from the verified claim. So a role that is only written
to the database row is a role the user CANNOT USE - the editor is promoted, and
their next request still says STUDENT, because the claim is minted by Supabase Auth
at sign-in and nothing in this process can change it.

The claim lives in ``app_metadata``, which only the Admin API can write. That is
exactly what makes it a trustworthy authorization input (``user_metadata`` is
user-writable, and reading a role from it was a real privilege-escalation bug in
this codebase's history). The consequence is that a working role assignment needs
two writes: the row, and the claim. This module is the second one.

WHAT HAPPENS WHEN IT IS NOT CONFIGURED

``SUPABASE_SECRET_KEY`` may be absent - it is a server-side secret and a deployment
can run without it, with Storage and identity verification both fine. A missing key
must not be reported as a failed promotion, because the row WAS updated and the
claim is the part that lags. So the caller is told the truth in two parts:
``claim_updated`` says whether the token will carry the new role now, and the
response echoes the extra step an operator needs. Silently returning success would
leave an "editor" whose permissions are a mystery to everyone, including them.

The legacy ``service_role`` JWT is accepted in the same variable and needs a bearer
header - the two key systems authenticate differently, which is the trap
``SupabaseStorage._build_headers`` documents. Same rule here, and it is asserted by
a test rather than left to memory.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class AuthAdminError(RuntimeError):
    """The Auth Admin API refused the write.

    Raised rather than swallowed: a promotion that did not take effect must not be
    reported as a promotion. The message carries the status and the service's own
    text, because the two common failures - a revoked key and an unknown user id -
    look identical in a bare traceback.
    """


def claim_headers(key: str) -> dict[str, str]:
    """Auth headers for the key format in use (see the module docstring).

    ``sb_secret_...`` keys are opaque strings, not JWTs, and must go on ``apikey``
    only. A legacy ``service_role`` key IS a JWT and additionally needs the bearer
    form.
    """
    headers = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


class SupabaseAuthAdmin:
    """Sets the ``app_metadata.role`` claim for one user."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http
        self._owns_http = http is None

    @property
    def configured(self) -> bool:
        return bool(self._settings.supabase_url and self._settings.supabase_secret_key)

    async def __aenter__(self) -> SupabaseAuthAdmin:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
            self._owns_http = True
        return self._http

    async def set_role_claim(self, auth_user_id: str, role: str) -> bool:
        """Write ``app_metadata.role`` and report whether it took effect.

        Returns False only for the unconfigured case. A configured call that fails
        raises: the difference matters, because "no key in this environment" is a
        known state an operator can fix, while "Supabase said 401" means the key in
        use is wrong and the promotion loop is broken.

        ``PUT /auth/v1/admin/users/{id}`` merges the supplied ``app_metadata`` keys
        into the existing bag rather than replacing it, so an unrelated claim set by
        another integration survives - verified behavior, and the reason this uses
        the users endpoint rather than rewriting the whole metadata document.
        """
        if not self.configured:
            logger.warning(
                "Role claim not written for %s: no SUPABASE_SECRET_KEY in this "
                "environment. The database row was updated; the token will keep the "
                "old role until the claim is set.",
                auth_user_id,
            )
            return False

        url = f"{self._settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{auth_user_id}"
        headers = claim_headers(self._settings.supabase_secret_key or "")
        response = await self._client().put(
            url, headers=headers, json={"app_metadata": {"role": role}}
        )
        if response.status_code not in (200, 201):
            raise AuthAdminError(
                f"Supabase Auth refused the role write for {auth_user_id} "
                f"({response.status_code}): {response.text[:300]}"
            )
        return True
