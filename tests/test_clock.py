import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


c = load("companion", ROOT / "companion/scripts/companion.py")


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = c.Store(self.tmp.name)
        p = self.store.profile()
        p["wechat"]["enabled"] = True
        c.atomic(self.store.profile_path, json.dumps(p).encode())

    def tearDown(self):
        self.tmp.cleanup()

    def instant(self, hour, minute=0):
        return dt.datetime(2026, 10, 29, hour, minute, tzinfo=c.TZ)

    def test_preparation_and_exact_nine_boundaries(self):
        submit = MagicMock()
        self.assertIsNone(c.service_step(self.store, self.instant(8, 29), False, submit))
        self.assertEqual(
            c.service_step(self.store, self.instant(8, 30), False, submit), "2026-10-29:prepare"
        )
        self.assertEqual(submit.call_args.args, ("daily", "morning", False))
        c.service_step(self.store, self.instant(9), False, submit)
        self.assertEqual(submit.call_args.args, ("daily", "morning", True))
        c.service_step(self.store, self.instant(21), False, submit)
        self.assertEqual(submit.call_args.args, ("daily", "evening", True))

    def test_no_historical_morning_catchup_at_night_or_double_send(self):
        submit = MagicMock()
        with self.store.db() as db:
            db.execute(
                "INSERT INTO delivery VALUES (?,?,?,?,?)",
                ("2026-10-29:evening", "unknown", "", "{}", ""),
            )
        self.assertIsNone(c.service_step(self.store, self.instant(22), False, submit))
        submit.assert_not_called()

    def test_busy_and_unbound_clocks_do_not_dispatch(self):
        submit = MagicMock()
        c.service_step(self.store, self.instant(9), True, submit)
        p = self.store.profile()
        p["enabled"] = False
        c.atomic(self.store.profile_path, json.dumps(p).encode())
        c.service_step(self.store, self.instant(9), False, submit)
        submit.assert_not_called()

    def test_prepared_reminder_can_run_while_model_is_reading(self):
        current = self.instant(9)
        self.assertTrue(c.clock_busy(self.store, current, True, False))
        self.store.put("digest:2026-10-29", {"date": "2026-10-29"})
        self.assertFalse(c.clock_busy(self.store, current, True, False))
        self.assertTrue(c.clock_busy(self.store, current, True, True))
        self.assertTrue(c.clock_busy(self.store, self.instant(8, 35), True, False))

    def test_reminder_thread_never_starts_generation_when_digest_missing(self):
        with patch.object(c, "run_codex") as model:
            with self.assertRaisesRegex(RuntimeError, "不在提醒线程"):
                c.daily(self.store, "morning", True, cached_only=True)
            model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
