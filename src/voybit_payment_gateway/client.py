"""Create a payment gateway invoice."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, TypedDict

DEFAULT_BASE_URL = "https://api.voybit.com/api/v1"
RETRYABLE = {408, 429, 500, 502, 503, 504}
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
POSITIVE_DECIMAL = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
FIAT_CURRENCY = re.compile(r"^[A-Z]{3}$")
USER_AGENT = "voybit-payment-gateway-python/0.2.0"


class _RequiredCheckoutSessionRequest(TypedDict):
    fiat_amount: str
    fiat_currency: str


class CheckoutSessionRequest(_RequiredCheckoutSessionRequest, total=False):
    description: str
    metadata: dict[str, Any]
    payment_window_seconds: int


class _RequiredCheckoutSession(TypedDict):
    checkout_url: str


class CheckoutSession(_RequiredCheckoutSession, total=False):
    id: str
    session_id: str
    public_id: str
    status: str
    fiat_amount: str
    fiat_currency: str
    description: str
    metadata: dict[str, Any]
    payment_window_seconds: int
    expires_at: str
    created_at: str


class CreatedCheckoutSession(TypedDict):
    checkout_session: CheckoutSession
    replayed: bool
    request_id: str


class VoybitError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: str = ""):
        super().__init__(message or f"payment gateway returned HTTP {status}")
        self.status = status
        self.code = code or "unknown_error"
        self.request_id = request_id


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        if not api_key:
            raise ValueError("API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(_NoRedirect)

    def create_payment(self, request: dict, idempotency_key: str) -> dict:
        return self._create(
            self.base_url + "/gateway/payments",
            request,
            idempotency_key,
            "payment",
        )

    def create_checkout_session(
        self,
        request: CheckoutSessionRequest | Mapping[str, Any],
        idempotency_key: str,
    ) -> CreatedCheckoutSession:
        return self._create(
            self.base_url + "/gateway/checkout-sessions",
            _checkout_session_body(request),
            idempotency_key,
            "checkout_session",
        )

    def _create(
        self,
        endpoint: str,
        request: Mapping[str, Any],
        idempotency_key: str,
        result_key: str,
    ) -> dict:
        if not IDEMPOTENCY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key must be 8 to 128 URL-safe characters")
        body = json.dumps(request).encode()
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                return self._post(endpoint, body, idempotency_key, result_key)
            except VoybitError as error:
                if error.status not in RETRYABLE or attempt == 3:
                    raise
                last_error = error
                time.sleep(_delay(attempt, getattr(error, "retry_after", 0)))
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 3:
                    raise
                last_error = error
                time.sleep(_delay(attempt))
        raise last_error or RuntimeError("payment gateway request failed")

    def _post(self, endpoint: str, body: bytes, idempotency_key: str, result_key: str) -> dict:
        http_request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "X-Voybit-Api-Key": self.api_key,
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with self._opener.open(http_request, timeout=20) as response:
                return _success(response, result_key)
        except urllib.error.HTTPError as error:
            try:
                decoded = _object(error.read())
            except (json.JSONDecodeError, ValueError):
                decoded = {}
            envelope = decoded.get("error") if isinstance(decoded.get("error"), dict) else {}
            api_error = VoybitError(
                error.code,
                str(envelope.get("code") or "unknown_error"),
                str(envelope.get("message") or ""),
                error.headers.get("X-Request-ID", ""),
            )
            retry_after = error.headers.get("Retry-After")
            if error.code in RETRYABLE and retry_after:
                api_error.retry_after = _seconds(retry_after)
            raise api_error from None


def _success(response, result_key: str) -> dict:
    resource = _object(response.read())
    return {
        result_key: resource,
        "replayed": response.headers.get("Idempotency-Replayed") == "true",
        "request_id": response.headers.get("X-Request-ID", ""),
    }


def _checkout_session_body(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ValueError("checkout session request is required")
    amount = request.get("fiat_amount")
    if not isinstance(amount, str) or not POSITIVE_DECIMAL.fullmatch(amount) or not any(
        character != "0" and character != "." for character in amount
    ):
        raise ValueError("fiat_amount must be a positive decimal string")
    currency = request.get("fiat_currency")
    if not isinstance(currency, str) or not FIAT_CURRENCY.fullmatch(currency):
        raise ValueError("fiat_currency must be a three-letter uppercase currency code")
    window = request.get("payment_window_seconds")
    if window is not None and (isinstance(window, bool) or not isinstance(window, int) or window <= 0):
        raise ValueError("payment_window_seconds must be a positive integer")
    description = request.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("description must be a string")
    metadata = request.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an object")

    body: dict[str, Any] = {
        "fiat_amount": amount,
        "fiat_currency": currency,
    }
    if description is not None:
        body["description"] = description
    if metadata is not None:
        body["metadata"] = dict(metadata)
    if window is not None:
        body["payment_window_seconds"] = window
    return body


def _object(raw: bytes) -> dict:
    if not raw:
        return {}
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("response was not a JSON object")
    return decoded


def _delay(attempt: int, retry_after: float = 0) -> float:
    if retry_after > 0:
        return min(retry_after, 30)
    return min(0.5 * (2**attempt), 8)


def _seconds(value: str) -> float:
    try:
        return float(value.strip())
    except (AttributeError, ValueError):
        return 0
