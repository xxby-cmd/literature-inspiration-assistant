import json
import tempfile
import unittest
from unittest.mock import MagicMock
from test_connector import m, digest
import discovery_engine as engine
import reading_engine
import reader_gateway


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = m.Store(self.temp.name)
        self.old = digest()
        self.store.put("digest:" + self.old["date"], self.old)
        self.payload = {
            "kind": "discover",
            "mode": "more",
            "node_id": "a" * 32,
            "date": self.old["date"],
            "topic": "",
        }
        self.new = digest()
        for i, c in enumerate(self.new["cards"]):
            c["source_url"] = "https://new.example/" + str(i)
            c["title_original"] = "New fixture " + str(i)

    def tearDown(self):
        self.temp.cleanup()

    def test_new_five_are_immutable_and_daily_digest_is_preserved(self):
        generate = MagicMock(return_value=json.dumps(self.new))
        a = reading_engine.execute(self.store, self.payload, generate)
        b = reading_engine.execute(self.store, self.payload, generate)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(a, b)
        self.assertEqual(self.store.meta("digest:" + self.old["date"]), self.old)
        self.assertIn(self.old["cards"][0]["source_url"], generate.call_args.args[1])

    def test_seen_alias_or_title_cannot_fill_a_new_batch(self):
        old = digest()
        old["cards"][0]["source_url"] = "https://arxiv.org/abs/2401.12345"
        self.new["cards"][0]["source_url"] = "https://arxiv.org/html/2401.12345v4"
        with self.assertRaises(ValueError):
            engine.validate_new(self.new, self.old["date"], old["cards"])
        self.new["cards"][0]["source_url"] = "https://new.example/0"
        self.new["cards"][0]["title_original"] = old["cards"][1]["title_original"]
        with self.assertRaises(ValueError):
            engine.validate_new(self.new, self.old["date"], old["cards"])

    def test_reading_number_is_resolved_within_its_original_batch(self):
        new = dict(self.new, batch_id="b" * 32)
        self.store.put("batch:" + "b" * 32, new)
        reading = {
            "kind": "coarse",
            "date": self.old["date"],
            "paper": 1,
            "batch": "b" * 32,
            "source_url": new["cards"][0]["source_url"],
        }
        self.assertEqual(reading_engine.validate_payload(self.store, reading), new["cards"][0])
        with self.assertRaises(ValueError):
            reading_engine.validate_payload(self.store, dict(reading, batch=""))

    def test_similar_seed_must_be_a_known_paper(self):
        valid = dict(self.payload, mode="similar", seed=self.old["cards"][0])
        engine.validate_request(self.store, valid)
        with self.assertRaises(ValueError):
            engine.validate_request(self.store, dict(valid, seed=self.new["cards"][0]))

    def test_topic_and_similar_use_the_chosen_question_or_seed_and_keep_daily(self):
        for mode in ("topic", "similar"):
            with self.subTest(mode=mode):
                payload = dict(
                    self.payload,
                    node_id=("b" if mode == "topic" else "c") * 32,
                    mode=mode,
                    topic="contact aided leg odometry" if mode == "topic" else "",
                    seed=self.old["cards"][1],
                )
                generate = MagicMock(return_value=json.dumps(self.new))
                result = reading_engine.execute(self.store, payload, generate)
                prompt = generate.call_args.args[1]
                self.assertIn(
                    payload["topic"] if mode == "topic" else payload["seed"]["title_original"],
                    prompt,
                )
                self.assertEqual(result["digest"]["mode"], mode)
                self.assertEqual(len(result["digest"]["cards"]), 5)
                self.assertEqual(self.store.meta("digest:" + self.old["date"]), self.old)
                # The second mode needs different papers: old manual batches
                # are excluded just like daily groups.
                for i, card in enumerate(self.new["cards"]):
                    card["source_url"] = "https://another.example/" + str(i)
                    card["title_original"] = "Another fixture " + str(i)

    def test_new_public_paths_do_not_expose_private_worker_sync(self):
        for path in ("/api/discover", "/api/paper-state"):
            self.assertTrue(reader_gateway.permitted(path, "POST"))
        for path in ("/api/batches", "/api/batch", "/api/shelf"):
            self.assertTrue(reader_gateway.permitted(path, "GET"))
        self.assertFalse(reader_gateway.permitted("/api/sync/claim", "POST"))
        self.assertFalse(reader_gateway.permitted("/api/sync/result", "POST"))


if __name__ == "__main__":
    unittest.main()
