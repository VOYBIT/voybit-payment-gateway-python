"""Verify a payment gateway webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time


TOLERANCE_SECONDS = 5 * 60
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def verify_webhook(secret: str, delivery_id: str, timestamp: str, signature: str, raw_body: bytes, now: float | None = None) -> None:
    hex_signature = signature[3:] if signature.startswith("v1=") else ""
    if not secret or not delivery_id or not timestamp.isdigit() or not _HEX.fullmatch(hex_signature):
        raise ValueError("webhook signature is invalid")
    seconds = int(timestamp)
    current = time.time() if now is None else now
    if abs(int(current) - seconds) > TOLERANCE_SECONDS:
        raise ValueError("webhook timestamp is outside the 5 minute window")
    signed = delivery_id.encode() + b"." + timestamp.encode() + b"." + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, bytes.fromhex(hex_signature)):
        raise ValueError("webhook signature does not match")


def parse_event(raw_body: bytes) -> dict:
    event = json.loads(raw_body)
    if not isinstance(event, dict):
        raise ValueError("webhook body must be an object")
    return event
