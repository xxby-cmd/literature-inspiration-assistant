"""Boundary checks without live WeChat messages or paid APIs."""

import importlib.util
import sys
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion/scripts"))
spec = importlib.util.spec_from_file_location("companion", ROOT / "companion/scripts/companion.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def digest():
    cards = []
    for i in range(5):
        cards.append(
            {
                "group": "preference" if i < 3 else "exploration",
                "field": "领域" + str(i),
                "title_cn": "论文" + str(i),
                "title_original": "Paper " + str(i),
                "authors": "Test Author",
                "published_date": "2026-10-28",
                "source_url": "https://example.org/paper/" + str(i),
                "plain_summary": "仅用于本地测试，不是实际研究。",
                "why": "测试",
                "evidence_scope": "测试数据",
                "publication_status": "测试",
                "learn": "测试",
            }
        )
    return {
        "date": m.now().date().isoformat(),
        "search_scope": "本地测试，无真实论文",
        "cards": cards,
        "english": "测试英语",
        "practice": "测试实践",
    }


class ConnectorTests(unittest.TestCase):
    def test_daily_clawbot_contains_only_five_briefs_and_fixed_entry(self):
        data = digest()
        self.store.put("digest:" + data["date"], data)
        p = self.store.profile()
        p["wechat"]["enabled"] = True
        m.atomic(self.store.profile_path, json.dumps(p).encode())
        self.store.put("reader_site", {"enabled": True, "url": "https://example.chatgpt.site"})
        self.store.put(
            "reader_portal",
            {"ready": True, "shareUrl": "https://www.pushplus.plus/push/doc/Abcd1234"},
        )
        with (
            patch("reader_sync.sync"),
            patch.object(m.PushPlus, "send", return_value={"status": "sent"}) as send,
        ):
            m.daily(self.store, "morning", True)
        args = send.call_args
        self.assertEqual(args.args[3], "clawbot")
        self.assertEqual(args.kwargs["template"], "txt")
        self.assertIn("https://www.pushplus.plus/push/doc/Abcd1234", args.args[2])
        self.assertEqual(args.args[2].count("【兴趣】"), 3)
        self.assertEqual(args.args[2].count("【拓展】"), 2)
        self.assertNotIn("为什么推荐", args.args[2])
        self.assertNotIn("一点英语", args.args[2])

    def test_daily_definite_clawbot_rejection_uses_one_official_fallback(self):
        data = digest()
        self.store.put("digest:" + data["date"], data)
        p = self.store.profile()
        p["wechat"]["enabled"] = True
        m.atomic(self.store.profile_path, json.dumps(p).encode())
        with (
            patch.object(
                m, "request", side_effect=[m.ProviderRejected("rejected"), "receipt"]
            ) as request,
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
        ):
            result = m.daily(self.store, "evening", True)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[1]["channel"], "clawbot")
        self.assertEqual(request.call_args_list[1].args[1]["channel"], "wechat")

    def test_recovered_connection_keeps_definite_failure_fallback(self):
        with self.store.db() as db:
            db.execute(
                "INSERT INTO inbox(id,text,status,answer,error,source,delivery_key) VALUES (1,'test','needs_review','saved answer','','panel','inbox:1')"
            )
            db.execute(
                "INSERT INTO delivery VALUES (?,?,?,?,?)",
                (
                    "inbox:1",
                    "not_submitted",
                    "",
                    json.dumps(
                        {
                            "title": "test",
                            "content": "saved answer",
                            "channel": "clawbot",
                            "template": "txt",
                        }
                    ),
                    "",
                ),
            )
        with (
            patch.object(
                m, "request", side_effect=[m.ProviderRejected("rejected"), "official-receipt"]
            ),
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
        ):
            m.PushPlus(self.store).retry_unsent()
        with self.store.db() as db:
            row = db.execute("SELECT status,delivery_key FROM inbox WHERE id=1").fetchone()
        self.assertEqual(tuple(row), ("sent", "inbox:1:wechat"))

    def test_failed_dns_fallback_is_definitely_unsent(self):
        import socket
        import urllib.error

        with (
            patch.object(
                m.urllib.request,
                "urlopen",
                side_effect=urllib.error.URLError(socket.gaierror(11002, "DNS failed")),
            ),
            patch("pushplus_transport.addresses", side_effect=OSError("DNS failed")),
        ):
            with self.assertRaises(m.NotSubmitted):
                m.PushPlus(self.store).send("dns-outage", "title", "body", "clawbot")
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT status FROM delivery WHERE key='dns-outage'").fetchone()[0],
                "not_submitted",
            )

    def test_preconnection_failure_is_retried_with_backoff_and_original_payload(self):
        client = m.PushPlus(self.store)
        with (
            patch.object(m, "request", side_effect=m.NotSubmitted("DNS")),
            patch.object(m.time, "time", return_value=100),
        ):
            with self.assertRaises(m.NotSubmitted):
                client.send("recover", "original title", "original body", "clawbot", template="txt")
        with (
            patch.object(m, "request", return_value="receipt") as request,
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
            patch.object(m.time, "time", return_value=110),
        ):
            client.retry_unsent()
            request.assert_not_called()
        with (
            patch.object(m, "request", return_value="receipt") as request,
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
            patch.object(m.time, "time", return_value=131),
        ):
            client.send("recover", "replacement title", "replacement body", "wechat")
            self.assertEqual(request.call_args.args[1]["content"], "original body")
            self.assertEqual(request.call_args.args[1]["channel"], "clawbot")
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT status FROM delivery WHERE key='recover'").fetchone()[0], "sent"
            )

    def test_only_preconnection_dns_failure_may_use_scoped_fallback(self):
        import socket
        import urllib.error

        with (
            patch.object(
                m.urllib.request,
                "urlopen",
                side_effect=urllib.error.URLError(socket.gaierror(11002, "DNS failed")),
            ),
            patch(
                "pushplus_transport.resolved_request",
                return_value={"code": 200, "data": "received"},
            ) as fallback,
        ):
            self.assertEqual(m.request("/send", {"content": "test"}), "received")
            fallback.assert_called_once()
        with (
            patch.object(
                m.urllib.request,
                "urlopen",
                side_effect=urllib.error.URLError(TimeoutError("unknown send outcome")),
            ),
            patch("pushplus_transport.resolved_request") as fallback,
        ):
            with self.assertRaises(RuntimeError):
                m.request("/send", {"content": "test"})
            fallback.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = m.Store(self.tmp.name)
        self.store.save_credentials("dummy-token", "x" * 32)

    def tearDown(self):
        self.tmp.cleanup()

    def test_credentials_are_encrypted_and_recoverable(self):
        blob = (self.store.home / "credentials.dpapi").read_bytes()
        self.assertNotIn(b"dummy-token", blob)
        self.assertEqual(self.store.credentials()["token"], "dummy-token")

    def test_existing_provider_key_is_not_forced_to_rotate(self):
        self.store.save_credentials("dummy-token", "x" * 28)
        self.assertEqual(len(self.store.credentials()["secretKey"]), 28)

    def test_wechat_test_text_is_acknowledged_without_model_cost(self):
        with patch.object(m, "run_codex") as model:
            self.assertIn("已收到", m.answer(self.store, "文献助手接入测试"))
            model.assert_not_called()

    def test_real_interactive_reply_can_verify_channel(self):
        with self.store.db() as db:
            for key, channel in [("test:wechat", "wechat"), ("inbox:1", "clawbot")]:
                db.execute(
                    "INSERT INTO delivery VALUES (?,?,?,?,?)",
                    (key, "sent", "code", json.dumps({"channel": channel}), ""),
                )
        probe = {"realname_verified": True, "bot_bound": True, "has_conversation": True}
        with (
            patch.object(m.PushPlus, "probe", return_value=probe),
            patch.object(m.PushPlus, "check", return_value={"status": "sent"}),
        ):
            m.enable(self.store, True)
        self.assertTrue(self.store.profile()["wechat"]["enabled"])

    def test_missing_phone_confirmation_does_not_enable(self):
        probe = {"realname_verified": True, "bot_bound": True, "has_conversation": True}
        with patch.object(m.PushPlus, "probe", return_value=probe):
            with self.assertRaises(RuntimeError):
                m.enable(self.store, False)
        self.assertFalse(self.store.profile()["wechat"]["enabled"])

    def test_receipt_updates_the_corresponding_inbox_state(self):
        with self.store.db() as db:
            db.execute(
                "INSERT INTO inbox(id,text,status,answer,error) VALUES (?,?,?,?,?)",
                (7, "test", "pending", "answer", ""),
            )
            db.execute(
                "INSERT INTO delivery VALUES (?,?,?,?,?)",
                ("inbox:7", "pending", "code", json.dumps({"channel": "clawbot"}), ""),
            )
        with patch.object(m.PushPlus, "get", return_value={"status": 2}):
            m.PushPlus(self.store).check("inbox:7")
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT status FROM inbox WHERE id=7").fetchone()[0], "sent"
            )

    def test_verified_official_account_only_applies_to_wechat(self):
        p = self.store.profile()
        p["wechat"]["official_option"] = "verified-account"
        m.atomic(self.store.profile_path, json.dumps(p).encode())
        with (
            patch.object(m, "request", return_value="code") as request,
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
        ):
            client = m.PushPlus(self.store)
            client.send("official", "title", "content", "wechat")
            self.assertEqual(request.call_args.args[1]["option"], "verified-account")
            client.send("bot", "title", "content", "clawbot")
            self.assertNotIn("option", request.call_args.args[1])

    def test_acceptance_is_not_delivery(self):
        with (
            patch.object(m, "request", return_value="test-code"),
            patch.object(m.PushPlus, "get", return_value={"status": 1}),
        ):
            result = m.PushPlus(self.store).send("test", "title", "content", "wechat")
        self.assertEqual(result["status"], "sending")

    def test_final_delivery_checked_without_duplicate_send(self):
        with (
            patch.object(m, "request", return_value="test-code") as req,
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
        ):
            client = m.PushPlus(self.store)
            self.assertEqual(client.send("test", "title", "content", "wechat")["status"], "sent")
            client.send("test", "title", "content", "wechat")
            self.assertEqual(req.call_count, 1)

    def test_timeout_is_not_blindly_retried_after_restart(self):
        with patch.object(m, "request", side_effect=RuntimeError("网络失败")) as req:
            with self.assertRaises(RuntimeError):
                m.PushPlus(self.store).send("test", "title", "content", "wechat")
            restarted = m.PushPlus(m.Store(self.tmp.name))
            self.assertEqual(
                restarted.send("test", "title", "content", "wechat")["status"], "unknown"
            )
            self.assertEqual(req.call_count, 1)

    def test_paid_channel_refused(self):
        with patch.object(m, "request") as req:
            with self.assertRaises(ValueError):
                m.PushPlus(self.store).send("bad", "title", "content", "sms")
            req.assert_not_called()

    def test_empty_inbox_does_not_call_codex(self):
        m.ingest(self.store, [])
        with patch.object(m, "run_codex") as model:
            self.assertFalse(m.process_one(self.store))
            model.assert_not_called()

    def test_batch_replay_suppressed_and_followup_allowed(self):
        messages = [{"type": 1, "text": "查课题：四足机器人"}]
        m.ingest(self.store, messages)
        m.ingest(m.Store(self.tmp.name), messages)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 1)
        m.ingest(self.store, [])
        m.ingest(self.store, messages)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 2)

    def test_evening_reuses_the_same_five_papers(self):
        data = digest()
        self.store.put("digest:" + m.now().date().isoformat(), data)
        with patch.object(m, "run_codex") as model:
            m.daily(self.store, "morning")
            m.daily(self.store, "evening")
            model.assert_not_called()
        evening = (
            self.store.home / "research" / (m.now().date().isoformat() + "_evening.md")
        ).read_text(encoding="utf-8")
        self.assertIn("晚间复习", evening)
        for card in data["cards"]:
            self.assertIn(card["title_cn"], evening)
        self.assertEqual(evening.count("【兴趣】"), 3)
        self.assertEqual(evening.count("【拓展】"), 2)
        self.assertNotIn("一点英语", evening)

    def test_wrong_mix_and_duplicate_papers_refused(self):
        data = digest()
        data["cards"][0]["group"] = "exploration"
        with self.assertRaises(ValueError):
            m.validate_digest(data, data["date"])
        data = digest()
        data["cards"][1]["title_original"] = data["cards"][0]["title_original"]
        with self.assertRaises(ValueError):
            m.validate_digest(data, data["date"])

    def test_feedback_resolves_paper_and_does_not_call_model(self):
        self.store.put("digest:" + m.now().date().isoformat(), digest())
        with patch.object(m, "run_codex") as model:
            m.answer(self.store, "太难 1")
            model.assert_not_called()
        self.assertEqual(self.store.feedbacks()[0]["action"], "太难")
        self.assertIn("Paper 0", self.store.feedbacks()[0]["target"])

    def test_disabled_listener_never_polls(self):
        with patch.object(m.PushPlus, "get") as network:
            with self.assertRaises(RuntimeError):
                m.listen(self.store)
            network.assert_not_called()

    def test_reset_excludes_old_feedback_without_deleting_history(self):
        self.store.feedback("太难", "old topic")
        m.answer(self.store, "关注：材料学，天文学")
        self.assertEqual(self.store.profile()["interests"], ["材料学", "天文学"])
        m.answer(self.store, "重置偏好")
        self.assertEqual(self.store.feedbacks(), [])
        self.store.feedback("喜欢", "new topic")
        self.assertEqual(len(self.store.feedbacks()), 1)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0], 2)

    def test_arxiv_versions_cannot_count_as_distinct_papers(self):
        data = digest()
        data["cards"][0]["source_url"] = "https://arxiv.org/abs/2601.00001v1"
        data["cards"][1]["source_url"] = "https://arxiv.org/pdf/2601.00001v2.pdf"
        with self.assertRaises(ValueError):
            m.validate_digest(data, data["date"])

    def test_only_capacity_error_retries_with_same_arguments(self):
        commands = []

        def run(args, **kwargs):
            if "login" in args:
                return subprocess.CompletedProcess(args, 0, "Logged in using ChatGPT", "")
            if "--help" in args:
                return subprocess.CompletedProcess(args, 0, "--no-daemon", "")
            commands.append(args)
            if len(commands) == 1:
                return subprocess.CompletedProcess(
                    args, 1, "", "Selected model is at capacity. Please try a different model."
                )
            Path(args[args.index("-o") + 1]).write_text("success", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")

        with (
            patch.object(m.subprocess, "run", side_effect=run),
            patch.object(m.time, "sleep") as sleep,
        ):
            self.assertEqual(m.run_codex(self.store, "read-only test"), "success")
        self.assertEqual(commands[0], commands[1])
        sleep.assert_called_once_with(30)

    def test_quota_error_is_not_capacity_retry(self):
        with (
            patch.object(
                m.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "ChatGPT", ""),
                    subprocess.CompletedProcess([], 0, "--no-daemon", ""),
                    subprocess.CompletedProcess([], 1, "", "usageLimitExceeded"),
                ],
            ) as run,
            patch.object(m.time, "sleep") as sleep,
        ):
            with self.assertRaises(RuntimeError):
                m.run_codex(self.store, "test")
            self.assertEqual(run.call_count, 3)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
