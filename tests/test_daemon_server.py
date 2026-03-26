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
                },
            )

            self.assertEqual(captured_payload["question"], "继续吗？")
            self.assertEqual(captured_payload["choices"], ["是", "否"])
            self.assertEqual(captured_payload["receive_id_type"], "open_id")
            self.assertEqual(captured_payload["receive_id"], "ou_demo")
            self.assertEqual(response["status"], "answered")
            self.assertEqual(response["user_answer"], "done")
            self.assertEqual(response["daemon_epoch"], daemon.metadata.daemon_epoch)

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

    def _fetch_json(self, url: str, *, token: str) -> dict[str, object]:
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=1) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, url: str, *, token: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=1) as response:
            return json.loads(response.read().decode("utf-8"))
