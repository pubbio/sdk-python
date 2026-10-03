import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pubb import PubbAPIError, PubbProtocolError, PubbServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.received.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(self.server.status)
        for key, value in self.server.response_headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, *args):
        pass


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.received = []
        self.http.status = 202
        self.http.response_headers = {}
        self.http.body = json.dumps(dict(success=True, accepted=True, channels=["updates"], publicationId="pub-1", duplicate=False)).encode()
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.pubb = PubbServer("app/id", "public-key", "test-secret", api_host=f"http://127.0.0.1:{self.http.server_port}")

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def test_publish_contract(self):
        result = self.pubb.trigger(["updates", "room"], "message", {"text": "Merhaba 🌍"}, socket_id="1.2", idempotency_key="event-42")
        path, headers, body = self.http.received[0]
        self.assertEqual(path, "/api/apps/app%2Fid/events")
        self.assertEqual(headers["Authorization"], "Bearer test-secret")
        self.assertEqual(headers["Idempotency-Key"], "event-42")
        self.assertEqual(json.loads(body), dict(channel=["updates", "room"], name="message", data={"text": "Merhaba 🌍"}, socket_id="1.2"))
        self.assertEqual(result.publication_id, "pub-1")
        self.assertFalse(result.duplicate)

    def test_single_channel_and_duplicate(self):
        self.http.body = self.http.body.replace(b'"duplicate": false', b'"duplicate": true')
        self.assertTrue(self.pubb.trigger("updates", "message", None).duplicate)
        self.assertNotIn("Idempotency-Key", self.http.received[0][1])
        self.assertNotIn("socket_id", json.loads(self.http.received[0][2]))

    def test_api_errors_do_not_echo_response(self):
        self.http.status = 429
        self.http.response_headers = {"Retry-After": "5"}
        self.http.body = b"sensitive response"
        with self.assertRaises(PubbAPIError) as caught:
            self.pubb.trigger("updates", "message", {})
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, "5")
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual(len(self.http.received), 1)

    def test_redirect_is_not_followed(self):
        self.http.status = 307
        self.http.response_headers = {"Location": f"http://127.0.0.1:{self.http.server_port}/other"}
        with self.assertRaises(PubbAPIError) as caught:
            self.pubb.trigger("updates", "message", {})
        self.assertEqual(caught.exception.status, 307)
        self.assertEqual(len(self.http.received), 1)

    def test_only_202_is_accepted(self):
        self.http.status = 200
        with self.assertRaises(PubbAPIError):
            self.pubb.trigger("updates", "message", {})

    def test_invalid_response(self):
        for body in [b"not json", b"{}", b"[]", b' {"success": false}', b'"secret"']:
            with self.subTest(body=body):
                self.http.body = body
                with self.assertRaises(PubbProtocolError):
                    self.pubb.trigger("updates", "message", {})

    def test_validation_before_network(self):
        for channel, name, data, extra in [([], "event", {}, {}), (["a"]*101, "event", {}, {}),
                ("", "event", {}, {}), ("room", "", {}, {}), ("room", "event", "é"*5120, {}),
                ("room", "event", float("nan"), {}), ("room", "event", {}, {"socket_id": ""}),
                ("room", "event", {}, {"idempotency_key": "bad key"})]:
            with self.subTest(channel=channel, name=name, extra=extra):
                with self.assertRaises(ValueError):
                    self.pubb.trigger(channel, name, data, **extra)
        self.assertEqual(self.http.received, [])

    def test_private_signing_vector(self):
        self.assertEqual(self.pubb.authorize_channel("123.456", "private-room"),
                         {"auth": "public-key:f3312a32998c7c174fb5d44146dad485d980398fe616181bf1f758764ccf603a"})

    def test_presence_signing_vector(self):
        self.assertEqual(self.pubb.authorize_presence_channel("123.456", "presence-room", "user-1", {"name": "Ada"}),
                         {"auth": "public-key:41d9f92ca8a5496a5f346458be7d75c0d79bce9928260f8d139330140140d939", "channel_data": '{"user_id":"user-1","user_info":{"name":"Ada"}}'})

    def test_auth_validation(self):
        with self.assertRaises(ValueError):
            self.pubb.authorize_channel("1.2", "presence-room")
        with self.assertRaises(ValueError):
            self.pubb.authorize_presence_channel("1.2", "private-room", "user")
        with self.assertRaises(ValueError):
            self.pubb.authorize_presence_channel("", "presence-room", "user")

    def test_configuration_validation(self):
        for kwargs in [{"api_host": "ftp://example.com"}, {"api_host": "https://user:pass@example.com"},
                       {"api_host": "https://example.com?q=1"}, {"timeout": 0}, {"timeout": float("inf")}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PubbServer("id", "key", "secret", **kwargs)
        self.assertNotIn("test-secret", repr(self.pubb))


if __name__ == "__main__":
    unittest.main()
