from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.bootstrap import (
    DaemonCompatibilityError,
    DaemonConnectionInfo,
    _spawn_daemon_process,
    ensure_daemon_running,
)
from ask_user_via_feishu.daemon.runtime import DAEMON_PROTOCOL_VERSION, DaemonMetadata, runtime_dir_for_settings
from ask_user_via_feishu.daemon.server import SharedLongConnDaemonServer


class DaemonBootstrapTest(unittest.TestCase):
    def test_ensure_daemon_running_reuses_healthy_daemon(self) -> None:
        settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            runtime_dir = runtime_dir_for_settings(settings, base_dir=base_dir)
            daemon = SharedLongConnDaemonServer(settings, runtime_dir)
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with patch("ask_user_via_feishu.daemon.bootstrap._spawn_daemon_process") as spawn_mock:
                connection = ensure_daemon_running(settings, base_dir=base_dir)

            self.assertEqual(connection.metadata.port, daemon.metadata.port)
            self.assertEqual(connection.metadata.daemon_epoch, daemon.metadata.daemon_epoch)
            spawn_mock.assert_not_called()

    def test_ensure_daemon_running_rejects_incompatible_live_daemon(self) -> None:
        daemon_settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")
        client_settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
            base_url="https://different.example.com",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            runtime_dir = runtime_dir_for_settings(daemon_settings, base_dir=base_dir)
            daemon = SharedLongConnDaemonServer(daemon_settings, runtime_dir)
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with self.assertRaises(DaemonCompatibilityError):
                ensure_daemon_running(client_settings, base_dir=base_dir)

    def test_ensure_daemon_running_rejects_live_daemon_with_different_idle_settings(self) -> None:
        daemon_settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")
        client_settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
            daemon_idle_timeout_seconds=300,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            runtime_dir = runtime_dir_for_settings(daemon_settings, base_dir=base_dir)
            daemon = SharedLongConnDaemonServer(daemon_settings, runtime_dir)
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with self.assertRaises(DaemonCompatibilityError):
                ensure_daemon_running(client_settings, base_dir=base_dir)

    def test_ensure_daemon_running_spawns_and_waits_when_missing(self) -> None:
        settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")
        fake_connection = DaemonConnectionInfo(
            runtime_dir=Path("/tmp/demo"),
            metadata=DaemonMetadata(
                pid=123,
                port=456,
                daemon_epoch="epoch_demo",
                protocol_version=DAEMON_PROTOCOL_VERSION,
                compatibility_hash="hash_demo",
                started_at="2026-01-01T00:00:00+00:00",
                app_id="cli_demo",
            ),
            token="token_demo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.bootstrap._spawn_daemon_process") as spawn_mock,
                patch(
                    "ask_user_via_feishu.daemon.bootstrap._wait_for_ready_daemon",
                    return_value=fake_connection,
                ) as wait_mock,
            ):
                connection = ensure_daemon_running(settings, base_dir=base_dir)

        self.assertIs(connection, fake_connection)
        spawn_mock.assert_called_once()
        wait_mock.assert_called_once()

    def test_failed_daemon_is_not_reused(self) -> None:
        settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")
        fake_connection = DaemonConnectionInfo(
            runtime_dir=Path("/tmp/demo"),
            metadata=DaemonMetadata(
                pid=123,
                port=456,
                daemon_epoch="epoch_demo",
                protocol_version=DAEMON_PROTOCOL_VERSION,
                compatibility_hash="hash_demo",
                started_at="2026-01-01T00:00:00+00:00",
                app_id="cli_demo",
            ),
            token="token_demo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            runtime_dir = runtime_dir_for_settings(settings, base_dir=base_dir)
            daemon = SharedLongConnDaemonServer(
                settings,
                runtime_dir,
                status_provider=lambda: {"long_connection_state": "failed"},
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with (
                patch("ask_user_via_feishu.daemon.bootstrap._spawn_daemon_process") as spawn_mock,
                patch(
                    "ask_user_via_feishu.daemon.bootstrap._wait_for_ready_daemon",
                    return_value=fake_connection,
                ) as wait_mock,
            ):
                connection = ensure_daemon_running(settings, base_dir=base_dir)

        self.assertIs(connection, fake_connection)
        spawn_mock.assert_called_once()
        wait_mock.assert_called_once()

    def test_spawn_daemon_process_passes_effective_settings_in_env(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
            base_url="https://example.test",
            api_timeout_seconds=11,
            log_level="DEBUG",
            reaction_enabled=False,
            reaction_emoji_type="Done",
            ask_timeout_seconds=123,
            ask_reminder_max_attempts=4,
            ask_timeout_reminder_text="reply soon",
            ask_timeout_default_answer="[AUTO_RECALL]",
            daemon_idle_timeout_seconds=600,
            daemon_idle_check_interval_seconds=10,
            daemon_min_uptime_seconds=60,
        )
        with patch("ask_user_via_feishu.daemon.bootstrap.subprocess.Popen") as popen_mock:
            _spawn_daemon_process(Path("/tmp/daemon-runtime"), settings)

        kwargs = popen_mock.call_args.kwargs
        env = kwargs["env"]
        self.assertEqual(env["APP_ID"], "cli_demo")
        self.assertEqual(env["APP_SECRET"], "secret_demo")
        self.assertEqual(env["OWNER_OPEN_ID"], "ou_demo")
        self.assertEqual(env["BASE_URL"], "https://example.test")
        self.assertEqual(env["REACTION_ENABLED"], "false")
        self.assertEqual(env["ASK_TIMEOUT_SECONDS"], "123")
        self.assertEqual(env["DAEMON_IDLE_TIMEOUT_SECONDS"], "600")
        self.assertEqual(env["DAEMON_IDLE_CHECK_INTERVAL_SECONDS"], "10")
        self.assertEqual(env["DAEMON_MIN_UPTIME_SECONDS"], "60")

    def test_daemon_reuse_ignores_owner_open_id_when_app_identity_matches(self) -> None:
        daemon_settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_a")
        client_settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_b")
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            runtime_dir = runtime_dir_for_settings(daemon_settings, base_dir=base_dir)
            daemon = SharedLongConnDaemonServer(daemon_settings, runtime_dir)
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with patch("ask_user_via_feishu.daemon.bootstrap._spawn_daemon_process") as spawn_mock:
                connection = ensure_daemon_running(client_settings, base_dir=base_dir)

        self.assertEqual(connection.metadata.port, daemon.metadata.port)
        spawn_mock.assert_not_called()
