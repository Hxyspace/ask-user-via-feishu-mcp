from __future__ import annotations

import asyncio
import inspect
import unittest
from typing import get_type_hints
from unittest.mock import patch

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.bootstrap import DaemonBootstrapError
from ask_user_via_feishu.ipc.client import DaemonTransportError
from ask_user_via_feishu.schemas import FeishuPostContent
from ask_user_via_feishu.server import _build_ask_user_options_card, _resolve_enabled_mcp_tools, create_server

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class FakeSendToolMessageService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def send_text(self, **kwargs):
        self.calls.append(("send_text", kwargs))
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}

    async def send_image(self, **kwargs):
        self.calls.append(("send_image", kwargs))
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}

    async def send_file(self, **kwargs):
        self.calls.append(("send_file", kwargs))
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}

    async def send_post(self, **kwargs):
        self.calls.append(("send_post", kwargs))
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}


class FakeDaemonClient:
    def __init__(self, *, result: dict[str, object] | None = None, error: BaseException | None = None) -> None:
        self.result = result or {
            "ok": True,
            "question_id": "ask_123",
            "status": "answered",
            "user_answer": "answer",
            "downloaded_paths": [],
        }
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def ask_and_wait(self, **kwargs):
        self.calls.append({"method": "ask_and_wait", **kwargs})
        if self.error is not None:
            raise self.error
        return dict(self.result)

    async def send_text_message(self, **kwargs):
        self.calls.append({"method": "send_text_message", **kwargs})
        if self.error is not None:
            raise self.error
        return {"ok": True}

    async def send_image_message(self, **kwargs):
        self.calls.append({"method": "send_image_message", **kwargs})
        if self.error is not None:
            raise self.error
        return {"ok": True}

    async def send_file_message(self, **kwargs):
        self.calls.append({"method": "send_file_message", **kwargs})
        if self.error is not None:
            raise self.error
        return {"ok": True}

    async def send_post_message(self, **kwargs):
        self.calls.append({"method": "send_post_message", **kwargs})
        if self.error is not None:
            raise self.error
        return {"ok": True}


class ServerTest(unittest.TestCase):
    def _settings(self, **overrides: str) -> Settings:
        env = {
            "APP_ID": "cli_123",
            "APP_SECRET": "secret_123",
            "OWNER_OPEN_ID": "ou_owner",
            "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
        }
        env.update(overrides)
        return Settings.from_env(env)

    def test_enabled_tools_default_to_reduced_surface(self) -> None:
        settings = self._settings()
        self.assertEqual(
            _resolve_enabled_mcp_tools(),
            {
                "ask_user_via_feishu",
                "send_file_message",
                "send_image_message",
                "send_post_message",
                "send_text_message",
            },
        )

    def test_ask_user_routes_through_daemon_client(self) -> None:
        settings = self._settings(
            ASK_TIMEOUT_SECONDS="321",
            ASK_REMINDER_MAX_ATTEMPTS="7",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_client = FakeDaemonClient()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=FakeSendToolMessageService()),
            patch("ask_user_via_feishu.server.ensure_daemon_running", return_value=object()) as ensure_mock,
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", return_value=fake_client),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=["是", "否"], uuid="req_123"))

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], "answer")
        ensure_mock.assert_called_once_with(settings)
        self.assertEqual(fake_client.calls[0]["question"], "还继续吗？")
        self.assertEqual(fake_client.calls[0]["choices"], ["是", "否"])
        self.assertEqual(fake_client.calls[0]["client_request_id"], "req_123")
        self.assertEqual(fake_client.calls[0]["receive_id_type"], "open_id")
        self.assertEqual(fake_client.calls[0]["receive_id"], "ou_owner")
        self.assertEqual(fake_client.calls[0]["wait_options"].timeout_seconds, 321)
        self.assertEqual(fake_client.calls[0]["wait_options"].reminder_max_attempts, 7)

    def test_ask_user_propagates_daemon_client_errors(self) -> None:
        settings = self._settings()
        fake_client = FakeDaemonClient(error=ValueError("daemon conflict"))

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=FakeSendToolMessageService()),
            patch("ask_user_via_feishu.server.ensure_daemon_running", return_value=object()),
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", return_value=fake_client),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            with self.assertRaisesRegex(ValueError, "daemon conflict"):
                asyncio.run(ask_tool(question="还继续吗？", choices=None))

    def test_send_text_tool_returns_minimal_public_result(self) -> None:
        settings = self._settings()
        fake_service = FakeSendToolMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch(
                "ask_user_via_feishu.server.ensure_daemon_running",
                side_effect=DaemonBootstrapError("daemon unavailable"),
            ),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            result = asyncio.run(send_text_tool(text="hello"))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(fake_service.calls[0][0], "send_text")

    def test_send_text_prefers_daemon_client_when_available(self) -> None:
        settings = self._settings()
        fake_service = FakeSendToolMessageService()
        fake_client = FakeDaemonClient()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.ensure_daemon_running", return_value=object()) as ensure_mock,
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", return_value=fake_client),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            result = asyncio.run(send_text_tool(text="hello", uuid="req_123"))

        self.assertEqual(result, {"ok": True})
        ensure_mock.assert_called_once_with(settings)
        self.assertEqual(fake_client.calls[0]["method"], "send_text_message")
        self.assertEqual(fake_client.calls[0]["text"], "hello")
        self.assertEqual(fake_client.calls[0]["uuid"], "req_123")
        self.assertEqual(fake_client.calls[0]["receive_id_type"], "open_id")
        self.assertEqual(fake_client.calls[0]["receive_id"], "ou_owner")
        self.assertEqual(fake_service.calls, [])

    def test_send_text_falls_back_to_direct_send_when_daemon_bootstrap_fails(self) -> None:
        settings = self._settings()
        fake_service = FakeSendToolMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch(
                "ask_user_via_feishu.server.ensure_daemon_running",
                side_effect=DaemonBootstrapError("daemon unavailable"),
            ),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            result = asyncio.run(send_text_tool(text="hello"))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(fake_service.calls[0][0], "send_text")
        self.assertEqual(fake_service.calls[0][1]["receive_id_type"], "open_id")
        self.assertEqual(fake_service.calls[0][1]["receive_id"], "ou_owner")

    def test_send_text_falls_back_to_direct_send_when_daemon_transport_fails(self) -> None:
        settings = self._settings()
        fake_service = FakeSendToolMessageService()
        fake_client = FakeDaemonClient(error=DaemonTransportError("daemon connection dropped"))

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.ensure_daemon_running", return_value=object()),
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", return_value=fake_client),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            result = asyncio.run(send_text_tool(text="hello"))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(fake_service.calls[0][0], "send_text")
        self.assertEqual(fake_service.calls[0][1]["receive_id_type"], "open_id")
        self.assertEqual(fake_service.calls[0][1]["receive_id"], "ou_owner")

    def test_create_server_registers_reduced_tools(self) -> None:
        server = create_server(self._settings())
        tools = set(server._tool_manager._tools.keys())
        self.assertEqual(
            tools,
            {
                "ask_user_via_feishu",
                "send_file_message",
                "send_image_message",
                "send_post_message",
                "send_text_message",
            },
        )

    def test_tool_signatures_expose_refined_schema(self) -> None:
        server = create_server(self._settings())

        send_text_sig = inspect.signature(server._tool_manager._tools["send_text_message"].fn)
        self.assertEqual(list(send_text_sig.parameters.keys()), ["text", "uuid"])

        send_image_sig = inspect.signature(server._tool_manager._tools["send_image_message"].fn)
        self.assertEqual(list(send_image_sig.parameters.keys()), ["image_path", "uuid"])

        send_file_sig = inspect.signature(server._tool_manager._tools["send_file_message"].fn)
        self.assertEqual(
            list(send_file_sig.parameters.keys()),
            ["file_path", "file_type", "file_name", "duration_ms", "uuid"],
        )
        self.assertEqual(send_file_sig.parameters["file_type"].default, "stream")

        send_post_fn = server._tool_manager._tools["send_post_message"].fn
        send_post_sig = inspect.signature(send_post_fn)
        self.assertEqual(
            list(send_post_sig.parameters.keys()),
            ["title", "content", "locale", "uuid"],
        )
        self.assertEqual(get_type_hints(send_post_fn)["content"], FeishuPostContent)

        ask_sig = inspect.signature(server._tool_manager._tools["ask_user_via_feishu"].fn)
        self.assertEqual(
            list(ask_sig.parameters.keys()),
            ["question", "choices", "uuid"],
        )

    def test_long_choice_card_summary_uses_real_newlines(self) -> None:
        card = _build_ask_user_options_card(
            question_id="ask_123",
            question="请选择",
            choices=["这是一个特别特别长的选项A", "这是一个特别特别长的选项B"],
        )

        summary_block = next(
            element
            for element in card["elements"]
            if element.get("tag") == "markdown" and str(element.get("content") or "").startswith("**选项说明**")
        )
        self.assertEqual(
            summary_block["content"],
            "**选项说明**\n1. 这是一个特别特别长的选项A\n2. 这是一个特别特别长的选项B",
        )
