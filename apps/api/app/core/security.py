"""Supabase Auth JWT verification and application authorization.

STACK: identity, database and file storage are all Supabase. This supersedes the
earlier Firebase Auth amendment (SA-05) - recorded as SA-09 in
``docs/architecture/stack-amendments.md``.

Blueprint v3 §8.1: React signs the student in with the Supabase client SDK, which
holds the session and refreshes it, and sends ``Authorization: Bearer
<access_token>`` to FastAPI. FastAPI verifies the token against the project's
published JWKS before applying role checks.

NO SHARED SECRET ON THE SERVER. The project signs access tokens with an
asymmetric key (ES256), so verification uses the public JWKS at
``<project>/auth/v1/.well-known/jwks.json``. That is strictly better than the
symmetric alternative: a leaked verify-only public key is harmless, nothing needs
the signing key, and key rotation is picked up automatically. It also removes the
HS256 confusion attack by construction - HS256 is not in the accepted algorithm
set, so there is no shared secret to confuse.

=============================================================================
THE FIVE MISTAKES THIS MODULE IS WRITTEN TO PREVENT
=============================================================================
Each is a real, commonly-shipped vulnerability in a Supabase + FastAPI
integration, and each has a test in ``tests/test_security.py``.

1. **Reading the role from ``user_metadata`` - the mistake the previous
   implementation made, in a different framework.**
   Supabase exposes two metadata bags, and they are NOT equivalent:

     * ``user_metadata``    WRITABLE BY THE USER. ``supabase.auth.updateUser({
                            data: { role: "ADMIN" } })`` puts whatever the student
                            likes in it, and the token is then validly signed
                            with the new value inside. Reading a role from here is
                            self-service privilege escalation.
     * ``app_metadata``     WRITABLE ONLY SERVER-SIDE, through the Admin API. A
                            student cannot change it, which is what makes it a
                            trustworthy authorization input.

   The role is read from ``app_metadata.role`` (or a namespaced top-level claim
   if a Custom Access Token Hook is configured) and from nowhere else.

   NOTE THE BARE ``role`` CLAIM. Supabase's own top-level ``role`` is real and
   meaningful - it holds ``"authenticated"`` (or ``"anon"``) - so it must never be
   treated as the application role. The earlier Firebase-era fallback that read a
   bare ``role`` is gone for exactly this reason: it would have resolved every
   signed-in student to an unknown role at best.

2. **Algorithm confusion (RS256/ES256 -> HS256).**
   Supabase publishes its public keys, and three algorithms are supported by the
   platform (``RS256``, ``ES256``, ``HS256``). A verifier that accepts whatever
   ``alg`` the token header names can be fooled with a token signed with the
   PUBLIC KEY as an HMAC secret. The accepted set is pinned to the two asymmetric
   algorithms, and the key is taken from the JWKS - never from the header.

3. **Skipping audience and issuer verification.**
   Every Supabase project signs its own tokens, so a signature check alone accepts
   a token from ANY project. Both ``aud`` (``authenticated``) and ``iss``
   (``<project>/auth/v1``) are verified, so a token minted elsewhere is rejected.

4. **Trusting a client-supplied user id.** The ``sub`` claim is the identity. A
   ``user_id`` in a path, query string or body is attacker-controlled and is never
   used for authorization.

5. **The anon key used as a bearer token.**
   The ``anon`` and ``service_role`` legacy keys are themselves JWTs and are
   routinely pasted into ``Authorization`` headers by mistake. They carry
   ``role: "anon"`` / ``role: "service_role"`` and no ``sub``, so requiring a
   signed-in role AND a subject refuses them - a check that costs nothing and
   closes a class of misconfiguration.

STORAGE AUTHORIZATION, IN ONE LINE: Supabase Storage RLS now CAN authorise a
Supabase user in principle, but this backend deliberately does not rely on it.
Files are reached through short-lived signed URLs issued by the API, so the
authorization boundary stays in one place instead of being split between Postgres
policies and Python.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """Roles from blueprint v3 §8.2."""

    STUDENT = "STUDENT"
    EDITOR = "EDITOR"
    CONTENT_MANAGER = "CONTENT_MANAGER"
    MODERATOR = "MODERATOR"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


#: Rank order for "at least this role" checks.
ROLE_RANK: dict[Role, int] = {
    Role.STUDENT: 0,
    Role.EDITOR: 1,
    Role.CONTENT_MANAGER: 2,
    Role.MODERATOR: 3,
    Role.ADMIN: 4,
    Role.SUPER_ADMIN: 5,
}

#: The metadata bag the role is read from. ``app_metadata`` is server-writable
#: only (Admin API) - see mistake 1 in the module docstring. ``user_metadata`` is
#: deliberately never consulted.
APP_METADATA_CLAIM = "app_metadata"
APP_ROLE_KEY = "role"

#: A namespaced top-level claim, for deployments that set custom claims through a
#: Supabase Custom Access Token Hook (a Postgres function that can add arbitrary
#: top-level claims). Accepted in addition to ``app_metadata.role``, and read as
#: a fallback because a hook is the only thing that can write it.
ROLE_CLAIM = "https://caprep.in/role"

#: The ONLY algorithms accepted. Both are asymmetric, so there is no shared
#: secret to confuse: a token signed HS256 with the public key as the HMAC secret
#: (the classic algorithm-confusion forgery) is rejected on the algorithm alone.
#:
#: This project's signing key is EC/ES256. RS256 is kept in the set so a project
#: rotated to an RSA key keeps working without a code change.
ALLOWED_ALGORITHMS = ("ES256", "RS256")

#: What a SIGNED-IN user's token looks like. ``anon`` and ``service_role`` tokens
#: - including the API keys themselves, which are JWTs - carry a different aud and
#: role, so requiring both refuses them. See mistake 5.
SUPABASE_AUDIENCE = "authenticated"
SUPABASE_SIGNED_IN_ROLE = "authenticated"

#: Access tokens live for one hour and the client SDK refreshes them. A small
#: leeway absorbs clock skew between Supabase, Render and the browser.
LEEWAY_SECONDS = 10

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    ``auth_user_id`` is the Supabase user id (a UUID), resolved from the verified
    ``sub`` claim. It is the only identity used for authorization.

    The column in PostgreSQL is named ``auth_user_id`` rather than
    ``firebase_uid``, and that naming decision has now paid for itself: this is
    the second identity provider the platform has used, and it cost no migration.
    """

    auth_user_id: str
    email: str | None
    role: Role
    claims: dict[str, Any]

    def has_role(self, role: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]

    @property
    def is_admin(self) -> bool:
        return self.has_role(Role.ADMIN)

    @property
    def sign_in_provider(self) -> str | None:
        """How the user authenticated: 'email', 'google', ...

        Read from ``app_metadata``, which is server-set - ``provider`` is the
        method used most recently and ``providers`` is the full list, so a student
        who signed up with email and later linked Google is identifiable.
        Useful for analytics and for challenging an admin action that arrived
        through an unfamiliar provider.
        """
        app_metadata = self.claims.get(APP_METADATA_CLAIM)
        if not isinstance(app_metadata, dict):
            return None
        provider = app_metadata.get("provider")
        if isinstance(provider, str):
            return provider
        providers = app_metadata.get("providers")
        if isinstance(providers, list) and providers:
            first = providers[0]
            return first if isinstance(first, str) else None
        return None

    @property
    def authenticated_at(self) -> int | None:
        """When the current SESSION began, as a Unix timestamp - ``iat``.

        BE HONEST ABOUT WHAT THIS IS. Supabase does not publish an ``auth_time``
        claim, so the closest available value is the token's ``iat``, and it is
        NOT equivalent: ``iat`` moves forward on every silent refresh. A session
        open for a day refreshes roughly hourly and therefore always looks
        "recent", which means ``require_recent_auth`` is a weaker control here
        than it was against Firebase.

        What still holds: a token cannot be older than its own ``exp``, so this
        bounds how stale the session can be, and it is the value sent by the
        server rather than anything the client chooses. If a genuinely
        sign-in-recent gate is needed for a destructive action (changing a
        password, exporting data), the reliable mechanism is Supabase's
        ``.reauthenticate()`` on the client plus confirming a fresh password
        challenge server-side - do not treat this property as that guarantee.
        """
        value = self.claims.get("iat")
        return int(value) if isinstance(value, (int, float)) else None


class AuthError(HTTPException):
    """401 with a body matching the documented error shape (§7.2)."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class SupabaseTokenVerifier:
    """Verifies Supabase Auth access tokens.

    Verification uses the project's published JWKS over the network.
    ``PyJWKClient`` caches the keys in-process, so this is not a network call per
    request - and because the keys are asymmetric, the server holds NO signing
    secret and NO service credential on the request path.

    WHY NOT ``supabase.auth.get_user(token)``: it is a fine API, but it makes a
    network call to the Auth server per request and ties verification to that
    service's availability. Verifying the JWT locally against the JWKS keeps the
    API stateless and unit-testable without network access, which is why the
    security tests run offline in CI.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jwk_client: Any = None

    def _project_url(self) -> str:
        return (self.settings.supabase_url or "").rstrip("/")

    def _get_jwk_client(self) -> Any:
        if self._jwk_client is None:
            import ssl

            import certifi
            from jwt import PyJWKClient

            # WHY AN EXPLICIT CERTIFI CONTEXT, AND WHY IT CHANGES NOTHING ABOUT
            # VERIFICATION:
            #
            # PyJWKClient fetches over urllib with the interpreter's default SSL
            # context. On a stock python.org macOS installation that context has
            # NO CA bundle until "Install Certificates.command" is run, so every
            # JWKS fetch failed with CERTIFICATE_VERIFY_FAILED and a perfectly
            # valid token was answered 401 "Invalid authentication token" - the
            # service looked like it rejected credentials when it simply could not
            # reach the keys.
            #
            # ``ssl.create_default_context`` is the STRICT default: certificate
            # verification required, hostname checking on, no way to disable
            # either. Pointing it at certifi's CA bundle makes the trust store
            # identical on macOS, Linux and the Render image. This must never
            # become CERT_NONE/check_hostname=False - a JWKS fetch that skips
            # verification would accept a forged key set.
            context = ssl.create_default_context(cafile=certifi.where())

            # cache_keys=True keeps the keys in memory. Supabase rotates signing
            # keys, and PyJWKClient refetches on an unknown kid, so rotation does
            # not need a restart.
            self._jwk_client = PyJWKClient(self.jwks_url, cache_keys=True, ssl_context=context)
        return self._jwk_client

    @property
    def jwks_url(self) -> str:
        return f"{self._project_url()}/auth/v1/.well-known/jwks.json"

    def _issuer(self) -> str:
        return f"{self._project_url()}/auth/v1"

    def verify(self, token: str) -> dict[str, Any]:
        if not token or not token.strip():
            raise AuthError("Missing bearer token")

        if not self._project_url():
            # Fail closed. An unconfigured project url must never fall through to
            # an unverified path.
            logger.error("SUPABASE_URL is not configured; refusing to verify tokens")
            raise AuthError("Authentication is not configured on this server")

        try:
            signing_key = self._get_jwk_client().get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                # Pinned. Never None, never taken from the token header.
                algorithms=list(ALLOWED_ALGORITHMS),
                # The key comes from the JWKS, so a token signed by another
                # Supabase project fails here too - but aud and iss are checked
                # anyway, because "the key matched" is not the same statement as
                # "this token is for this API".
                audience=SUPABASE_AUDIENCE,
                issuer=self._issuer(),
                leeway=LEEWAY_SECONDS,
                options={
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_exp": True,
                    "verify_signature": True,
                    "require": ["exp", "iat", "sub"],
                },
            )
        except AuthError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("Token audience is not valid for this API") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError("Token issuer is not recognised") from exc
        except jwt.InvalidAlgorithmError as exc:
            raise AuthError("Token algorithm is not accepted") from exc
        except jwt.PyJWTError as exc:
            # Deliberately vague to the client; the detail goes to the logs so a
            # probing attacker learns nothing about which check failed.
            logger.warning("Supabase token verification failed: %s", exc)
            raise AuthError("Invalid authentication token") from exc

        # Requires a SIGNED-IN token. The anon and service_role API keys are JWTs
        # too, and are pasted into Authorization headers often enough that
        # refusing them is worth one comparison.
        if claims.get("role") != SUPABASE_SIGNED_IN_ROLE:
            logger.warning("Rejected a token whose role claim is %r", claims.get("role"))
            raise AuthError("Token is not a signed-in user token")

        if not claims.get("sub"):
            raise AuthError("Token is missing a subject claim")

        return claims

    def extract_role(self, claims: dict[str, Any]) -> Role:
        """Resolve the application role from SERVER-SET claims only.

        Two sources, in order:

          1. ``app_metadata.role`` - writable only through the Admin API, so a
             student cannot set it.
          2. the namespaced ``https://caprep.in/role`` - present only if a Custom
             Access Token Hook put it there, which is also server-side.

        ``user_metadata`` is NEVER consulted: it is user-writable, and reading a
        role from it is the exact vulnerability this module documents. Neither is
        the bare ``role`` claim, which Supabase uses for ``"authenticated"``.

        An unrecognised or absent role resolves to STUDENT - the least privileged
        role - so a malformed claim can never grant access.
        """
        raw = None
        app_metadata = claims.get(APP_METADATA_CLAIM)
        if isinstance(app_metadata, dict):
            raw = app_metadata.get(APP_ROLE_KEY)
        if raw is None:
            raw = claims.get(ROLE_CLAIM)
        if raw is None:
            return Role.STUDENT

        try:
            return Role(raw)
        except ValueError:
            logger.warning("Unknown role claim %r; defaulting to STUDENT", raw)
            return Role.STUDENT


_verifier: SupabaseTokenVerifier | None = None


def get_verifier(settings: Settings | None = None) -> SupabaseTokenVerifier:
    global _verifier
    if _verifier is None:
        _verifier = SupabaseTokenVerifier(settings or get_settings())
    return _verifier


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Resolve the authenticated principal.

    IDENTITY RULE: the user id comes exclusively from the verified ``sub`` claim.
    Any ``user_id`` supplied in the path, query string, body or a custom header
    is ignored for authorization. Resolving the application user row is a
    repository concern (``app.repositories.users``); this dependency stops at the
    verified identity so it stays unit-testable without a database.
    """
    if credentials is None or not credentials.credentials:
        raise AuthError("Missing bearer token")

    verifier = get_verifier(settings)
    claims = verifier.verify(credentials.credentials)

    return Principal(
        auth_user_id=claims["sub"],
        email=claims.get("email"),
        role=verifier.extract_role(claims),
        claims=claims,
    )


def resolve_owned_user_id(principal: Principal, requested_user_id: str | None) -> str:
    """Guard against horizontal privilege escalation.

    A student asking for another student's data is rejected outright rather than
    silently scoped, so the attempt is visible in logs and tests. Admins may read
    across users.
    """
    if requested_user_id is None:
        return principal.auth_user_id
    if requested_user_id != principal.auth_user_id and not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another user's data",
        )
    return requested_user_id


def require_recent_auth(principal: Principal, max_age_seconds: int = 300) -> None:
    """Reject a request when the underlying sign-in is older than ``max_age``.

    Access tokens are refreshed silently by the client SDK, so "has a valid token"
    does not mean "signed in recently".

    READ THE CAVEAT on ``Principal.authenticated_at`` before relying on this: the
    value it compares is ``iat``, which moves forward on every refresh, so a
    long-running session always looks fresh. Treat this as a staleness bound, not
    as proof of a recent password entry.
    """
    import time

    authenticated_at = principal.authenticated_at
    if authenticated_at is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This action requires a recent sign-in. Please sign in again.",
        )
    if time.time() - authenticated_at > max_age_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This action requires a recent sign-in. Please sign in again.",
        )


# =============================================================================
# STORAGE AUTHORIZATION: ONE BOUNDARY, NOT TWO
# =============================================================================
# Supabase Storage row-level security policies evaluate ``auth.uid()`` from a
# Supabase Auth JWT. The platform now issues exactly those tokens, so RLS WOULD
# work - and is still deliberately not the mechanism for file access, because
# splitting authorization across Postgres policies and Python means two places to
# get it wrong and two places to audit.
#
# The pattern implemented in ``app/integrations/supabase_storage.py`` is
# BACKEND-BROKERED ACCESS:
#
#   1. Client authenticates with Supabase and calls the API with its access token.
#   2. The API verifies the token, applies the role check, and validates the
#      upload metadata (see ``validate_upload``).
#   3. The API asks Supabase for a short-lived SIGNED UPLOAD URL using the
#      secret key.
#   4. The client uploads the file directly to Supabase with that URL.
#   5. The client tells the API the resulting storage path, and the API enqueues
#      the ingestion job.
#
# This is strictly better than the browser-side RLS approach it replaces for one
# reason: large files never transit the API. Proxying a 50 MB PDF through Render
# would hit request-size and timeout limits on the platform, whereas a signed
# URL lets the upload go straight to storage while the API stays the single
# authorization boundary.
#
# Practical rule: buckets stay PRIVATE. No public bucket, no long-lived URL.
