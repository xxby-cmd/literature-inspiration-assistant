"""Installer state transitions and account/instance isolation without live AI."""

import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "companion/scripts"))
import companion
from codex_session import clean_environment
from scripts import manage, runtime, wechat


class InstallationTests(unittest.TestCase):
    def test_windows_entrypoints_retain_crlf_for_github_zip_downloads(self):
        for entry in ROOT.glob("*.cmd"):
            data = entry.read_bytes()
            self.assertEqual(data.count(b"\r\n"), data.count(b"\n"), entry.name)

    def test_old_cli_is_not_reused_without_required_capabilities(self):
        with patch.object(
            runtime,
            "run",
            side_effect=["--search --ask-for-approval", "--sandbox --skip-git-repo-check"],
        ):
            self.assertFalse(runtime.codex_suitable("old-codex.exe"))
        with patch.object(
            runtime,
            "run",
            side_effect=[
                "--search --ask-for-approval",
                "--ephemeral --output-schema --skip-git-repo-check --sandbox --output-last-message",
            ],
        ):
            self.assertTrue(runtime.codex_suitable("new-codex.exe"))

    def test_incompatible_global_cli_falls_back_to_project_owned_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "old-codex.exe"
            old.touch()
            owned = root / "tools/codex/node_modules/@openai/codex-native/codex.exe"

            def install(*args, **kwargs):
                owned.parent.mkdir(parents=True, exist_ok=True)
                owned.touch()

            with (
                patch.object(runtime, "codex_candidates", return_value=iter([old])),
                patch.object(
                    runtime, "codex_suitable", side_effect=lambda value: Path(value) == owned
                ),
                patch.object(runtime, "run", side_effect=install) as install_command,
                patch.object(runtime, "npm", return_value=["node", "npm"]),
            ):
                self.assertEqual(runtime.ensure_codex(root / "data", Path("node.exe")), owned)
                install_command.assert_called_once()

    def test_stale_tunnel_status_cannot_complete_new_onboarding(self):
        s = MagicMock()
        s.meta.side_effect = [
            {"status": "connected", "run_id": "old", "time": 10, "url": "https://old.example"},
            {"url": "http://127.0.0.1:18743"},
        ]
        self.assertFalse(wechat.channel_ready(s, 20))
        s.meta.side_effect = [
            {"status": "connected", "run_id": "new", "time": 30, "url": "https://new.example"},
            {"url": "https://new.example"},
        ]
        self.assertTrue(wechat.channel_ready(s, 20))

    def test_codex_uses_clean_project_home_without_modifying_shared_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shared = root / "shared"
            shared.mkdir()
            configuration = shared / "config.toml"
            configuration.write_text('model_provider="paid-example"', encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(shared),
                    "CODEX_API_KEY": "not-real-api-key",
                    "OPENAI_API_KEY": "not-real-api-key",
                    "OPENAI_BASE_URL": "https://example.invalid",
                },
            ):
                env = clean_environment(root / "application")
            self.assertEqual(Path(env["CODEX_HOME"]), root / "application/codex")
            self.assertNotIn("CODEX_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("OPENAI_BASE_URL", env)
            self.assertEqual(
                configuration.read_text(encoding="utf-8"), 'model_provider="paid-example"'
            )
            self.assertEqual(
                (root / "application/codex/config.toml").read_text(encoding="utf-8"),
                'model_provider = "openai"\n',
            )

    def test_occupied_ports_are_detected_and_another_complete_group_is_selected(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        try:
            self.assertFalse(runtime.ports_available({"worker": port}))
            chosen = runtime.choose_ports(28740)
            self.assertTrue(runtime.ports_available(chosen))
            self.assertEqual(len(set(chosen.values())), 4)
        finally:
            held.close()

    def test_zip_cannot_escape_installation_directory(self):
        import zipfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as data:
                data.writestr("../escape.txt", "bad")
            with self.assertRaises(RuntimeError):
                runtime.extract_zip(archive, root / "destination")
            self.assertFalse((root / "escape.txt").exists())

    def test_local_clock_does_not_require_wechat_or_submit_a_push(self):
        import datetime

        with tempfile.TemporaryDirectory() as folder:
            s = companion.Store(folder)
            p = s.profile()
            p["enabled"] = True
            p["wechat"]["enabled"] = False
            companion.atomic(s.profile_path, json.dumps(p).encode())
            instant = datetime.datetime(2026, 10, 29, 9, tzinfo=companion.TZ)
            submit = MagicMock()
            companion.service_step(s, instant, False, submit)
            submit.assert_called_once_with("daily", "morning", False)
            with patch.object(
                s, "credentials", side_effect=AssertionError("must not read credentials")
            ):
                companion.poll_wechat(s, MagicMock())


class WeChatCancellationTests(unittest.TestCase):
    def case(self, answers, start_error=None, portal_error=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        s = companion.Store(temporary.name)
        p = s.profile()
        p["enabled"] = True
        companion.atomic(s.profile_path, json.dumps(p).encode())
        s.save_credentials("dummy-token", "dummy-secret")
        s.put("reader_runtime", {"enabled": True, "public": False})
        s.put(
            "reader_gateway",
            {
                "status": "connected",
                "run_id": "test-current",
                "time": 10**12,
                "url": "https://example.test",
            },
        )
        s.put("reader_site", {"url": "https://example.test"})
        client = MagicMock()
        client.probe.return_value = {
            "realname_verified": True,
            "bot_bound": True,
            "has_conversation": True,
        }
        client.send.return_value = {"status": "sent"}
        patches = [
            patch.object(manage, "installation", return_value={"demo": False}),
            patch.object(manage, "stop"),
            patch.object(manage, "start", side_effect=start_error),
            patch.object(wechat, "current_ip", return_value="192.0.2.1"),
            patch.object(wechat, "ensure_cloudflared", return_value=Path("test-cloudflared.exe")),
            patch.object(wechat.webbrowser, "open"),
            patch.object(companion, "PushPlus", return_value=client),
            patch(
                "reader_portal.sync_portal",
                return_value="https://www.pushplus.plus/push/doc/Example12",
                side_effect=portal_error,
            ),
            patch("builtins.input", side_effect=answers),
            patch("sys.stdout", new_callable=io.StringIO),
        ]
        from contextlib import ExitStack

        with ExitStack() as stack:
            for candidate in patches:
                stack.enter_context(candidate)
            if start_error or portal_error:
                with self.assertRaises(RuntimeError):
                    wechat.configure(Path(temporary.name), SimpleNamespace(yes=False, wait=1))
            else:
                wechat.configure(Path(temporary.name), SimpleNamespace(yes=False, wait=1))
        self.assertTrue(s.profile()["enabled"])
        return s, client

    def test_declining_message_keeps_local_research_enabled(self):
        s, client = self.case(["y", "n", "", "n"])
        self.assertFalse(s.profile()["wechat"]["enabled"])
        client.send.assert_not_called()

    def test_declining_phone_confirmation_does_not_enable_unverified_wechat(self):
        s, client = self.case(["y", "n", "", "y", "n"])
        self.assertFalse(s.profile()["wechat"]["enabled"])

    def test_startup_failure_restores_local_research(self):
        self.case(["y", "n", ""], start_error=RuntimeError("test startup failure"))

    def test_portal_failure_restores_local_research(self):
        self.case(["y", "n", ""], portal_error=RuntimeError("test portal failure"))

    def test_success_enables_wechat_after_phone_confirmation(self):
        s, client = self.case(["y", "n", "", "y", "y"])
        self.assertTrue(s.profile()["wechat"]["enabled"])


if __name__ == "__main__":
    unittest.main()
