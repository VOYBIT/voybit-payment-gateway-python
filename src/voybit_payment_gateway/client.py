"""Create a Voybit payment gateway invoice."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.voybit.com/api/v1"
RETRYABLE = {408, 429, 500, 502, 503, 504}
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class VoybitError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: str = ""):
        super().__init__(message or f"Voybit payment gateway returned HTTP {status}")
        self.status = status
        self.code = code or "unknown_error"
        self.request_id = request_id


class Client:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        if not api_key:
            raise ValueError("Voybit payment gateway API key is required")
        self.api_key = api_key
        self.endpoint = base_url.rstrip("/") + "/gateway/payments"

    def create_payment(self, request: dict, idempotency_key: str) -> dict:
        if not IDEMPOTENCY.fullmatch(idempotency_key):
            raise ValueError("idempotency key must contain 8 to 128 URL-safe characters")
        body = json.dumps(request).encode()
        last_error: Exception | None = None
        for attempt in range(4):
            http_request = urllib.request.Request(
                self.endpoint,
                data=body,
                method="POST",
                headers={
                    "X-Voybit-Api-Key": self.api_key,
                    "Idempotency-Key": idempotency_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(http_request, timeout=20) as response:
                    return _success(response)
            except urllib.error.HTTPError as error:
                raw = error.read()
                decoded = json.loads(raw) if raw else {}
                envelope = decoded.get("error") or {}
                api_error = VoybitError(
                    error.code,
                    envelope.get("code", "unknown_error"),
                    envelope.get("message", ""),
                    error.headers.get("X-Request-ID", ""),
                )
                if error.code not in RETRYABLE or attempt == 3:
                    raise api_error from None
                last_error = api_error
                time.sleep(_delay(error.headers.get("Retry-After"), attempt))
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 3:
                    raise
                last_error = error
                time.sleep(_delay("", attempt))
        raise last_error or RuntimeError("Voybit payment gateway request failed")


def _success(response) -> dict:
    raw = response.read()
    payment = json.loads(raw) if raw else {}
    return {
        "payment": payment,
        "replayed": response.headers.get("Idempotency-Replayed") == "true",
        "request_id": response.headers.get("X-Request-ID", ""),
    }


def _delay(retry_after: str | None, attempt: int) -> float:
    try:
        seconds = int(str(retry_after).strip())
    except (TypeError, ValueError):
        seconds = 0
    if seconds > 0:
        return float(seconds)
    return min(0.5 * (2**attempt), 8)
