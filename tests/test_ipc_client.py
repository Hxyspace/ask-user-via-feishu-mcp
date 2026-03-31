from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from ask_user_via_feishu.ask_runtime import AskWaitOptions
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.bootstrap import DaemonConnectionInfo
from ask_user_via_feishu.daemon.server import SharedLongConnDaemonServer
from ask_user_via_feishu.ipc.client import DaemonAskRetryableError, DaemonTransportError, SharedLongConnDaemonClient


class SharedLongConnDaemonClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_route_treats_daemon_not_serving_as_transport_error(self) -> None:
        settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                send_handlers={"/v1/send_text_message": lambda payload: {"ok": True}},
                status_provider=lambda: {"daemon_state": "retiring_idle"},
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)
            client = SharedLongConnDaemonClient(
                DaemonConnectionInfo(runtime_dir=Path(tmpdir), metadata=daemon.metadata, token=daemon.token)
            )

            with self.assertRaisesRegex(DaemonTransportError, "Shared daemon is not accepting new requests"):
                await client.send_text_message(
                    text="hello",
                    uuid=None,
                    receive_id_type="open_id",
                    receive_id="ou_demo",
                )

    async def test_ask_route_treats_daemon_not_serving_as_retryable_before_send(self) -> None:
        settings = Settings(app_id="cli_demo", app_secret="secret_demo", owner_open_id="ou_demo")
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = SharedLongConnDaemonServer(
                settings,
                Path(tmpdir),
                ask_handler=lambda payload: {"ok": True, "status": "answered", "user_answer": "done"},
                status_provider=lambda: {"daemon_state": "retiring_idle"},
            )
            thread = daemon.start_background()
            self.addCleanup(daemon.shutdown)
            self.addCleanup(thread.join, 1)
            client = SharedLongConnDaemonClient(
                DaemonConnectionInfo(runtime_dir=Path(tmpdir), metadata=daemon.metadata, token=daemon.token)
            )

            with self.assertRaisesRegex(DaemonAskRetryableError, "Shared daemon is not accepting new requests") as error:
                await client.ask_and_wait(
                    question="继续吗？",
                    choices=["是", "否"],
                    uuid=None,
                    receive_id_type="open_id",
                    receive_id="ou_demo",
                    wait_options=AskWaitOptions(
                        timeout_seconds=60,
                        reminder_max_attempts=0,
                        timeout_reminder_text="",
                        timeout_default_answer="",
                    ),
                )

        self.assertEqual(error.exception.retry_stage, "before_send")
