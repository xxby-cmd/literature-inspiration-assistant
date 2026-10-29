"""Exercise real HTTP, durable queue, date binding and existing reply routing."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

from test_connector import m, digest

spec = importlib.util.spec_from_file_location(
    "button_panel", Path(m.__file__).with_name("button_panel.py")
)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = m.Store(self.tmp.name)
        profile = self.store.profile()
        profile["wechat"]["enabled"] = True
        m.atomic(self.store.profile_path, json.dumps(profile).encode())
        self.data = digest()
        self.store.put("digest:" + self.data["date"], self.data)
        self.server = panel.start_panel(self.store, 0)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def action(self, action="like", **kwargs):
        return {
            "action": action,
            "request_id": uuid.uuid4().hex,
            "date": self.data["date"],
            "paper": 1,
            **kwargs,
        }

    def post(self, body, origin=None):
        req = urllib.request.Request(
            self.url + "/action",
            json.dumps(body).encode(),
            {
                "Origin": origin or self.url,
                "Content-Type": "application/json",
                "X-Panel-Request": "1",
            },
        )
        with urllib.request.urlopen(req) as response:
            return json.load(response)

    def test_get_is_display_only_and_escapes_paper_text(self):
        self.data["cards"][0]["title_cn"] = "<script>alert(1)</script>"
        self.store.put("digest:" + self.data["date"], self.data)
        with urllib.request.urlopen(self.url) as response:
            page = response.read().decode()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 0)

    def test_selection_round_trip_uses_existing_answer_and_wechat(self):
        item = self.action()
        response = self.post(item)
        self.post(item)  # network retry must not duplicate feedback or messages
        with (
            patch.object(m, "run_codex") as model,
            patch.object(m.PushPlus, "send", return_value={"status": "sent"}) as send,
        ):
            self.assertTrue(m.process_one(self.store))
            self.assertFalse(m.process_one(self.store))
            model.assert_not_called()
            self.assertEqual(send.call_args.args[-1], "wechat")
        with urllib.request.urlopen(self.url + "/result/" + response["id"]) as reply:
            result = json.load(reply)
        self.assertEqual(result["status"], "sent")
        self.assertIn("已记录：喜欢", result["answer"])
        self.assertEqual(len(self.store.feedbacks()), 1)

    def test_old_edition_binds_actual_paper_not_new_number(self):
        self.data["date"] = "2026-10-20"
        self.data["cards"][0]["title_original"] = "Prior paper"
        self.store.put("digest:2026-10-20", self.data)
        response = self.post(self.action("deep"))
        with self.store.db() as db:
            row = db.execute("SELECT text FROM inbox").fetchone()
        self.assertIn("Prior paper", row["text"])
        self.assertIn("2026-10-20", row["text"])
        self.assertEqual(panel.result(self.store, response["id"])["status"], "queued")

    def test_today_uses_cached_set_without_model(self):
        with patch.object(m, "run_codex") as model:
            self.assertIn("论文0", m.answer(self.store, "今日文献"))
            model.assert_not_called()

    def test_deep_button_passes_exact_source_to_existing_research(self):
        self.post(self.action("deep"))
        with (
            patch.object(m, "run_codex", return_value="逐步解释") as model,
            patch.object(m.PushPlus, "send", return_value={"status": "sent"}),
        ):
            m.process_one(self.store)
        self.assertIn("精读：Paper 0", model.call_args.args[1])
        self.assertIn("https://example.org/paper/0", model.call_args.args[1])

    def test_cross_site_or_untrusted_host_cannot_submit(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post(self.action(), origin="https://other.example")
        self.assertEqual(err.exception.code, 403)
        req = urllib.request.Request(self.url, headers={"Host": "attacker.example"})
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        self.assertEqual(err.exception.code, 403)

    def test_invalid_actions_and_queue_bound(self):
        for change in (
            {"action": "任意系统命令"},
            {"paper": 6},
            {"paper": True},
            {"date": "bad"},
            {"action": "topic", "topic": ""},
        ):
            with self.assertRaises(urllib.error.HTTPError):
                self.post(self.action(**change))
        for _ in range(8):
            self.post(self.action())
        with self.assertRaises(urllib.error.HTTPError):
            self.post(self.action())

    def test_same_request_id_cannot_change_command(self):
        item = self.action()
        self.post(item)
        item["action"] = "deep"
        with self.assertRaises(urllib.error.HTTPError):
            self.post(item)


if __name__ == "__main__":
    unittest.main()
