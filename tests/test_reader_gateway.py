import http.server
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import http.cookiejar
import http.client
import gzip
from test_connector import m
import reader_gateway as g


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = m.Store(self.tmp.name)

        class Backend(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/javascript"
                    if self.path.startswith("/assets/")
                    else "application/json",
                )
                self.end_headers()
                self.wfile.write(
                    b"x" * 2000 if self.path.startswith("/assets/") else b'{"ok":true}'
                )

            def do_POST(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        self.backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        self.gateway = g.start_gateway(
            self.store, 0, "http://127.0.0.1:" + str(self.backend.server_port)
        )
        self.base = "http://127.0.0.1:" + str(self.gateway.server_port)
        self.client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.backend.shutdown()
        self.backend.server_close()
        self.tmp.cleanup()

    def enter(self):
        self.client.open(self.base + "/enter/" + g.secret(self.store)).read()

    def test_personal_entry_required_and_sync_endpoint_never_exposed(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.client.open(self.base + "/api/today")
        self.assertEqual(e.exception.code, 401)
        self.enter()
        self.assertTrue(json.load(self.client.open(self.base + "/api/today"))["ok"])
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.client.open(self.base + "/api/sync/claim")
        self.assertEqual(e.exception.code, 403)

    def test_other_site_cannot_submit_even_with_session_cookie(self):
        self.enter()
        request = urllib.request.Request(
            self.base + "/api/action",
            b"{}",
            {"Content-Type": "application/json", "Origin": "https://other.example"},
        )
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.client.open(request)
        self.assertEqual(e.exception.code, 403)
        good = urllib.request.Request(
            self.base + "/api/action",
            b"{}",
            {"Content-Type": "application/json", "Origin": self.base},
        )
        self.assertTrue(json.load(self.client.open(good))["ok"])

    def test_no_open_redirect_and_gate_secret_is_encrypted(self):
        key = g.secret(self.store)
        self.assertNotIn(key.encode(), (self.store.home / "reader_gate.dpapi").read_bytes())
        self.assertEqual(g.safe_target("https://other.example/"), "/")
        self.assertEqual(g.safe_target("/?read=" + "a" * 32), "/?read=" + "a" * 32)
        self.assertFalse(g.permitted("/.env", "GET"))
        self.assertFalse(g.permitted("/api/sync/result", "POST"))
        self.store.put(
            "reader_site", {"mode": "local", "enabled": True, "url": "http://127.0.0.1:18743"}
        )
        self.assertEqual(g.entry_url(self.store), "")
        self.assertTrue(g.local_entry_url(self.store).startswith("http://127.0.0.1:18743/enter/"))

    def test_provisioning_connector_refuses_other_hosts_and_expired_dns(self):
        proxy = g.start_api_proxy(self.store, 0)
        try:
            client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
            client.request("CONNECT", "other.example:443")
            self.assertEqual(client.getresponse().status, 403)
            client.close()
            client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=3)
            client.request("POST", "/tunnel", body=b"")
            self.assertEqual(client.getresponse().status, 503)
            client.close()
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_only_fingerprinted_static_files_are_cached_and_compressed(self):
        self.enter()
        request = urllib.request.Request(
            self.base + "/assets/example-abcdefgh.js", headers={"Accept-Encoding": "gzip"}
        )
        with self.client.open(request) as response:
            self.assertIn("private", response.headers["Cache-Control"])
            self.assertIn("immutable", response.headers["Cache-Control"])
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            self.assertEqual(gzip.decompress(response.read()), b"x" * 2000)
        with self.client.open(self.base + "/api/today") as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
