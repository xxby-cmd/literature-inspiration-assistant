import html
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from test_connector import m, digest
import form_bridge as f


class MemberFormTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = m.Store(self.tmp.name)
        self.form = {
            "id": 160,
            "formCode": "examplecode",
            "fillUrl": "https://www.pushplus.plus/push/form/examplecode",
            "date": "2026-10-20",
            "enabled": True,
        }
        self.store.put("push_form", self.form)
        self.digest = digest()
        self.digest["date"] = "2026-10-20"
        self.store.put("digest:2026-10-20", self.digest)
        self.client = MagicMock()

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, action="喜欢"):
        return {
            "event": "form.submit",
            "formId": "160",
            "formCode": "examplecode",
            "responseId": 42,
            "updated": False,
            "answerMap": {"action": action, "paper": "2026-10-20｜1｜论文0"},
        }

    def document(self, event):
        return (
            '<div id="messageContent">'
            + html.escape(
                json.dumps({"bridge": "literature-form-v1", "payload": event}, ensure_ascii=False)
            )
            + "</div><div>unrelated</div>"
        )

    def test_txt_receipt_preserves_json_and_literal_markup(self):
        event = self.event("查新课题")
        event["answerMap"]["topic"] = "<script>text</script>\n引号“” & 小于号 <"
        self.assertEqual(f.parse_receipt(self.document(event)), event)
        self.assertEqual(
            f.parse_receipt(
                self.document(event).replace(
                    'id="messageContent">', 'id="messageContent">已收到选择，助手正在处理。\n\n'
                )
            ),
            event,
        )

    def test_callback_test_and_edited_response_do_not_trigger(self):
        self.assertIsNone(f.action_from_event({"event": "form.webhook.test"}, self.form))
        event = self.event()
        event["updated"] = True
        self.assertIsNone(f.action_from_event(event, self.form))

    def test_foreign_form_and_missing_paper_are_rejected(self):
        for field, value in [("formId", 161), ("formCode", "different"), ("responseId", True)]:
            event = self.event()
            event[field] = value
            with self.assertRaises(ValueError):
                f.action_from_event(event, self.form)
        event = self.event()
        event["answerMap"]["paper"] = ""
        with self.assertRaises(ValueError):
            f.action_from_event(event, self.form)

    def test_duplicate_webhooks_use_same_durable_request(self):
        self.client.messages.return_value = {
            "pages": 1,
            "list": [
                {"title": f.BRIDGE_TITLE, "shortCode": "bbbbbbbb"},
                {"title": f.BRIDGE_TITLE, "shortCode": "aaaaaaaa"},
            ],
        }
        with patch.object(f, "receipt_document", return_value=self.document(self.event())):
            f.poll_forms(self.store, self.client)
            f.poll_forms(self.store, self.client)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 1)
            self.assertIn("Paper 0", db.execute("SELECT text FROM inbox").fetchone()[0])
        self.client.send.assert_not_called()

    def test_missing_topic_is_not_forwarded_to_model(self):
        self.client.messages.return_value = {
            "pages": 1,
            "list": [{"title": f.BRIDGE_TITLE, "shortCode": "cccccccc"}],
        }
        with patch.object(
            f, "receipt_document", return_value=self.document(self.event("查新课题"))
        ):
            f.poll_forms(self.store, self.client)
        self.client.send.assert_called_once()
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 0)

    def test_unrelated_message_content_is_never_read(self):
        self.client.messages.return_value = {
            "pages": 1,
            "list": [{"title": "其他消息", "shortCode": "cccccccc"}],
        }
        with patch.object(f, "receipt_document") as read:
            f.poll_forms(self.store, self.client)
        read.assert_not_called()

    def test_callback_credentials_do_not_appear_in_public_footer(self):
        self.store.save_credentials("private-token", "existing-secret")
        profile = self.store.profile()
        profile["wechat"]["official_option"] = "verified"
        m.atomic(self.store.profile_path, json.dumps(profile).encode())
        body = f.design(self.store, self.digest)
        self.assertIn("private-token", body["settings"]["webhookUrl"])
        self.assertNotIn("private-token", f.menu_footer(self.store))
        self.assertFalse(body["settings"]["allowEdit"])
        self.assertFalse(body["settings"]["allowAnonymous"])

    def test_native_wechat_form_card_uses_public_code_only(self):
        self.store.save_credentials("private-token", "existing-secret")
        with (
            patch.object(m, "request", return_value="receipt") as request,
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
        ):
            m.PushPlus(self.store).send(
                "menu", "点选入口", "选择后提交", "wechat", template="form", push_id="examplecode"
            )
        self.assertEqual(request.call_args.args[1]["template"], "form")
        self.assertEqual(request.call_args.args[1]["pushId"], "examplecode")

    def reply_fixture(self, status="failed"):
        self.store.save_credentials("private-token", "existing-secret")
        with self.store.db() as db:
            db.execute(
                "INSERT INTO inbox(id,text,status,answer,error,source,delivery_key) VALUES (1,'我的偏好','pending','既有答复','','panel','inbox:1')"
            )
            db.execute(
                "INSERT INTO delivery VALUES (?,?,?,?,?)",
                ("inbox:1", status, "botreceipt", json.dumps({"channel": "clawbot"}), ""),
            )

    def test_definite_bot_failure_falls_back_once_and_receipts_follow_active_key(self):
        self.reply_fixture()
        client = m.PushPlus(self.store)
        with (
            patch.object(m, "request", return_value="officialreceipt") as send,
            patch.object(client, "get", return_value={"status": 2}),
        ):
            self.assertEqual(client.fallback_reply("inbox:1")["status"], "sent")
            self.assertIsNone(client.fallback_reply("inbox:1"))
            send.assert_called_once()
        # A stale receipt for the failed bot cannot overwrite the successful fallback.
        with patch.object(client, "get", return_value={"status": 3}):
            client.check("inbox:1")
        with self.store.db() as db:
            row = db.execute("SELECT status,delivery_key FROM inbox").fetchone()
        self.assertEqual(tuple(row), ("sent", "inbox:1:wechat"))

    def test_unknown_bot_delivery_does_not_trigger_a_second_channel(self):
        self.reply_fixture("unknown")
        with patch.object(m, "request") as send:
            self.assertIsNone(m.PushPlus(self.store).fallback_reply("inbox:1"))
        send.assert_not_called()

    def test_bot_preference_and_explicit_rejection_use_official_fallback(self):
        self.store.save_credentials("private-token", "existing-secret")
        profile = self.store.profile()
        profile["wechat"]["form_reply_channel"] = "clawbot"
        m.atomic(self.store.profile_path, json.dumps(profile).encode())
        with self.store.db() as db:
            db.execute(
                "INSERT INTO inbox(text,status,answer,error,source) VALUES ('我的偏好','queued','','','panel')"
            )
        with (
            patch.object(
                m, "request", side_effect=[m.ProviderRejected("已明确拒收"), "receipt"]
            ) as send,
            patch.object(m.PushPlus, "get", return_value={"status": 2}),
        ):
            m.process_one(self.store)
        self.assertEqual(send.call_args_list[0].args[1]["channel"], "clawbot")
        self.assertEqual(send.call_args_list[0].args[1]["template"], "txt")
        self.assertEqual(send.call_args_list[1].args[1]["channel"], "wechat")
        with self.store.db() as db:
            self.assertEqual(
                tuple(db.execute("SELECT status,delivery_key FROM inbox").fetchone()),
                ("sent", "inbox:1:wechat"),
            )


if __name__ == "__main__":
    unittest.main()
