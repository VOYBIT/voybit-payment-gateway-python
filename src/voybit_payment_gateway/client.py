"""Create a payment gateway invoice."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.voybit.com/api/v1"
RETRYABLE = {408, 429, 500, 502, 503, 504}
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
USER_AGENT = "voybit-payment-gateway-python/0.1.0"


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
        self.endpoint = base_url.rstrip("/") + "/gateway/payments"
        self._opener = urllib.request.build_opener(_NoRedirect)

    def create_payment(self, request: dict, idempotency_key: str) -> dict:
        if not IDEMPOTENCY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key must be 8 to 128 URL-safe characters")
        body = json.dumps(request).encode()
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                return self._post(body, idempotency_key)
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

    def _post(self, body: bytes, idempotency_key: str) -> dict:
        http_request = urllib.request.Request(
            self.endpoint,
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
                return _success(response)
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


def _success(response) -> dict:
    payment = _object(response.read())
    return {
        "payment": payment,
        "replayed": response.headers.get("Idempotency-Replayed") == "true",
        "request_id": response.headers.get("X-Request-ID", ""),
    }


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
