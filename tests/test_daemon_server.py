from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.runtime import load_metadata, load_token
from ask_user_via_feishu.daemon.server import SharedLongConnDaemonServer
from ask_user_via_feishu.errors import RetryableAskError


class DaemonServerTest(unittest.TestCase):
    def test_health_and_status_require_auth_and_return_metadata(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(settings, Path(tmpdir))
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with self.assertRaises(HTTPError) as health_error:
                urlopen(f"http://127.0.0.1:{daemon.metadata.port}/v1/health", timeout=1)
            self.assertEqual(health_error.exception.code, 401)

            with self.assertRaises(HTTPError) as wrong_token_error:
                self._fetch_json(
                    f"http://127.0.0.1:{daemon.metadata.port}/v1/health",
                    token=f"{daemon.token[:-1]}x",
                )
            self.assertEqual(wrong_token_error.exception.code, 401)

            health = self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/health",
                token=daemon.token,
            )
            status = self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/status",
                token=daemon.token,
            )

            self.assertTrue(health["ready"])
            self.assertEqual(health["daemon_epoch"], daemon.metadata.daemon_epoch)
            self.assertEqual(status["identity"]["app_id"], "cli_demo")
            self.assertFalse(status["pending_ask"])
            self.assertEqual(status["active_ask_count"], 0)
            self.assertEqual(status["queued_ask_count"], 0)
            self.assertEqual(status["queues_by_target"], [])
            self.assertEqual(status["queue_exempt_question_ids"], [])

    def test_ask_and_wait_route_returns_handler_result(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        captured_payload: dict[str, object] = {}

        def ask_handler(payload: dict[str, object]) -> dict[str, object]:
            captured_payload.update(payload)
            return {
                "ok": True,
                "question_id": "ask_123",
                "status": "answered",
                "user_answer": "done",
                "downloaded_paths": [],
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(settings, Path(tmpdir), ask_handler=ask_handler)
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            response = self._post_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/ask_and_wait",
                token=daemon.token,
                payload={
                    "question": "继续吗？",
                    "choices": ["是", "否"],
                    "receive_id_type": "open_id",
                    "receive_id": "ou_demo",
                    "client_id": "client_alpha",
                    "client_request_id": "request_alpha",
                },
            )

            self.assertEqual(captured_payload["question"], "继续吗？")
            self.assertEqual(captured_payload["choices"], ["是", "否"])
            self.assertEqual(captured_payload["receive_id_type"], "open_id")
            self.assertEqual(captured_payload["receive_id"], "ou_demo")
            self.assertEqual(captured_payload["client_id"], "client_alpha")
            self.assertEqual(captured_payload["client_request_id"], "request_alpha")
            self.assertEqual(response["status"], "answered")
            self.assertEqual(response["user_answer"], "done")
            self.assertEqual(response["daemon_epoch"], daemon.metadata.daemon_epoch)

    def test_ask_and_wait_route_returns_retryable_error_code(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )

        def ask_handler(payload: dict[str, object]) -> dict[str, object]:
            raise RetryableAskError("retry me", retry_stage="after_send")

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(settings, Path(tmpdir), ask_handler=ask_handler)
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with self.assertRaises(HTTPError) as error:
                self._post_json(
                    f"http://127.0.0.1:{daemon.metadata.port}/v1/ask_and_wait",
                    token=daemon.token,
                    payload={
                        "question": "继续吗？",
                        "choices": [],
                        "receive_id_type": "open_id",
                        "receive_id": "ou_demo",
                    },
                )

            response = json.loads(error.exception.read().decode("utf-8"))

        self.assertEqual(error.exception.code, 503)
        self.assertEqual(response["error_code"], "ask_retryable_after_send")

    def test_mutating_routes_reject_when_daemon_is_not_serving(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )

        def send_text_handler(payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "message_id": "om_123", "receive_id": "ou_demo"}

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                send_handlers={"/v1/send_text_message": send_text_handler},
                status_provider=lambda: {"daemon_state": "retiring_idle"},
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            with self.assertRaises(HTTPError) as error:
                self._post_json(
                    f"http://127.0.0.1:{daemon.metadata.port}/v1/send_text_message",
                    token=daemon.token,
                    payload={
                        "text": "hello",
                        "receive_id_type": "open_id",
                        "receive_id": "ou_demo",
                    },
                )

            response = json.loads(error.exception.read().decode("utf-8"))

        self.assertEqual(error.exception.code, 503)
        self.assertEqual(response["error_code"], "daemon_not_serving")

    def test_exit_route_bypasses_serving_gate_and_returns_handler_result(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        captured_payload: dict[str, object] = {}

        def exit_handler(payload: dict[str, object]) -> dict[str, object]:
            captured_payload.update(payload)
            return {
                "ok": True,
                "shutdown_requested": True,
                "daemon_state": "retiring_manual",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                exit_handler=exit_handler,
                status_provider=lambda: {"daemon_state": "retiring_idle"},
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            response = self._post_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/exit",
                token=daemon.token,
                payload={
                    "reason": "version_mismatch",
                    "requested_by_version": "999.0.0",
                },
            )

        self.assertEqual(captured_payload["reason"], "version_mismatch")
        self.assertEqual(captured_payload["requested_by_version"], "999.0.0")
        self.assertTrue(response["shutdown_requested"])
        self.assertEqual(response["daemon_state"], "retiring_manual")
        self.assertEqual(response["daemon_epoch"], daemon.metadata.daemon_epoch)

    def test_health_and_status_reflect_terminal_daemon_state(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                status_provider=lambda: {
                    "daemon_state": "terminal_failed",
                    "failure_reason": "ws failed",
                    "long_connection_state": "failed",
                    "active_ask_count": 1,
                    "queued_ask_count": 1,
                    "queues_by_target": [
                        {
                            "delivery_key": "chat_id:oc_demo",
                            "receive_id_type": "chat_id",
                            "receive_id": "oc_demo",
                            "active_question_id": "ask_123",
                            "active_client_id": "client_alpha",
                            "active_client_request_id": "request_alpha",
                            "queued_question_ids": ["ask_456"],
                            "queued_client_ids": ["client_beta"],
                            "queued_client_request_ids": ["request_beta"],
                        }
                    ],
                    "queue_exempt_question_ids": ["select_target_123"],
                },
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            health = self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/health",
                token=daemon.token,
            )
            status = self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/status",
                token=daemon.token,
            )

        self.assertFalse(health["ready"])
        self.assertEqual(health["daemon_state"], "terminal_failed")
        self.assertEqual(status["daemon_state"], "terminal_failed")
        self.assertEqual(status["failure_reason"], "ws failed")
        self.assertEqual(status["active_ask_count"], 1)
        self.assertEqual(status["queued_ask_count"], 1)
        self.assertEqual(status["queues_by_target"][0]["active_client_id"], "client_alpha")
        self.assertEqual(status["queue_exempt_question_ids"], ["select_target_123"])

    def test_send_text_route_returns_handler_result(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        captured_payload: dict[str, object] = {}

        def send_text_handler(payload: dict[str, object]) -> dict[str, object]:
            captured_payload.update(payload)
            return {
                "ok": True,
                "message_id": "om_123",
                "receive_id": "ou_demo",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                send_handlers={"/v1/send_text_message": send_text_handler},
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            response = self._post_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/send_text_message",
                token=daemon.token,
                payload={
                    "text": "hello",
                    "uuid": "req_123",
                    "receive_id_type": "open_id",
                    "receive_id": "ou_demo",
                },
            )

            self.assertEqual(captured_payload["text"], "hello")
            self.assertEqual(captured_payload["uuid"], "req_123")
            self.assertEqual(captured_payload["receive_id_type"], "open_id")
            self.assertEqual(captured_payload["receive_id"], "ou_demo")
            self.assertTrue(response["ok"])
            self.assertEqual(response["daemon_epoch"], daemon.metadata.daemon_epoch)

    def test_authorized_requests_notify_request_callbacks(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        started: list[str] = []
        finished: list[str] = []

        def send_text_handler(payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "message_id": "om_123", "receive_id": "ou_demo"}

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                send_handlers={"/v1/send_text_message": send_text_handler},
                on_request_started=started.append,
                on_request_finished=finished.append,
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/health",
                token=daemon.token,
            )
            self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/status",
                token=daemon.token,
            )
            self._post_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/send_text_message",
                token=daemon.token,
                payload={
                    "text": "hello",
                    "receive_id_type": "open_id",
                    "receive_id": "ou_demo",
                },
            )

            with self.assertRaises(HTTPError):
                urlopen(f"http://127.0.0.1:{daemon.metadata.port}/v1/health", timeout=1)

        self.assertEqual(started, ["/v1/health", "/v1/status", "/v1/send_text_message"])
        self.assertEqual(finished, started)

    def test_bootstrap_probe_health_does_not_notify_request_callbacks(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        started: list[str] = []
        finished: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                on_request_started=started.append,
                on_request_finished=finished.append,
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/health",
                token=daemon.token,
                extra_headers={"X-Daemon-Probe": "bootstrap"},
            )

        self.assertEqual(started, [])
        self.assertEqual(finished, [])

    def test_startup_version_probe_health_does_not_notify_request_callbacks(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        started: list[str] = []
        finished: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                on_request_started=started.append,
                on_request_finished=finished.append,
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            self._fetch_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/health",
                token=daemon.token,
                extra_headers={"X-Daemon-Probe": "startup-version-check"},
            )

        self.assertEqual(started, [])
        self.assertEqual(finished, [])

    def test_bootstrap_probe_header_does_not_suppress_post_activity_tracking(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        started: list[str] = []
        finished: list[str] = []

        def send_text_handler(payload: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "message_id": "om_123", "receive_id": "ou_demo"}

        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                send_handlers={"/v1/send_text_message": send_text_handler},
                on_request_started=started.append,
                on_request_finished=finished.append,
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)

            self._post_json(
                f"http://127.0.0.1:{daemon.metadata.port}/v1/send_text_message",
                token=daemon.token,
                payload={
                    "text": "hello",
                    "receive_id_type": "open_id",
                    "receive_id": "ou_demo",
                },
                extra_headers={"X-Daemon-Probe": "bootstrap"},
            )

        self.assertEqual(started, ["/v1/send_text_message"])
        self.assertEqual(finished, ["/v1/send_text_message"])

    def test_cleanup_only_removes_its_own_runtime_files(self) -> None:
        settings = Settings(
            app_id="cli_demo",
            app_secret="secret_demo",
            owner_open_id="ou_demo",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            first = SharedLongConnDaemonServer(settings, runtime_dir)
            second = SharedLongConnDaemonServer(settings, runtime_dir)
            self.addCleanup(first.close)
            self.addCleanup(second.close)

            first._publish_runtime_files()
            second._publish_runtime_files()
            first._cleanup_runtime_files()

            metadata = load_metadata(runtime_dir)
            token = load_token(runtime_dir)
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata.daemon_epoch, second.metadata.daemon_epoch)
            self.assertEqual(token, second.token)

    def _fetch_json(
        self,
        url: str,
        *,
        token: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {token}"}
        if extra_headers:
            headers.update(extra_headers)
        request = Request(url, headers=headers)
        with urlopen(request, timeout=1) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(
        self,
        url: str,
        *,
        token: str,
        payload: dict[str, object],
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=1) as response:
            return json.loads(response.read().decode("utf-8"))
