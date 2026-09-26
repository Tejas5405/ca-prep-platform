#!/usr/bin/env python3
"""A local stand-in for Supabase Auth, for development and end-to-end tests.

WHY THIS EXISTS

The API verifies access tokens against `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`.
Pointing `SUPABASE_URL` at this script therefore exercises the REAL verification
path -- signature, issuer, audience, expiry, role claim -- with no dependency
override and no stub verifier. That is the difference between testing the auth code
and testing a mock of it, and it is how the Supabase swap was verified end to end
before the live project had a single user.

It also makes local development independent of the cloud project: no account, no
confirmation email, no dashboard, and no need to paste a real token into a shell.

WHAT IT IS NOT. It is not a Supabase emulator. It serves one JWKS document and mints
tokens that the API will accept. There is no sign-in, no password, no database.
Never expose it, and never point a deployed environment at it -- the API would then
accept tokens from anyone who can reach this port.

USAGE

    python3 scripts/local_auth.py serve --port 54321          # serve the JWKS
    python3 scripts/local_auth.py token --role ADMIN          # print an access token
    python3 scripts/local_auth.py token --sub <uuid> --email a@b.in

Then run the API with:

    SUPABASE_URL=http://127.0.0.1:54321 \
    DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5433/caprep \
    uvicorn app.main:app --port 8000

The key pair is kept in a machine-local file under the temp directory, created with
0600 and never committed. It has to be shared between the two invocations -- `serve`
signs nothing and `token` serves nothing, and if each generated its own key the API
would reject every token with "Signature verification failed", which looks exactly
like a broken verifier rather than two mismatched key pairs. Nothing in this
repository can forge a token, because the key never enters it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import encode

# Machine-local, 0600, outside the repository. See the module docstring for why it
# is on disk at all.
KEY_PATH = pathlib.Path(tempfile.gettempdir()) / "caprep-local-auth" / "private.pem"
KID = "local-dev"


def _load_or_create_key():
    """The same key pair for every invocation, so `serve` and `token` agree."""
    if KEY_PATH.is_file():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    KEY_PATH.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(KEY_PATH, 0o600)
    return key


_KEY = _load_or_create_key()
AUDIENCE = "authenticated"

# The project URL the tokens are minted for. Tokens carry it as `iss`, and since the
# child process is told the same value the two agree by construction.
PROJECT_URL = "http://127.0.0.1:54321"
ISSUER = f"{PROJECT_URL}/auth/v1"


def _b64url(number: int, size: int) -> str:
    return base64.urlsafe_b64encode(number.to_bytes(size, "big")).rstrip(b"=").decode()


def jwks_document() -> dict:
    numbers = _KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "alg": "ES256",
                "use": "sig",
                "kid": KID,
                "x": _b64url(numbers.x, 32),
                "y": _b64url(numbers.y, 32),
            }
        ]
    }


def private_pem() -> bytes:
    return _KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def mint(
    sub: str | None = None,
    email: str = "student@example.com",
    role: str = "STUDENT",
    *,
    lifetime_seconds: int = 3600,
    project_url: str = PROJECT_URL,
    signed_in: bool = True,
    extra: dict | None = None,
) -> str:
    """An access token shaped exactly like the ones Supabase issues.

    The top-level `role` is `authenticated`, NOT the application role: that is the
    trap SA-09 records. The application role lives in `app_metadata`, which only the
    backend can write -- the same shape the live project produces.
    """
    now = int(time.time())
    claims = {
        "iss": f"{project_url}/auth/v1",
        "sub": sub or str(uuid.uuid4()),
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + lifetime_seconds,
        "email": email,
        "phone": "",
        "role": "authenticated" if signed_in else "anon",
        "aal": "aal1",
        "amr": [{"method": "password", "timestamp": now}],
        "session_id": str(uuid.uuid4()),
        "is_anonymous": False,
        "app_metadata": {"provider": "email", "providers": ["email"], "role": role},
        "user_metadata": {"email_verified": True},
    }
    if extra:
        claims.update(extra)
    return encode(claims, private_pem(), algorithm="ES256", headers={"kid": KID})


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        # Supabase serves the JWKS at this exact path; the verifier builds the URL
        # from SUPABASE_URL, so serving anything else would not be exercised.
        if self.path.rstrip("/") == "/auth/v1/.well-known/jwks.json":
            body = json.dumps(jwks_document()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Anything else is a 404 with a body, so a misconfigured path fails loudly
        # rather than with an empty 200 that PyJWKClient reports as "no key".
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"message": "not found", "path": self.path}).encode())

    def log_message(self, fmt, *args):
        sys.stderr.write("[local-auth] " + (fmt % args) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="serve the JWKS document")
    serve.add_argument("--port", type=int, default=54321)
    serve.add_argument("--host", default="127.0.0.1")

    token = sub.add_parser("token", help="print an access token that the API accepts")
    token.add_argument("--sub")
    token.add_argument("--email", default="student@example.com")
    token.add_argument("--role", default="STUDENT")
    token.add_argument("--project-url", default=PROJECT_URL)
    token.add_argument("--lifetime", type=int, default=3600)
    token.add_argument(
        "--anon",
        action="store_true",
        help="mint an API-key-shaped token, which the API must REJECT",
    )

    args = parser.parse_args()

    if args.command == "serve":
        print(f"[local-auth] key: {KEY_PATH}")
        print(f"[local-auth] JWKS on http://{args.host}:{args.port}/auth/v1/.well-known/jwks.json")
        print(f"[local-auth] run the API with SUPABASE_URL=http://{args.host}:{args.port}")
        HTTPServer((args.host, args.port), Handler).serve_forever()
        return 0

    print(
        mint(
            sub=args.sub,
            email=args.email,
            role=args.role,
            project_url=args.project_url,
            lifetime_seconds=args.lifetime,
            signed_in=not args.anon,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
