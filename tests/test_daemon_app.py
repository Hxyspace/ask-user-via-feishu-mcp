from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.app import SharedLongConnDaemonApp


class FakeMessageService:
    def __init__(self, *, raise_on_health: bool = False) -> None:
        self.health_calls = 0
        self.raise_on_health = raise_on_health

    async def health_check(self) -> dict[str, object]:
        self.health_calls += 1
        if self.raise_on_health:
            raise RuntimeError("auth failed")
        return {"ok": True}


class FakeSharedRuntime:
    def long_connection_state(self) -> str:
        return "running"

    def has_pending_question(self) -> bool:
        return False

    def current_pending_question_id(self) -> str:
        return ""


class DaemonAppTest(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")

    def test_initialize_does_not_create_local_tenant_token_file(self) -> None:
        service = FakeMessageService()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            token_cache_path = runtime_dir / "tenant-token.json"
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=FakeSharedRuntime()),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app.initialize()

        self.assertEqual(service.health_calls, 1)
        self.assertFalse(token_cache_path.exists())

    def test_initialize_failure_does_not_create_local_tenant_token_file(self) -> None:
        service = FakeMessageService(raise_on_health=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            token_cache_path = runtime_dir / "tenant-token.json"
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=FakeSharedRuntime()),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                with self.assertRaisesRegex(RuntimeError, "auth failed"):
                    app.initialize()


        self.assertEqual(service.health_calls, 1)
        self.assertFalse(token_cache_path.exists())
