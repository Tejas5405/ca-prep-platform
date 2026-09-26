"""Supabase Auth JWT verification and authorization tests.

The highest-severity tests in the suite. Each corresponds to a mistake described
in ``app/core/security.py``. Tokens are signed with a locally generated EC key
pair - ES256, the algorithm this project's Supabase signing key uses - so the real
verification path is exercised with NO network access, which is why the suite runs
in CI.

THE ROLE CLAIM TESTS ARE THE ONES THAT MATTER MOST. The previous implementation
read the application role from ``user_metadata``, which the authenticated user can
write, so a student could promote themselves and the token would still verify.
``TestRoleClaimCannotBeSelfAssigned`` exists so that bug cannot return in any form.

THE BARE ``role`` CLAIM IS A NEW TRAP, and it is Supabase-specific: their tokens
carry a top-level ``role`` of ``"authenticated"`` (or ``"anon"``). Code that read a
bare ``role`` - which the Firebase-era implementation did as a fallback - would
find a value that is real, meaningful and NOT the application role.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app.core.config import Settings
from app.core.security import (
    ALLOWED_ALGORITHMS,
    APP_METADATA_CLAIM,
    ROLE_CLAIM,
    ROLE_RANK,
    SUPABASE_AUDIENCE,
    Principal,
    Role,
    SupabaseTokenVerifier,
    require_recent_auth,
    resolve_owned_user_id,
)

PROJECT_URL = "https://zyrmlnpvylhcpyaoizyz.supabase.co"
ISSUER = f"{PROJECT_URL}/auth/v1"
USER_ID = "8f14e45f-ceea-4b7a-9c1d-2f9a6c1b3d40"


@pytest.fixture(scope="module")
def keypair():
    """An EC P-256 key pair - the same key type the project signs with."""
    private = ec.generate_private_key(ec.SECP256R1())
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem


def make_token(
    private_key,
    *,
    sub: str = USER_ID,
    audience: str = SUPABASE_AUDIENCE,
    issuer: str = ISSUER,
    expires_in: int = 3600,
    token_role: str = "authenticated",
    app_role: str | None = "STUDENT",
    app_provider: str = "email",
    namespaced_role: str | None = None,
    user_metadata: dict | None = None,
    algorithm: str = "ES256",
    omit: tuple[str, ...] = (),
    extra: dict | None = None,
) -> str:
    """Build a Supabase-shaped ACCESS TOKEN (what the browser sends).

    Mirrors the real claim set so the tests exercise the actual shape: ``aud`` is
    ``authenticated``, the top-level ``role`` says how SIGNED IN the caller is,
    and the application role lives in the server-writable ``app_metadata``.
    """
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer,
        "sub": sub,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "email": "student@example.com",
        "phone": "",
        "role": token_role,
        "aal": "aal1",
        "amr": [{"method": "password", "timestamp": now}],
        "session_id": str(uuid.uuid4()),
        "is_anonymous": False,
        "app_metadata": {
            "provider": app_provider,
            "providers": [app_provider],
            **({"role": app_role} if app_role is not None else {}),
        },
        "user_metadata": user_metadata if user_metadata is not None else {"full_name": "Student"},
    }
    if namespaced_role is not None:
        claims[ROLE_CLAIM] = namespaced_role
    for key in omit:
        claims.pop(key, None)
    if extra:
        claims.update(extra)
    return jwt.encode(claims, private_key, algorithm=algorithm)


class StubVerifier(SupabaseTokenVerifier):
    """Verifier wired to a local public key instead of the project's JWKS.

    Only key RESOLUTION is replaced. Every check - algorithm pinning, audience,
    issuer, expiry, required claims, the signed-in role gate - runs as written in
    production code.
    """

    def __init__(self, settings: Settings, public_pem: bytes) -> None:
        super().__init__(settings)
        self._public_pem = public_pem

    def _get_jwk_client(self):  # type: ignore[override]
        class _Client:
            def __init__(self, pem: bytes) -> None:
                self._key = serialization.load_pem_public_key(pem)

            def get_signing_key_from_jwt(self, _token: str):
                class _Key:
                    def __init__(self, key) -> None:
                        self.key = key

                return _Key(self._key)

        return _Client(self._public_pem)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, supabase_url=PROJECT_URL)


@pytest.fixture
def verifier(settings, keypair) -> StubVerifier:
    _, public_pem = keypair
    return StubVerifier(settings, public_pem)


class TestAlgorithmPinning:
    def test_rejects_alg_none(self, verifier):
        unsigned = jwt.encode(
            {
                "sub": USER_ID,
                "aud": SUPABASE_AUDIENCE,
                "iss": ISSUER,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(HTTPException) as exc:
            verifier.verify(unsigned)
        assert exc.value.status_code == 401

    def test_only_asymmetric_algorithms_are_accepted(self):
        # Supabase can sign with RS256, ES256 or HS256. Accepting HS256 would
        # reintroduce the shared-secret confusion attack, so it is excluded and
        # this deployment never holds a signing secret at all.
        assert ALLOWED_ALGORITHMS == ("ES256", "RS256")
        assert "HS256" not in ALLOWED_ALGORITHMS
        assert "none" not in ALLOWED_ALGORITHMS

    def test_rejects_hs256_signed_with_the_public_key(self, verifier, keypair):
        """The classic algorithm-confusion attack.

        The verification key is PUBLIC, so an attacker who knows it can sign a
        token with it as an HMAC secret. If the verifier honoured the token
        header's ``alg``, that forgery would validate.
        """
        _, public_pem = keypair

        # Hand-rolled on purpose: PyJWT refuses to BUILD this token, but an
        # attacker is not using PyJWT - they are base64url-encoding a header, a
        # payload and an HMAC. Constructing it by hand is the only way to test
        # that OUR verifier rejects it.
        def b64(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        now = int(time.time())
        payload = b64(
            json.dumps(
                {
                    "sub": "attacker",
                    "aud": SUPABASE_AUDIENCE,
                    "iss": ISSUER,
                    "iat": now,
                    "exp": now + 3600,
                    "role": "authenticated",
                    APP_METADATA_CLAIM: {"role": "SUPER_ADMIN"},
                }
            ).encode()
        )
        signing_input = f"{header}.{payload}".encode()
        signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
        forged = f"{header}.{payload}.{signature}"

        with pytest.raises(HTTPException) as exc:
            verifier.verify(forged)
        assert exc.value.status_code == 401

    def test_rejects_a_token_signed_with_a_different_private_key(self, verifier):
        other = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(HTTPException):
            verifier.verify(make_token(other))


class TestApiKeysCannotAuthenticate:
    """Supabase's ``anon`` and ``service_role`` keys are JWTs, and get pasted.

    They are signed by the same project, so a signature check alone would accept
    them. They carry no ``sub`` and their top-level ``role`` is not
    ``authenticated``, and both facts are checked.
    """

    def test_the_anon_key_shape_is_rejected(self, verifier, keypair):
        private, _ = keypair
        anon_shaped = jwt.encode(
            {
                "iss": "supabase",
                "ref": "zyrmlnpvylhcpyaoizyz",
                "role": "anon",
                "iat": int(time.time()),
                "exp": int(time.time()) + 36_000,
            },
            private,
            algorithm="ES256",
        )
        with pytest.raises(HTTPException) as exc:
            verifier.verify(anon_shaped)
        assert exc.value.status_code == 401

    def test_a_service_role_shaped_token_is_rejected(self, verifier, keypair):
        private, _ = keypair
        service_shaped = make_token(private, token_role="service_role")
        with pytest.raises(HTTPException):
            verifier.verify(service_shaped)

    def test_an_anonymous_sign_in_is_rejected(self, verifier, keypair):
        # Supabase's anonymous sign-ins produce a real token with a real sub. The
        # platform does not offer anonymous accounts, so it must not accept them.
        private, _ = keypair
        with pytest.raises(HTTPException):
            verifier.verify(make_token(private, token_role="anon"))


class TestAudienceAndIssuer:
    def test_accepts_a_valid_token(self, verifier, keypair):
        private, _ = keypair
        claims = verifier.verify(make_token(private))
        assert claims["sub"] == USER_ID
        assert claims["email"] == "student@example.com"

    def test_rejects_a_token_for_another_audience(self, verifier, keypair):
        """A token from another Supabase project is also Supabase-signed.

        A signature check alone passes it, which is why audience and issuer must
        both be verified explicitly.
        """
        private, _ = keypair
        with pytest.raises(HTTPException) as exc:
            verifier.verify(make_token(private, audience="anon"))
        assert exc.value.status_code == 401

    def test_rejects_a_token_from_another_project(self, verifier, keypair):
        private, _ = keypair
        foreign = make_token(private, issuer="https://someone-elses-project.supabase.co/auth/v1")
        with pytest.raises(HTTPException) as exc:
            verifier.verify(foreign)
        assert exc.value.status_code == 401

    def test_the_expected_issuer_is_derived_from_the_project_url(self, verifier):
        assert verifier._issuer() == ISSUER
        assert verifier.jwks_url == f"{PROJECT_URL}/auth/v1/.well-known/jwks.json"

    def test_a_trailing_slash_in_the_project_url_is_harmless(self, keypair):
        # Copying the URL out of a dashboard often includes the slash, and the
        # issuer check would then fail with a confusing 401 rather than a config
        # error.
        _, public_pem = keypair
        with_slash = StubVerifier(
            Settings(_env_file=None, supabase_url=PROJECT_URL + "/"), public_pem
        )
        assert with_slash._issuer() == ISSUER


class TestExpiryAndRequiredClaims:
    def test_rejects_an_expired_token(self, verifier, keypair):
        private, _ = keypair
        with pytest.raises(HTTPException) as exc:
            verifier.verify(make_token(private, expires_in=-3600))
        assert exc.value.status_code == 401

    def test_tolerates_small_clock_skew(self, verifier, keypair):
        # Supabase, Render and the browser clock are never perfectly aligned; a
        # few seconds of leeway avoids spurious 401s, and 10s grants nothing.
        private, _ = keypair
        assert verifier.verify(make_token(private, expires_in=-5))

    def test_rejects_a_token_with_no_expiry(self, verifier, keypair):
        private, _ = keypair
        with pytest.raises(HTTPException):
            verifier.verify(make_token(private, omit=("exp",)))

    def test_rejects_a_token_with_no_issued_at(self, verifier, keypair):
        """``iat`` absence indicates a hand-crafted token."""
        private, _ = keypair
        with pytest.raises(HTTPException):
            verifier.verify(make_token(private, omit=("iat",)))

    def test_rejects_a_token_with_no_subject(self, verifier, keypair):
        private, _ = keypair
        with pytest.raises(HTTPException):
            verifier.verify(make_token(private, omit=("sub",)))

    def test_rejects_an_empty_subject(self, verifier, keypair):
        private, _ = keypair
        with pytest.raises(HTTPException):
            verifier.verify(make_token(private, sub=""))


class TestMalformedInput:
    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_rejects_empty_input(self, verifier, bad):
        with pytest.raises(HTTPException):
            verifier.verify(bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", ["not.a.jwt", "a.b", "...."])
    def test_rejects_garbage(self, verifier, bad):
        with pytest.raises(HTTPException):
            verifier.verify(bad)

    def test_rejects_a_truncated_token(self, verifier, keypair):
        private, _ = keypair
        with pytest.raises(HTTPException):
            verifier.verify(make_token(private)[:-10])

    def test_fails_closed_when_the_project_url_is_not_configured(self, keypair):
        """A misconfigured server must refuse requests, not skip verification."""
        _, public_pem = keypair
        unconfigured = StubVerifier(Settings(_env_file=None), public_pem)
        private = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(HTTPException) as exc:
            unconfigured.verify(make_token(private))
        assert exc.value.status_code == 401


class TestRoleClaimCannotBeSelfAssigned:
    """Regression suite for the privilege-escalation bug in the Supabase port.

    The previous implementation read the role from Supabase's ``user_metadata``,
    which the AUTHENTICATED USER can write. A student could call
    ``updateUser({data: {role: 'ADMIN'}})`` and receive a validly-signed token
    claiming ADMIN - so no amount of signature checking would have caught it.

    Supabase splits the two bags deliberately: ``app_metadata`` is server-only,
    ``user_metadata`` is user-writable. These tests pin which one carries
    authority.
    """

    def test_app_metadata_role_is_trusted(self, verifier):
        claims = {"sub": "u1", APP_METADATA_CLAIM: {"role": "ADMIN"}}
        assert verifier.extract_role(claims) is Role.ADMIN

    def test_user_metadata_role_carries_no_authority(self, verifier):
        """THE regression test. This is the escalation that was shipped once."""
        claims = {
            "sub": "u1",
            "user_metadata": {"role": "SUPER_ADMIN", "is_admin": True},
            APP_METADATA_CLAIM: {"role": "STUDENT"},
        }
        assert verifier.extract_role(claims) is Role.STUDENT

    def test_user_metadata_alone_grants_nothing(self, verifier):
        claims = {"sub": "u1", "user_metadata": {"role": "ADMIN"}}
        assert verifier.extract_role(claims) is Role.STUDENT

    def test_the_bare_role_claim_is_not_the_application_role(self, verifier):
        """Supabase's own ``role`` says ``authenticated``, never a platform role.

        Treating it as one would resolve every signed-in student to an unknown
        role, at best - and to ADMIN if a project ever used that word.
        """
        assert verifier.extract_role({"sub": "u1", "role": "authenticated"}) is Role.STUDENT
        assert verifier.extract_role({"sub": "u1", "role": "ADMIN"}) is Role.STUDENT

    def test_a_namespaced_hook_claim_is_accepted(self, verifier):
        # A Custom Access Token Hook can add top-level claims; that is server-side
        # SQL, so it is a trustworthy source.
        assert verifier.extract_role({"sub": "u1", ROLE_CLAIM: "MODERATOR"}) is Role.MODERATOR

    def test_app_metadata_wins_over_the_namespaced_claim(self, verifier):
        # Both are server-set, so the Admin API - the value an operator changes
        # most recently through a supported path - takes precedence.
        claims = {
            "sub": "u1",
            ROLE_CLAIM: "STUDENT",
            APP_METADATA_CLAIM: {"role": "CONTENT_MANAGER"},
        }
        assert verifier.extract_role(claims) is Role.CONTENT_MANAGER

    def test_absent_role_defaults_to_the_least_privileged_role(self, verifier):
        # Fail CLOSED. An empty claim must not be treated as "not denied".
        assert verifier.extract_role({"sub": "u1"}) is Role.STUDENT
        assert verifier.extract_role({"sub": "u1", APP_METADATA_CLAIM: {}}) is Role.STUDENT

    def test_unknown_role_defaults_to_student(self, verifier):
        for bogus in ("SUPERUSER", "admin", "root", "", 12345, ["ADMIN"], {"role": "ADMIN"}):
            assert verifier.extract_role({"sub": "u1", APP_METADATA_CLAIM: {"role": bogus}}) is (
                Role.STUDENT
            )

    def test_a_self_signed_admin_token_is_still_rejected(self, verifier):
        """Even a correct-looking claim is worthless without a valid signature."""
        attacker_key = ec.generate_private_key(ec.SECP256R1())
        forged = make_token(attacker_key, app_role="SUPER_ADMIN")
        with pytest.raises(HTTPException) as exc:
            verifier.verify(forged)
        assert exc.value.status_code == 401


class TestRoleHierarchy:
    def test_rank_orders_the_hierarchy(self):
        assert ROLE_RANK[Role.SUPER_ADMIN] > ROLE_RANK[Role.ADMIN]
        assert ROLE_RANK[Role.ADMIN] > ROLE_RANK[Role.MODERATOR]
        assert ROLE_RANK[Role.MODERATOR] > ROLE_RANK[Role.CONTENT_MANAGER]
        assert ROLE_RANK[Role.CONTENT_MANAGER] > ROLE_RANK[Role.EDITOR]
        assert ROLE_RANK[Role.EDITOR] > ROLE_RANK[Role.STUDENT]

    def test_student_has_the_lowest_rank(self):
        assert ROLE_RANK[Role.STUDENT] == 0


class TestPrincipal:
    def test_matches_and_exceeds_roles(self):
        p = Principal("uid-1", "a@b.c", Role.ADMIN, {})
        assert p.has_role(Role.STUDENT)
        assert p.has_role(Role.EDITOR)
        assert p.has_role(Role.ADMIN)
        assert not p.has_role(Role.SUPER_ADMIN)
        assert p.is_admin

    def test_student_is_not_admin(self):
        p = Principal("uid-1", None, Role.STUDENT, {})
        assert not p.is_admin
        assert not p.has_role(Role.MODERATOR)

    def test_reports_the_sign_in_provider(self):
        p = Principal("uid-1", None, Role.STUDENT, {APP_METADATA_CLAIM: {"provider": "google"}})
        assert p.sign_in_provider == "google"

    def test_falls_back_to_the_provider_list(self):
        # ``provider`` is the most recent method; ``providers`` is every method
        # ever linked, so a student who linked Google later is still identifiable.
        p = Principal(
            "uid-1", None, Role.STUDENT, {APP_METADATA_CLAIM: {"providers": ["email", "google"]}}
        )
        assert p.sign_in_provider == "email"

    def test_sign_in_provider_is_none_when_absent(self):
        assert Principal("uid-1", None, Role.STUDENT, {}).sign_in_provider is None
        assert (
            Principal("uid-1", None, Role.STUDENT, {APP_METADATA_CLAIM: "junk"}).sign_in_provider
            is None
        )
        assert (
            Principal(
                "uid-1", None, Role.STUDENT, {APP_METADATA_CLAIM: {"providers": []}}
            ).sign_in_provider
            is None
        )

    def test_exposes_the_session_start_from_iat(self):
        p = Principal("uid-1", None, Role.STUDENT, {"iat": 1_700_000_000})
        assert p.authenticated_at == 1_700_000_000

    def test_session_start_is_none_when_malformed(self):
        p = Principal("uid-1", None, Role.STUDENT, {"iat": "yesterday"})
        assert p.authenticated_at is None


class TestRecentAuthRequirement:
    """Access tokens are refreshed silently, so a valid token is not a fresh login.

    See the caveat on ``Principal.authenticated_at``: the value compared is
    ``iat``, which advances on every refresh, so this is a staleness bound rather
    than proof of a recent password entry.
    """

    def test_allows_a_fresh_token(self):
        now = int(datetime.now(UTC).timestamp())
        p = Principal("u", None, Role.ADMIN, {"iat": now - 30})
        require_recent_auth(p, max_age_seconds=300)  # no raise

    def test_rejects_a_stale_token(self):
        now = int(datetime.now(UTC).timestamp())
        p = Principal("u", None, Role.ADMIN, {"iat": now - 3600})
        with pytest.raises(HTTPException) as exc:
            require_recent_auth(p, max_age_seconds=300)
        assert exc.value.status_code == 401
        assert "recent sign-in" in str(exc.value.detail).lower()

    def test_rejects_when_the_claim_is_missing(self):
        p = Principal("u", None, Role.ADMIN, {})
        with pytest.raises(HTTPException) as exc:
            require_recent_auth(p)
        assert exc.value.status_code == 401

    def test_just_inside_the_window_is_allowed(self):
        now = int(datetime.now(UTC).timestamp())
        p = Principal("u", None, Role.ADMIN, {"iat": now - 299})
        require_recent_auth(p, max_age_seconds=300)  # no raise

    def test_exactly_at_the_boundary_is_expired(self):
        """A token 300.4s old is genuinely older than 300s.

        ``iat`` is an integer but ``time.time()`` is a float, so "exactly at the
        boundary" is reached a few hundred milliseconds late and IS over the
        limit. Asserting it passes would be a flaky test asserting wrong
        behaviour; the strict comparison is the honest one.
        """
        now = int(datetime.now(UTC).timestamp())
        p = Principal("u", None, Role.ADMIN, {"iat": now - 300})
        with pytest.raises(HTTPException):
            require_recent_auth(p, max_age_seconds=300)


class TestHorizontalPrivilegeEscalation:
    """A user id supplied by the caller must never widen access."""

    def test_returns_own_id_when_none_requested(self):
        p = Principal("uid-1", None, Role.STUDENT, {})
        assert resolve_owned_user_id(p, None) == "uid-1"

    def test_allows_a_user_to_request_their_own_id(self):
        p = Principal("uid-1", None, Role.STUDENT, {})
        assert resolve_owned_user_id(p, "uid-1") == "uid-1"

    def test_rejects_a_student_requesting_another_users_id(self):
        p = Principal("uid-1", None, Role.STUDENT, {})
        with pytest.raises(HTTPException) as exc:
            resolve_owned_user_id(p, "uid-2")
        assert exc.value.status_code == 403

    def test_allows_an_admin_to_read_across_users(self):
        p = Principal("uid-1", None, Role.ADMIN, {})
        assert resolve_owned_user_id(p, "uid-2") == "uid-2"
