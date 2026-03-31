from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ask_user_via_feishu.ask_state import AskStatusSnapshot, TargetQueueStatus
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
    def __init__(self, *, pending: bool = False) -> None:
        self.pending = pending

    def long_connection_state(self) -> str:
        return "running"

    def has_pending_question(self) -> bool:
        return self.pending

    def current_pending_question_id(self) -> str:
        return "ask_pending" if self.pending else ""

    def ask_status_snapshot(self) -> AskStatusSnapshot:
        return AskStatusSnapshot(
            active_ask_count=1,
            queued_ask_count=1,
            queues_by_target=(
                TargetQueueStatus(
                    delivery_key="chat_id:oc_demo",
                    receive_id_type="chat_id",
                    receive_id="oc_demo",
                    active_question_id="ask_123",
                    active_client_id="client_alpha",
                    active_client_request_id="request_alpha",
                    queued_question_ids=("ask_456",),
                    queued_client_ids=("client_beta",),
                    queued_client_request_ids=("request_beta",),
                ),
            ),
            queue_exempt_question_ids=("select_target_123",),
        )


class DaemonAppTest(unittest.TestCase):
    def _settings(self, **overrides: object) -> Settings:
        defaults: dict[str, object] = {
            "app_id": "cli_demo",
            "app_secret": "secret_demo",
            "owner_open_id": "ou_demo",
        }
        defaults.update(overrides)
        return Settings(**defaults)

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
        self.assertEqual(status["active_ask_count"], 1)
        self.assertEqual(status["queued_ask_count"], 1)
        self.assertEqual(status["queues_by_target"][0]["active_client_id"], "client_alpha")
        self.assertEqual(status["queues_by_target"][0]["active_client_request_id"], "request_alpha")
        self.assertEqual(status["queues_by_target"][0]["queued_client_ids"], ["client_beta"])
        self.assertEqual(status["queues_by_target"][0]["queued_client_request_ids"], ["request_beta"])
        self.assertEqual(status["queue_exempt_question_ids"], ["select_target_123"])
        app._server.shutdown.assert_called_once()

    def test_request_activity_updates_inflight_and_timestamp(self) -> None:
        service = FakeMessageService()
        shared_runtime = FakeSharedRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=shared_runtime),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._last_client_activity_at = 0

                app._record_request_started("/v1/status")
                started_timestamp = app._last_client_activity_at
                self.assertEqual(app._in_flight_request_count, 1)
                self.assertGreater(started_timestamp, 0)

                app._record_request_finished("/v1/status")

        self.assertEqual(app._in_flight_request_count, 0)
        self.assertGreaterEqual(app._last_client_activity_at, started_timestamp)

    def test_mark_serving_started_resets_idle_baseline(self) -> None:
        service = FakeMessageService()
        shared_runtime = FakeSharedRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=shared_runtime),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._started_at_monotonic = 0
                app._last_client_activity_at = 0

                app._mark_serving_started(now_monotonic=50)

        self.assertEqual(app._started_at_monotonic, 50)
        self.assertEqual(app._last_client_activity_at, 50)

    def test_idle_retirement_shuts_down_when_timeout_elapsed_without_pending_work(self) -> None:
        service = FakeMessageService()
        shared_runtime = FakeSharedRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=shared_runtime),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._server.shutdown = Mock()
                app._started_at_monotonic = 0
                app._last_client_activity_at = 0

                retired = app._maybe_retire_for_idle(now_monotonic=700)

        self.assertTrue(retired)
        self.assertEqual(app._status()["daemon_state"], "shutting_down")
        app._server.shutdown.assert_called_once()

    def test_idle_retirement_waits_for_pending_question(self) -> None:
        service = FakeMessageService()
        shared_runtime = FakeSharedRuntime(pending=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=shared_runtime),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._server.shutdown = Mock()
                app._started_at_monotonic = 0
                app._last_client_activity_at = 0

                retired = app._maybe_retire_for_idle(now_monotonic=700)

        self.assertFalse(retired)
        self.assertEqual(app._status()["daemon_state"], "serving")
        app._server.shutdown.assert_not_called()

    def test_idle_retirement_waits_for_inflight_request_completion(self) -> None:
        service = FakeMessageService()
        shared_runtime = FakeSharedRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            with (
                patch("ask_user_via_feishu.daemon.app.build_message_service", return_value=service),
                patch("ask_user_via_feishu.daemon.app.build_event_processor", return_value=object()),
                patch("ask_user_via_feishu.daemon.app.FeishuSharedLongConnectionRuntime", return_value=shared_runtime),
            ):
                app = SharedLongConnDaemonApp(self._settings(), runtime_dir=runtime_dir)
                self.addCleanup(app._server.close)
                app._server.shutdown = Mock()
                app._started_at_monotonic = 0
                app._last_client_activity_at = 0
                app._in_flight_request_count = 1

                retired = app._maybe_retire_for_idle(now_monotonic=700)

        self.assertFalse(retired)
        self.assertEqual(app._status()["daemon_state"], "serving")
        app._server.shutdown.assert_not_called()

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
                        "client_id": "client_alpha",
                        "client_request_id": "request_alpha",
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
        self.assertEqual(call_kwargs["client_id"], "client_alpha")
        self.assertEqual(call_kwargs["client_request_id"], "request_alpha")
