from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ask_user_via_feishu.ask_state import AskStatusSnapshot
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.app import SharedLongConnDaemonApp
from ask_user_via_feishu.errors import RetryableAskError


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

    def ask_status_snapshot(self) -> AskStatusSnapshot:
        return AskStatusSnapshot(active_ask_count=0, queued_ask_count=0)


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

    def test_terminal_daemon_rejects_new_asks(self) -> None:
        service = FakeMessageService()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=FakeSharedRuntime()),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._daemon_state = "terminal_failed"
                app._failure_reason = "ws failed"

                with self.assertRaises(RetryableAskError) as error:
                    app._ask_and_wait(
                        {
                            "question": "继续吗？",
                            "choices": [],
                            "receive_id_type": "open_id",
                            "receive_id": "ou_demo",
                            "timeout_seconds": 60,
                            "reminder_max_attempts": 0,
                            "timeout_reminder_text": "",
                            "timeout_default_answer": "",
                        }
                    )

        self.assertEqual(error.exception.retry_stage, "before_send")

    def test_terminal_failure_updates_status_and_retires_daemon(self) -> None:
        service = FakeMessageService()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=FakeSharedRuntime()),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._server.shutdown = Mock()
                app._terminal_shutdown_delay_seconds = 0

                app._handle_terminal_failure(RuntimeError("ws failed"))
                if app._retirement_thread is not None:
                    app._retirement_thread.join(1)

                status = app._status()

        self.assertEqual(status["daemon_state"], "shutting_down")
        self.assertEqual(status["failure_reason"], "ws failed")
        self.assertEqual(status["active_ask_count"], 0)
        self.assertEqual(status["queued_ask_count"], 0)
        self.assertEqual(status["queues_by_target"], [])
        self.assertEqual(status["queue_exempt_question_ids"], [])
        app._server.shutdown.assert_called_once()

    def test_ask_and_wait_forwards_optional_card_fields(self) -> None:
        service = FakeMessageService()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=FakeSharedRuntime()),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._ask_runtime.ask = AsyncMock(return_value={"ok": True, "status": "answered", "user_answer": "done"})

                app._ask_and_wait(
                    {
                        "question": "继续吗？",
                        "choices": [],
                        "receive_id_type": "chat_id",
                        "receive_id": "oc_demo",
                        "allowed_actor_open_id": "ou_demo",
                        "question_id": "select_123",
                        "card": {"header": {"title": {"tag": "plain_text", "content": "选择会话"}}},
                        "timeout_seconds": 60,
                        "reminder_max_attempts": 0,
                        "timeout_reminder_text": "",
                        "timeout_default_answer": "",
                    }
                )

        call_kwargs = app._ask_runtime.ask.await_args.kwargs
        self.assertEqual(call_kwargs["receive_id_type"], "chat_id")
        self.assertEqual(call_kwargs["receive_id"], "oc_demo")
        self.assertEqual(call_kwargs["allowed_actor_open_id"], "ou_demo")
        self.assertEqual(call_kwargs["question_id"], "select_123")
        self.assertEqual(call_kwargs["card"]["header"]["title"]["content"], "选择会话")
