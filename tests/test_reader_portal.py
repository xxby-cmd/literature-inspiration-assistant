import tempfile
import unittest
from test_connector import m
import reader_portal as p
import reader_gateway as g


class Client:
    def __init__(self):
        self.calls = []
        self.body = ""

    def document(self, path, body=None):
        self.calls.append(path)
        if path == "list":
            return {"list": []}
        if path == "create":
            return {
                "docCode": "Abcd1234",
                "shareUrl": "https://www.pushplus.plus/push/doc/Abcd1234",
            }
        if path == "saveContent":
            self.body = body["content"]
        if path.startswith("content?"):
            return {
                "published": True,
                "publishDirty": False,
                "sharePerm": 0,
                "shareLogin": 1,
                "content": self.body,
            }
        return {}


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = m.Store(self.tmp.name)
        self.store.put(
            "reader_site",
            {"enabled": True, "mode": "local", "url": "https://first.trycloudflare.com"},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixed_address_survives_reader_address_change_without_new_document(self):
        client = Client()
        fixed = p.sync_portal(self.store, client)
        first = client.body
        p.sync_portal(self.store, client)
        self.assertEqual(client.calls.count("saveContent"), 1)
        self.store.put(
            "reader_site",
            {"enabled": True, "mode": "local", "url": "https://second.trycloudflare.com"},
        )
        self.assertEqual(p.sync_portal(self.store, client), fixed)
        self.assertEqual(client.calls.count("create"), 1)
        self.assertNotEqual(first, client.body)
        self.assertIn("https://second.trycloudflare.com/enter/", client.body)
        self.assertEqual(g.entry_url(self.store), fixed)
        self.assertIn(
            "https://second.trycloudflare.com/enter/", g.entry_url(self.store, "/?read=" + "a" * 32)
        )

    def test_incomplete_publish_is_not_advertised_as_fixed_entry(self):
        client = Client()
        original = client.document

        def reject(path, body=None):
            if path.startswith("publish?"):
                raise RuntimeError("publishing rejected")
            return original(path, body)

        client.document = reject
        with self.assertRaises(RuntimeError):
            p.sync_portal(self.store, client)
        self.assertEqual(p.fixed_url(self.store), "")

    def test_unexpected_portal_host_is_rejected(self):
        self.store.put(
            "reader_portal", {"ready": True, "shareUrl": "https://other.example/push/doc/Abcd1234"}
        )
        self.assertEqual(p.fixed_url(self.store), "")


if __name__ == "__main__":
    unittest.main()
