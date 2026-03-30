from __future__ import annotations

import asyncio
import inspect
import unittest
from typing import get_type_hints
from unittest.mock import patch

from ask_user_via_feishu.ask_runtime import ASK_LOCAL_FALLBACK_ANSWER
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.bootstrap import DaemonBootstrapError
from ask_user_via_feishu.ipc.client import DaemonAskRetryableError, DaemonTransportError
from ask_user_via_feishu.schemas import FeishuPostContent
from ask_user_via_feishu.server import (
    _build_ask_user_options_card,
    _build_target_selection_card,
    _resolve_enabled_mcp_tools,
    create_server,
)

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class FakeSendToolMessageService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.owner_chats = [{"chat_id": "oc_existing", "name": "existing-chat"}]
        self.created_chat_id = "oc_created"

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

    async def list_owner_chats(self):
        self.calls.append(("list_owner_chats", {}))
        return list(self.owner_chats)

    async def create_owner_chat(self, *, name: str, uuid: str | None = None):
        self.calls.append(("create_owner_chat", {"name": name, "uuid": uuid}))
        return {
            "ok": True,
            "chat_id": self.created_chat_id,
            "name": name,
        }


class FakeDaemonClient:
    def __init__(
        self,
        *,
        result: dict[str, object] | None = None,
        results: list[dict[str, object]] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or {
            "ok": True,
            "question_id": "ask_123",
            "status": "answered",
            "user_answer": "answer",
            "downloaded_paths": [],
        }
        self.results = list(results or [])
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def ask_and_wait(self, **kwargs):
        self.calls.append({"method": "ask_and_wait", **kwargs})
        if self.error is not None:
            raise self.error
        if self.results:
            return dict(self.results.pop(0))
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
            CHAT_ID="oc_configured",
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
        self.assertEqual(fake_client.calls[0]["receive_id_type"], "chat_id")
        self.assertEqual(fake_client.calls[0]["receive_id"], "oc_configured")
        self.assertEqual(fake_client.calls[0]["allowed_actor_open_id"], "ou_owner")
        self.assertEqual(fake_client.calls[0]["wait_options"].timeout_seconds, 321)
        self.assertEqual(fake_client.calls[0]["wait_options"].reminder_max_attempts, 7)

    def test_ask_user_uses_configured_chat_id_without_bootstrap(self) -> None:
        settings = self._settings(CHAT_ID="oc_configured")
        fake_client = FakeDaemonClient()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=FakeSendToolMessageService()),
            patch("ask_user_via_feishu.server.ensure_daemon_running", return_value=object()) as ensure_mock,
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", return_value=fake_client),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None))

        self.assertEqual(result["status"], "answered")
        self.assertEqual(fake_client.calls[0]["receive_id_type"], "chat_id")
        self.assertEqual(fake_client.calls[0]["receive_id"], "oc_configured")
        self.assertEqual(fake_client.calls[0]["allowed_actor_open_id"], "ou_owner")
        ensure_mock.assert_called_once_with(settings)

    def test_ask_user_propagates_daemon_client_errors(self) -> None:
        settings = self._settings(CHAT_ID="oc_configured")
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

    def test_ask_user_retries_once_on_retryable_daemon_failure(self) -> None:
        settings = self._settings(CHAT_ID="oc_configured")
        first_client = FakeDaemonClient(error=DaemonAskRetryableError("retry", retry_stage="after_send"))
        second_client = FakeDaemonClient()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=FakeSendToolMessageService()),
            patch("ask_user_via_feishu.server.ensure_daemon_running", side_effect=[object(), object()]) as ensure_mock,
            patch(
                "ask_user_via_feishu.server.SharedLongConnDaemonClient",
                side_effect=[first_client, second_client],
            ),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=["是", "否"], uuid="req_123"))

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], "answer")
        self.assertEqual(ensure_mock.call_count, 2)
        self.assertEqual(first_client.calls[0]["uuid"], "req_123")
        self.assertNotEqual(second_client.calls[0]["uuid"], "req_123")
        self.assertIn("_retry_", second_client.calls[0]["uuid"])

    def test_ask_user_returns_local_fallback_when_retry_cannot_reach_fresh_daemon(self) -> None:
        settings = self._settings(CHAT_ID="oc_configured")
        first_client = FakeDaemonClient(error=DaemonAskRetryableError("retry", retry_stage="after_send"))

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=FakeSendToolMessageService()),
            patch(
                "ask_user_via_feishu.server.ensure_daemon_running",
                side_effect=[object(), DaemonBootstrapError("daemon unavailable")],
            ),
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", side_effect=[first_client]),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None, uuid="req_123"))

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], ASK_LOCAL_FALLBACK_ANSWER)

    def test_send_text_tool_returns_minimal_public_result(self) -> None:
        settings = self._settings(CHAT_ID="oc_configured")
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
        fake_client = FakeDaemonClient(
            results=[
                {
                    "ok": True,
                    "question_id": "select_123",
                    "status": "answered",
                    "user_answer": "current_conversation",
                    "downloaded_paths": [],
                    "card_action": {
                        "action": "feishu_select_chat_target",
                        "value": {
                            "action": "feishu_select_chat_target",
                            "question_id": "select_123",
                            "selection_kind": "current_conversation",
                        },
                    },
                }
            ]
        )

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.ensure_daemon_running", return_value=object()) as ensure_mock,
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", return_value=fake_client),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            result = asyncio.run(send_text_tool(text="hello", uuid="req_123"))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(ensure_mock.call_count, 2)
        self.assertEqual(fake_client.calls[0]["method"], "ask_and_wait")
        self.assertEqual(fake_client.calls[1]["method"], "send_text_message")
        self.assertEqual(fake_client.calls[1]["text"], "hello")
        self.assertEqual(fake_client.calls[1]["uuid"], "req_123")
        self.assertEqual(fake_client.calls[1]["receive_id_type"], "open_id")
        self.assertEqual(fake_client.calls[1]["receive_id"], "ou_owner")
        self.assertEqual(fake_service.calls, [("list_owner_chats", {})])

    def test_send_text_bootstraps_new_chat_and_reuses_selection(self) -> None:
        settings = self._settings()
        fake_service = FakeSendToolMessageService()
        fake_service.created_chat_id = "oc_new_chat"
        fake_client = FakeDaemonClient(
            results=[
                {
                    "ok": True,
                    "question_id": "select_123",
                    "status": "answered",
                    "user_answer": "project-alpha",
                    "downloaded_paths": [],
                    "card_action": {
                        "action": "feishu_select_chat_target",
                        "value": {
                            "action": "feishu_select_chat_target",
                            "question_id": "select_123",
                            "selection_kind": "new_chat",
                        },
                    },
                }
            ]
        )

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.ensure_daemon_running", return_value=object()) as ensure_mock,
            patch("ask_user_via_feishu.server.SharedLongConnDaemonClient", return_value=fake_client),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            first = asyncio.run(send_text_tool(text="hello"))
            second = asyncio.run(send_text_tool(text="again"))

        self.assertEqual(first, {"ok": True})
        self.assertEqual(second, {"ok": True})
        self.assertEqual([call["method"] for call in fake_client.calls], ["ask_and_wait", "send_text_message", "send_text_message"])
        self.assertEqual(fake_client.calls[1]["receive_id_type"], "chat_id")
        self.assertEqual(fake_client.calls[1]["receive_id"], "oc_new_chat")
        self.assertEqual(fake_client.calls[2]["receive_id_type"], "chat_id")
        self.assertEqual(fake_client.calls[2]["receive_id"], "oc_new_chat")
        self.assertIn(("list_owner_chats", {}), fake_service.calls)
        self.assertIn(("create_owner_chat", {"name": "project-alpha", "uuid": None}), fake_service.calls)
        self.assertEqual(ensure_mock.call_count, 3)

    def test_send_text_falls_back_to_direct_send_when_daemon_bootstrap_fails(self) -> None:
        settings = self._settings(CHAT_ID="oc_configured")
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
        self.assertEqual(fake_service.calls[0][1]["receive_id_type"], "chat_id")
        self.assertEqual(fake_service.calls[0][1]["receive_id"], "oc_configured")

    def test_send_text_falls_back_to_direct_send_when_daemon_transport_fails(self) -> None:
        settings = self._settings(CHAT_ID="oc_configured")
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
        self.assertEqual(fake_service.calls[0][1]["receive_id_type"], "chat_id")
        self.assertEqual(fake_service.calls[0][1]["receive_id"], "oc_configured")

    def test_send_text_requires_bootstrap_when_no_target_and_daemon_unavailable(self) -> None:
        settings = self._settings()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=FakeSendToolMessageService()),
            patch(
                "ask_user_via_feishu.server.ensure_daemon_running",
                side_effect=DaemonBootstrapError("daemon unavailable"),
            ),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            with self.assertRaisesRegex(DaemonBootstrapError, "daemon unavailable"):
                asyncio.run(send_text_tool(text="hello"))

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

    def test_target_selection_card_uses_legacy_1p0_layout(self) -> None:
        card = _build_target_selection_card(
            question_id="select_123",
            candidate_chats=[
                {"chat_id": "oc_existing", "name": "existing-chat"},
                {"chat_id": "oc_second", "name": "another-chat"},
            ],
        )

        action_blocks = [element for element in card["elements"] if element.get("tag") == "action"]
        form_block = next(element for element in card["elements"] if element.get("tag") == "form")
        note_block = next(element for element in card["elements"] if element.get("tag") == "note")
        input_block = next(element for element in form_block["elements"] if element.get("tag") == "input")
        submit_button = next(element for element in form_block["elements"] if element.get("tag") == "button")

        self.assertNotIn("schema", card)
        self.assertNotIn("layout", action_blocks[0])
        self.assertEqual(len(action_blocks), 2)
        self.assertEqual(action_blocks[0]["actions"][0]["text"]["content"], "当前会话")
        self.assertEqual(action_blocks[1]["actions"][0]["text"]["content"], "existing-chat")
        self.assertEqual(action_blocks[1]["actions"][1]["text"]["content"], "another-chat")
        self.assertEqual(action_blocks[1]["actions"][0]["value"]["chat_id"], "oc_existing")
        self.assertEqual(action_blocks[1]["actions"][1]["value"]["chat_id"], "oc_second")
        self.assertEqual(form_block["name"], "select_target_new_chat_form")
        self.assertEqual(input_block["name"], "new_chat_name")
        self.assertEqual(submit_button["action_type"], "form_submit")
        self.assertEqual(submit_button["value"]["selection_kind"], "new_chat")
        self.assertEqual(submit_button["text"]["content"], "提交")
        self.assertIn("只保存在当前 MCP 进程内存中", note_block["elements"][0]["content"])
