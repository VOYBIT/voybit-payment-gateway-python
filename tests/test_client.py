import hashlib
import hmac
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from voybit_payment_gateway import Client, VoybitError, parse_event, verify_webhook


class GatewayTest(unittest.TestCase):
    def test_create_payment(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                seen["body"] = self.rfile.read(length)
                seen["key"] = self.headers.get("X-Voybit-Api-Key")
                seen["idempotency"] = self.headers.get("Idempotency-Key")
                payload = json.dumps({
                    "id": "pay_1",
                    "status": "pending",
                    "checkout_url": "https://voybit.com/pay/pub_1",
                }).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        created = Client("vb_test_example_secret", f"http://127.0.0.1:{port}/api/v1").create_payment(
            {"asset_id": "asset", "crypto_amount": "25.0000", "amount_minor": 2500, "fiat_currency": "USD"},
            "order:1001:attempt:1",
        )
        self.assertEqual(created["payment"]["checkout_url"], "https://voybit.com/pay/pub_1")
        self.assertEqual(seen["key"], "vb_test_example_secret")
        self.assertEqual(seen["idempotency"], "order:1001:attempt:1")
        self.assertEqual(json.loads(seen["body"])["amount_minor"], 2500)

    def test_validation_is_not_retried(self):
        calls = {"n": 0}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls["n"] += 1
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                payload = json.dumps({"error": {"code": "asset_unavailable", "message": "That asset is not enabled for this gateway."}}).encode()
                self.send_response(422)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        with self.assertRaises(VoybitError) as raised:
            Client("vb_test_example_secret", f"http://127.0.0.1:{port}/api/v1").create_payment(
                {"asset_id": "asset"},
                "order:1001:attempt:1",
            )
        self.assertEqual(raised.exception.code, "asset_unavailable")
        self.assertEqual(calls["n"], 1)

    def test_webhook(self):
        raw = b'{"type":"payment.paid","status":"paid"}'
        now = 1_700_000_000
        timestamp = str(now)
        signature = "v1=" + hmac.new(b"whsec_example", f"delivery-1.{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
        verify_webhook("whsec_example", "delivery-1", timestamp, signature, raw, now)
        self.assertEqual(parse_event(raw)["status"], "paid")
        with self.assertRaises(ValueError):
            verify_webhook("whsec_example", "delivery-1", timestamp, signature, raw + b" ", now)


if __name__ == "__main__":
    unittest.main()
