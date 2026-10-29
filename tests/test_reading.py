import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from test_connector import m, digest
import reading_engine as engine
import reader_sync as sync


class ReadingTests(unittest.TestCase):
    def test_completed_reading_is_published_without_clawbot_message(self):
        job = "quiet-reading"
        result = {
            "title": "精读",
            "body": "只在网页出现的完整正文",
            "scope": "测试证据",
            "kind": "deep",
            "expires_at": None,
        }
        with self.store.db() as db:
            db.execute(
                "INSERT INTO inbox(id,text,status,answer,error,source,reader_job) VALUES (99,'精读','queued','','','panel',?)",
                (job,),
            )
            db.execute(
                "INSERT INTO reader_requests VALUES (?,99,?,'','queued',0,NULL)",
                (job, json.dumps(self.payload)),
            )
        with (
            patch.object(engine, "execute", return_value=result),
            patch.object(sync, "sync") as upload,
            patch.object(m.PushPlus, "send") as send,
        ):
            self.assertTrue(m.process_one(self.store))
            send.assert_not_called()
            upload.assert_called_once()
        with self.store.db() as db:
            row = db.execute("SELECT status,answer,delivery_key FROM inbox WHERE id=99").fetchone()
            self.assertEqual(tuple(row), ("complete", "", ""))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM delivery").fetchone()[0], 0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = m.Store(self.tmp.name)
        self.digest = digest()
        self.store.put("digest:" + self.digest["date"], self.digest)
        self.site = {"enabled": True, "url": "https://example.chatgpt.site", "id": "unit-test"}
        self.store.put("reader_site", self.site)
        self.payload = {
            "node_id": "a" * 32,
            "generation": 1,
            "kind": "coarse",
            "date": self.digest["date"],
            "paper": 1,
            "source_url": self.digest["cards"][0]["source_url"],
            "level": 0,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def response(self, **values):
        result = {
            "title": "论文解读",
            "body": "有证据的通俗正文",
            "evidence_scope": "仅核对摘要",
            "access_status": "abstract",
            "full_translation": False,
            "license_url": "",
        }
        result.update(values)
        return json.dumps(result, ensure_ascii=False)

    def test_progressive_explanation_uses_previous_version_not_only_paper(self):
        payload = dict(
            self.payload,
            kind="explain",
            level=3,
            parent_id="b" * 32,
            parent_text="上一版的独特小例子",
            parent_scope="摘要",
        )
        generate = MagicMock(return_value=self.response())
        engine.execute(self.store, payload, generate)
        self.assertIn("上一版的独特小例子", generate.call_args.args[1])
        self.assertIn("当前解释级别：3", generate.call_args.args[1])
        self.assertFalse(generate.call_args.kwargs["retain_output"])

    def test_neutral_is_neutral_and_does_not_call_model(self):
        generate = MagicMock()
        result = engine.execute(self.store, dict(self.payload, kind="neutral"), generate)
        self.assertEqual(self.store.feedbacks()[0]["action"], "无感")
        self.assertIn("中性", result["body"])
        generate.assert_not_called()

    def test_wrong_paper_and_missing_previous_layer_are_rejected(self):
        with self.assertRaises(ValueError):
            engine.validate_payload(
                self.store, dict(self.payload, source_url="https://elsewhere.example/")
            )
        with self.assertRaises(ValueError):
            engine.validate_payload(self.store, dict(self.payload, kind="explain"))

    def test_full_translation_requires_reusable_license_and_actual_fulltext(self):
        for url in (
            "",
            "https://arxiv.org/licenses/nonexclusive-distrib/1.0/",
            "https://creativecommons.org/licenses/by-nd/4.0/",
        ):
            with self.assertRaises(ValueError):
                engine.validate_result(
                    json.loads(
                        self.response(
                            full_translation=True, access_status="fulltext", license_url=url
                        )
                    ),
                    "translation",
                )
        engine.validate_result(
            json.loads(
                self.response(
                    full_translation=True,
                    access_status="fulltext",
                    license_url="https://creativecommons.org/licenses/by/4.0/",
                )
            ),
            "translation",
        )

    def test_translation_cache_is_seven_days_and_not_copied_into_wechat(self):
        with patch.object(engine.time, "time", return_value=100):
            result = engine.execute(
                self.store,
                dict(self.payload, kind="translation"),
                MagicMock(return_value=self.response(body="临时译文唯一正文")),
            )
        self.assertEqual(result["expires_at"], 100 + 7 * 86400)
        text = engine.notification(self.store, self.payload, result)
        self.assertNotIn("临时译文唯一正文", text)
        self.assertIn("今日文献", text)

    def test_expired_translation_body_is_removed_from_managed_cache(self):
        with self.store.db() as db:
            db.execute(
                "INSERT INTO reader_requests VALUES ('job',NULL,'{}',?,'complete',1,100)",
                (json.dumps({"body": "临时正文"}),),
            )
        sync.cleanup(self.store, 101)
        with self.store.db() as db:
            self.assertEqual(
                tuple(db.execute("SELECT result,status FROM reader_requests").fetchone()),
                ("", "expired"),
            )

    def test_replayed_cloud_job_does_not_duplicate_local_work(self):
        job = {"id": "a" * 32 + "-1", "payload": self.payload}
        client = MagicMock()
        client.post.side_effect = (
            lambda path, data: {"job": job} if path == "claim" else {"ok": True}
        )
        sync.sync(self.store, client=client)
        sync.sync(self.store, client=client)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 1)

    def test_coarse_preparation_uses_one_private_deduplicating_request_per_digest(self):
        client = MagicMock()
        client.post.side_effect = (
            lambda path, data: {"complete": True, "nodes": [{"id": "a" * 32, "status": "ready"}]}
            if path == "prepare"
            else {"ok": True}
        )
        sync.sync(self.store, allow_claim=False, client=client)
        sync.sync(self.store, allow_claim=False, client=client)
        prepared = [call for call in client.post.call_args_list if call.args[0] == "prepare"]
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].args[1], {"date": self.digest["date"]})
        self.assertEqual(self.store.meta("coarse_preparation")["date"], self.digest["date"])

    def test_user_requests_are_processed_before_claimed_background_readings(self):
        for index, background in [(1, True), (2, False)]:
            payload = dict(self.payload, background=background)
            with self.store.db() as db:
                db.execute(
                    "INSERT INTO inbox(id,text,status,answer,error,source,reader_job) VALUES (?,?,'queued','','','panel',?)",
                    (index, "阅读", str(index)),
                )
                db.execute(
                    "INSERT INTO reader_requests VALUES (?,?,?,'','queued',0,NULL)",
                    (str(index), index, json.dumps(payload)),
                )
        with (
            patch.object(
                engine,
                "execute",
                return_value={
                    "title": "测试",
                    "body": "已完成",
                    "scope": "测试",
                    "kind": "coarse",
                    "expires_at": None,
                },
            ),
            patch.object(sync, "sync"),
        ):
            m.process_one(self.store)
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT status FROM inbox WHERE id=2").fetchone()[0], "complete"
            )
            self.assertEqual(
                db.execute("SELECT status FROM inbox WHERE id=1").fetchone()[0], "queued"
            )

    def test_unverified_full_translation_is_not_published_as_fulltext(self):
        generate = MagicMock(
            return_value=self.response(
                body="未经核实的全文正文",
                full_translation=True,
                access_status="fulltext",
                license_url="https://creativecommons.org/licenses/by/4.0/",
            )
        )
        with patch.object(engine, "source_has_license", return_value=False):
            result = engine.execute(self.store, dict(self.payload, kind="translation"), generate)
        self.assertNotIn("未经核实的全文正文", result["body"])
        self.assertIn("摘要", result["scope"])


if __name__ == "__main__":
    unittest.main()
