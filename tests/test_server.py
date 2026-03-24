from __future__ import annotations

import asyncio
import inspect
import unittest
from typing import get_type_hints
from unittest.mock import patch

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.shared_longconn import PendingQuestionTimeout
from ask_user_via_feishu.schemas import FeishuPostContent
from ask_user_via_feishu.server import (
    ASK_AUTO_RECALL_ANSWER,
    ASK_RESOURCES_ONLY_ANSWER,
    _build_ask_user_options_card,
    _resolve_enabled_mcp_tools,
    create_server,
)
from ask_user_via_feishu.errors import MessageValidationError

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class FakeTimeoutMessageService:
    def __init__(self) -> None:
        self.sent_interactive: list[dict] = []
        self.sent_texts: list[dict] = []
        self.updated_cards: list[dict] = []
        self.created_reactions: list[dict] = []
        self.deleted_reactions: list[dict] = []

    async def send_interactive(self, **kwargs):
        self.sent_interactive.append(kwargs)
        return {
            "ok": True,
            "message_id": "om_question",
            "receive_id": "ou_owner",
        }

    async def send_text(self, **kwargs):
        self.sent_texts.append(kwargs)
        return {"ok": True}

    async def update_interactive(self, **kwargs):
        self.updated_cards.append(kwargs)
        return {"ok": True}

    async def download_reply_resources(self, **kwargs):
        return []

    async def create_reaction(self, **kwargs):
        self.created_reactions.append(kwargs)
        return {
            "ok": True,
            "message_id": kwargs["message_id"],
            "reaction_id": "reaction_123",
            "emoji_type": kwargs.get("emoji_type") or "Typing",
        }

    async def delete_reaction(self, **kwargs):
        self.deleted_reactions.append(kwargs)
        return {
            "ok": True,
            "message_id": kwargs["message_id"],
            "reaction_id": kwargs["reaction_id"],
            "deleted": True,
        }


class FakeSendToolMessageService:
    async def send_text(self, **kwargs):
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}

    async def send_image(self, **kwargs):
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}

    async def send_file(self, **kwargs):
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}

    async def send_post(self, **kwargs):
        return {"ok": True, "message_id": "om_123", "receive_id": "ou_owner"}


class FakeFileOnlyRuntime:
    def __init__(self, settings, event_processor) -> None:
        self.settings = settings
        self.event_processor = event_processor

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        return None

    def wait_for_question(self, question_id: str, timeout_seconds: int):
        return {
            "message_id": "om_reply",
            "chat_id": "oc_p2p",
            "message_type": "file",
            "text": "",
            "message_content": {"file_key": "file_123", "file_name": "report.pdf"},
            "resource_refs": [{"kind": "file", "message_id": "om_reply", "file_key": "file_123", "file_name": "report.pdf"}],
            "callback_response": {},
        }


class FakeDownloadMessageService(FakeTimeoutMessageService):
    async def download_reply_resources(self, **kwargs):
        return ["/tmp/receive_files/ask_123/report.pdf"]


class FakeTimeoutRuntime:
    last_instance = None

    def __init__(self, settings, event_processor) -> None:
        self.settings = settings
        self.event_processor = event_processor
        self.registered_questions: list[dict] = []
        self.unregistered_question_ids: list[str] = []
        type(self).last_instance = self

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        self.registered_questions.append(kwargs)
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        self.unregistered_question_ids.append(question_id)

    def wait_for_question(self, question_id: str, timeout_seconds: int):
        raise PendingQuestionTimeout(f"Timed out after {timeout_seconds} seconds")


class FakeAnsweredRuntime:
    def __init__(self, settings, event_processor) -> None:
        self.settings = settings
        self.event_processor = event_processor
        self.registered_questions: list[dict] = []
        self.unregistered_question_ids: list[str] = []

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        self.registered_questions.append(kwargs)
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        self.unregistered_question_ids.append(question_id)

    def wait_for_question(self, question_id: str, timeout_seconds: int):
        return {
            "message_id": "om_reply",
            "chat_id": "oc_p2p",
            "message_type": "text",
            "text": "answer",
            "message_content": {"text": "answer"},
            "callback_response": {},
        }


class FakeRejectPendingRuntime:
    def __init__(self, settings, event_processor) -> None:
        self.settings = settings
        self.event_processor = event_processor

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        raise ValueError(
            "A pending Feishu question for this open_id already exists. Concurrent questions for the same user are not supported."
        )

    def unregister_pending_question(self, question_id: str) -> None:
        return None


class FakeRollbackRuntime:
    last_instance = None

    def __init__(self, settings, event_processor) -> None:
        self.settings = settings
        self.event_processor = event_processor
        self.registered_questions: list[dict] = []
        self.unregistered_question_ids: list[str] = []
        type(self).last_instance = self

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        self.registered_questions.append(kwargs)
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        self.unregistered_question_ids.append(question_id)


class FakeFailingSendMessageService(FakeTimeoutMessageService):
    async def send_interactive(self, **kwargs):
        self.sent_interactive.append(kwargs)
        raise MessageValidationError("send failed")


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

    def test_ask_user_timeout_returns_auto_recall_after_reminder_limit(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="1",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeTimeoutRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None))

        self.assertEqual(
            result,
            {
                "ok": True,
                "question_id": result["question_id"],
                "status": "answered",
                "user_answer": ASK_AUTO_RECALL_ANSWER,
                "downloaded_paths": [],
            },
        )
        self.assertEqual(len(fake_service.sent_texts), 1)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_ask_user_timeout_with_zero_reminders_exits_on_first_timeout(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="0",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeTimeoutRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None))

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], ASK_AUTO_RECALL_ANSWER)
        self.assertEqual(len(fake_service.sent_texts), 0)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_ask_user_timeout_with_negative_reminders_matches_zero_behavior(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="-1",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeTimeoutRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None))

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], ASK_AUTO_RECALL_ANSWER)
        self.assertEqual(len(fake_service.sent_texts), 0)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_ask_user_timeout_keeps_same_pending_registration_between_retries(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="2",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeTimeoutRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None))
            runtime = FakeTimeoutRuntime.last_instance

        self.assertEqual(result["status"], "answered")
        self.assertIsNotNone(runtime)
        self.assertEqual(len(fake_service.sent_texts), 2)
        self.assertEqual(len(runtime.registered_questions), 1)
        self.assertEqual(
            runtime.unregistered_question_ids,
            [runtime.registered_questions[0]["question_id"]],
        )

    def test_ask_user_timeout_with_empty_default_returns_plain_timeout(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="1",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="",
        )
        fake_service = FakeTimeoutMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeTimeoutRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None))

        self.assertEqual(
            result,
            {
                "ok": True,
                "question_id": result["question_id"],
                "status": "timeout",
                "user_answer": "",
                "downloaded_paths": [],
            },
        )
        self.assertEqual(len(fake_service.sent_texts), 1)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_ask_user_fails_before_sending_when_pending_slot_is_unavailable(self) -> None:
        settings = self._settings()
        fake_service = FakeTimeoutMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeRejectPendingRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            with self.assertRaises(ValueError):
                asyncio.run(ask_tool(question="还继续吗？", choices=None))

        self.assertEqual(fake_service.sent_interactive, [])

    def test_ask_user_unregisters_reserved_pending_when_send_fails(self) -> None:
        settings = self._settings()
        fake_service = FakeFailingSendMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeRollbackRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            with self.assertRaises(MessageValidationError):
                asyncio.run(ask_tool(question="还继续吗？", choices=None))

        runtime = FakeRollbackRuntime.last_instance
        self.assertIsNotNone(runtime)
        self.assertEqual(len(runtime.registered_questions), 1)
        self.assertEqual(
            runtime.unregistered_question_ids,
            [runtime.registered_questions[0]["question_id"]],
        )
        self.assertEqual(len(fake_service.sent_interactive), 1)

    def test_ask_user_returns_answer_when_card_update_fails(self) -> None:
        settings = self._settings(REACTION_ENABLED="true")
        fake_service = FakeTimeoutMessageService()

        async def broken_update_interactive(**kwargs):
            raise MessageValidationError("card update failed")

        fake_service.update_interactive = broken_update_interactive  # type: ignore[method-assign]

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeAnsweredRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="还继续吗？", choices=None))

        self.assertEqual(
            result,
            {
                "ok": True,
                "question_id": result["question_id"],
                "status": "answered",
                "user_answer": "answer",
                "downloaded_paths": [],
            },
        )
        self.assertEqual(
            fake_service.created_reactions,
            [{"message_id": "om_reply", "emoji_type": "Typing"}],
        )
        self.assertEqual(fake_service.deleted_reactions, [])

    def test_ask_user_returns_file_paths_and_reask_hint_for_file_only_reply(self) -> None:
        settings = self._settings()
        fake_service = FakeDownloadMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeFileOnlyRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            result = asyncio.run(ask_tool(question="把资料发我", choices=None))

        self.assertEqual(
            result,
            {
                "ok": True,
                "question_id": result["question_id"],
                "status": "answered",
                "user_answer": ASK_RESOURCES_ONLY_ANSWER,
                "downloaded_paths": ["/tmp/receive_files/ask_123/report.pdf"],
            },
        )

    def test_next_ask_clears_previous_processing_reaction(self) -> None:
        settings = self._settings(REACTION_ENABLED="true")
        fake_service = FakeTimeoutMessageService()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=fake_service),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
            patch("ask_user_via_feishu.server.FeishuSharedLongConnectionRuntime", FakeAnsweredRuntime),
        ):
            server = create_server(settings)
            ask_tool = server._tool_manager._tools["ask_user_via_feishu"].fn
            first_result = asyncio.run(ask_tool(question="第一问", choices=None))
            second_result = asyncio.run(ask_tool(question="第二问", choices=None))

        self.assertEqual(first_result["status"], "answered")
        self.assertEqual(second_result["status"], "answered")
        self.assertEqual(
            fake_service.created_reactions,
            [
                {"message_id": "om_reply", "emoji_type": "Typing"},
                {"message_id": "om_reply", "emoji_type": "Typing"},
            ],
        )
        self.assertEqual(
            fake_service.deleted_reactions,
            [{"message_id": "om_reply", "reaction_id": "reaction_123"}],
        )

    def test_send_text_tool_returns_minimal_public_result(self) -> None:
        settings = self._settings()

        with (
            patch("ask_user_via_feishu.server.build_message_service", return_value=FakeSendToolMessageService()),
            patch("ask_user_via_feishu.server.build_event_processor", return_value=object()),
        ):
            server = create_server(settings)
            send_text_tool = server._tool_manager._tools["send_text_message"].fn
            result = asyncio.run(send_text_tool(text="hello"))

        self.assertEqual(result, {"ok": True})

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
