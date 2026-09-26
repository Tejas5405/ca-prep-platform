"""Razorpay integration: Orders API and payment lookup.

Blueprint v3 §13.1, steps 2 and 6:

    "FastAPI creates Razorpay order"
    "FastAPI fetches/validates payment server-side"

Razorpay can do a great deal more than this - Plans, Subscriptions, Payment
Links, Route marketplace splits. This module wraps the two calls the blueprint's
flow actually needs, and the API surface is deliberately a payment gateway rather
than a subscription engine. The reason is failure containment: recurring
subscriptions on Razorpay would make the provider the source of truth for
entitlement (it owns the billing cycle, retries and dunning), and this codebase's
position is that the SUBSCRIPTION ROW in PostgreSQL is the source of truth
(billing.py, `entitlement_for`). One-off orders plus local expiry arithmetic keeps
that true. Move to Razorpay Subscriptions only if auto-renewal becomes a real
requirement, and record it as a stack amendment when it does.

=============================================================================
WHY THE SERVER FETCHES THE PAYMENT INSTEAD OF TRUSTING THE WEBHOOK BODY
=============================================================================
A signed webhook proves Razorpay sent the event. It does not prove the event's
contents are complete, and it does not help at all for the browser callback path.
So after any claim of payment - from a browser callback or a webhook - the amount
and status are read back from Razorpay's own API and compared against the order
THIS SERVER created. A mismatch is treated as fraud, not as a discrepancy to log
and continue past.

The API is also the only caller allowed to decide that a payment is captured. The
client can say anything; this cannot.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

#: Razorpay's API host. Overridable for tests, which point it at a local mock.
DEFAULT_BASE_URL = "https://api.razorpay.com/v1"

#: Orders are created and then paid from a browser. A slow gateway must not pin
#: an API worker, and a student on a 3G connection must not see a timeout.
DEFAULT_TIMEOUT_SECONDS = 15.0


class RazorpayError(RuntimeError):
    """Any failure talking to Razorpay: transport, auth or a rejected request."""


class RazorpayNotConfiguredError(RazorpayError):
    """Raised when the account keys are absent.

    A distinct type because the correct HTTP response is 503 (this deployment
    cannot take payments yet), not 500 (this request crashed). Collapsing the two
    would page an on-call engineer every time someone opened the pricing page on
    an environment without keys.
    """


@dataclass(frozen=True, slots=True)
class GatewayOrder:
    """The subset of a Razorpay order this service depends on."""

    id: str
    amount_paise: int
    currency: str
    receipt: str | None
    status: str
    #: ``{"key_id": "..."}``, sent to the browser so Checkout can open. The KEY
    #: ID is public by design; the key SECRET never leaves the server, and
    #: ``notes`` is omitted from this dataclass so a caller cannot accidentally
    #: serialise it into a response.
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GatewayPayment:
    """The subset of a Razorpay payment that decides entitlement.

    ``captured`` is what matters. Razorpay reports ``authorized`` while the money
    is only held, and treating authorized as paid grants access to funds that can
    still be reversed.
    """

    id: str
    order_id: str | None
    amount_paise: int
    currency: str
    status: str
    captured: bool
    method: str | None
    email: str | None
    raw: dict[str, Any]


class RazorpayClient:
    """Async Razorpay client, constructed from settings.

    Usage is a context manager so the HTTP connection is pooled across a request
    and always closed::

        async with RazorpayClient(settings) as gateway:
            order = await gateway.create_order(...)

    An ``httpx.AsyncClient`` may be injected for tests. As with the storage
    client, ownership is tracked so closing an injected client is never this
    class's decision.
    """

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._key_id = settings.razorpay_key_id
        self._key_secret = settings.razorpay_key_secret
        self._base_url = base_url.rstrip("/")
        self._http = http
        self._owns_http = http is None

    @property
    def key_id(self) -> str | None:
        """The publishable key id, for the checkout payload.

        Exposed as a property rather than a plain attribute so the secret has no
        equivalent accessor: there is no attribute named ``key_secret`` on this
        object, which makes an accidental JSON serialisation of the client
        impossible.
        """
        return self._key_id

    def is_configured(self) -> bool:
        return bool(self._key_id and self._key_secret)

    def _require_configured(self) -> tuple[str, str]:
        if not self.is_configured():
            raise RazorpayNotConfiguredError(
                "Razorpay keys are not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)"
            )
        assert self._key_id is not None and self._key_secret is not None
        return self._key_id, self._key_secret

    @staticmethod
    def _build_auth_header(key_id: str, key_secret: str) -> dict[str, str]:
        """HTTP Basic auth, as Razorpay's API requires.

        Built and returned as a whole dict so the secret exists in exactly one
        expression. Razorpay authenticates with the key pair over Basic auth -
        unlike Supabase's newer opaque keys, which use an ``apikey`` header and
        reject Basic. The two integrations look similar and behave differently,
        which is worth the comment: copying one header scheme into the other
        produces a 401 that reads like a wrong credential.
        """
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode("ascii")
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    async def __aenter__(self) -> RazorpayClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
            self._owns_http = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
            self._owns_http = True
        return self._http

    def _headers(self) -> dict[str, str]:
        return self._build_auth_header(*self._require_configured())

    # ------------------------------------------------------------- operations

    async def create_order(
        self,
        *,
        amount_paise: int,
        receipt: str,
        currency: str = "INR",
        notes: dict[str, str] | None = None,
    ) -> GatewayOrder:
        """Create an order for a server-decided amount.

        There is no ``amount`` parameter a caller could fill from a request body:
        the amount is passed in by the route from the plan catalogue, and this
        function refuses the degenerate values rather than forwarding them.
        """
        if amount_paise <= 0:
            raise RazorpayError(f"refusing to create an order for {amount_paise} paise")

        payload: dict[str, Any] = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            # Razorpay's default is to auto-capture on authorisation. Left at the
            # default and stated explicitly, because changing it later changes the
            # meaning of every `captured` check in this codebase.
            "payment_capture": 1,
        }
        if notes:
            payload["notes"] = notes

        data = await self._request("POST", "/orders", json=payload)
        order_id = data.get("id")
        if not isinstance(order_id, str) or not order_id:
            raise RazorpayError("Razorpay returned an order without an id")

        return GatewayOrder(
            id=order_id,
            amount_paise=int(data.get("amount", amount_paise)),
            currency=str(data.get("currency", currency)),
            receipt=data.get("receipt"),
            status=str(data.get("status", "created")),
            raw={"key_id": self._key_id},
        )

    async def fetch_payment(self, payment_id: str) -> GatewayPayment:
        """Read a payment back from Razorpay.

        THE VALIDATION STEP. Everything the browser or a webhook claims about a
        payment is checked against this. A caller may not skip it - there is no
        method here that returns a payment from a request payload, so the only way
        to obtain a ``GatewayPayment`` is to ask Razorpay.
        """
        if not payment_id:
            raise RazorpayError("fetch_payment requires a payment id")

        data = await self._request("GET", f"/payments/{payment_id}")
        status = str(data.get("status", ""))
        return GatewayPayment(
            id=str(data.get("id", payment_id)),
            order_id=data.get("order_id"),
            amount_paise=int(data.get("amount", 0)),
            currency=str(data.get("currency", "INR")),
            status=status,
            # Both conditions: Razorpay's status must say captured, and the
            # boolean must agree. A payload where they disagree is malformed, and
            # the safe reading of a malformed payment is "not paid".
            captured=status == "captured" and bool(data.get("captured", False)),
            method=data.get("method"),
            email=data.get("email"),
            raw=data,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        client = self._client()
        url = f"{self._base_url}{path}"
        try:
            response = await client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            raise RazorpayError(f"transport failure calling {path}: {exc}") from exc

        if response.status_code >= 400:
            # The response body is NOT included: Razorpay echoes request details
            # back on validation errors, and those can include the amount, the
            # notes and the receipt. Logged at debug for an operator to fetch
            # deliberately rather than at error where it lands in an aggregator.
            logger.debug("Razorpay %s %s -> %s", method, path, response.text[:500])
            raise RazorpayError(f"Razorpay returned {response.status_code} for {method} {path}")

        try:
            body = response.json()
        except ValueError as exc:
            raise RazorpayError(f"Razorpay returned non-JSON for {method} {path}") from exc
        if not isinstance(body, dict):
            raise RazorpayError(f"Razorpay returned {type(body).__name__} for {method} {path}")
        return body
