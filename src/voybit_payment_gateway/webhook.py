"""Verify a Voybit payment gateway webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import time


TOLERANCE_SECONDS = 5 * 60


def verify_webhook(secret: str, delivery_id: str, timestamp: str, signature: str, raw_body: bytes, now: float | None = None) -> None:
    if not secret or not delivery_id or not timestamp or not signature.startswith("v1="):
        raise ValueError("webhook signature is invalid")
    try:
        supplied = bytes.fromhex(signature[3:])
        seconds = int(timestamp)
    except ValueError as error:
        raise ValueError("webhook signature is invalid") from error
    current = time.time() if now is None else now
    if len(supplied) != 32 or abs(int(current) - seconds) > TOLERANCE_SECONDS:
        raise ValueError("webhook signature is invalid")
    signed = delivery_id.encode() + b"." + timestamp.encode() + b"." + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("webhook signature does not match")


def parse_event(raw_body: bytes) -> dict:
    event = json.loads(raw_body)
    if not isinstance(event, dict):
        raise ValueError("webhook body must be an object")
    return event
