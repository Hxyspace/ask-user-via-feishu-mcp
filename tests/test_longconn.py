from __future__ import annotations

import json
import time
import unittest

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.longconn import FeishuLongConnectionSubscriber, LongConnectionSetupError
from ask_user_via_feishu.shared_longconn import (
    FeishuSharedLongConnectionRuntime,
    PendingQuestionAborted,
    PendingQuestionTimeout,
)

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class FakeEventProcessor:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def process_payload(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        return {"handled": True, "reply_sent": False}


class FakeBuilder:
    def __init__(self) -> None:
        self.registrations: list[tuple[str, object]] = []

    def register_p2_im_message_receive_v1(self, handler: object) -> "FakeBuilder":
        self.registrations.append(("register_p2_im_message_receive_v1", handler))
        return self

    def register_p2_card_action_trigger(self, handler: object) -> "FakeBuilder":
        self.registrations.append(("register_p2_card_action_trigger", handler))
        return self

    def register_p2_customized_event(self, event_type: str, handler: object) -> "FakeBuilder":
        self.registrations.append((f"register_p2_customized_event:{event_type}", handler))
        return self

    def build(self) -> "FakeBuilder":
        return self


class FakeEventDispatcherHandler:
    last_builder: FakeBuilder | None = None

    @classmethod
    def builder(cls, encrypt_key: str, verification_token: str) -> FakeBuilder:
        cls.last_builder = FakeBuilder()
        return cls.last_builder


class FakeWSClient:
    def __init__(self, app_id: str, app_secret: str, *, event_handler: object, log_level: object) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.event_handler = event_handler
        self.log_level = log_level

    def start(self) -> None:
        return None


class FakeFailingWSClient(FakeWSClient):
    def start(self) -> None:
        raise RuntimeError("ws failed")


class FakeSDK:
    EventDispatcherHandler = FakeEventDispatcherHandler

    class JSON:
        @staticmethod
        def marshal(data: object) -> str:
            return json.dumps(data, ensure_ascii=False)

    class ws:
        Client = FakeWSClient

    class LogLevel:
        INFO = "sdk-info"
        DEBUG = "sdk-debug"

    class event:
        class callback:
            class model:
                class p2_card_action_trigger:
                    class CallBackToast:
                        def __init__(self, d: dict[str, object] | None = None) -> None:
                            d = d or {}
                            self.type = d.get("type")
                            self.content = d.get("content")
                            self.i18n = d.get("i18n")

                    class CallBackCard:
                        def __init__(self, d: dict[str, object] | None = None) -> None:
                            d = d or {}
                            self.type = d.get("type")
                            self.data = d.get("data")

                    class P2CardActionTriggerResponse:
                        def __init__(self, d: dict[str, object] | None = None) -> None:
                            self.toast = None
                            self.card = None


class FakeFailingSDK(FakeSDK):
    class ws:
        Client = FakeFailingWSClient


class LongConnectionTest(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
                "OWNER_OPEN_ID": "ou_owner",
                "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
            }
        )

    def test_build_event_handler_registers_default_v2_events(self) -> None:
        subscriber = FeishuLongConnectionSubscriber(self._settings(), FakeEventProcessor(), sdk=FakeSDK)

        handler = subscriber.build_event_handler()

        self.assertIs(handler, FakeEventDispatcherHandler.last_builder)
        self.assertEqual(
            [name for name, _ in FakeEventDispatcherHandler.last_builder.registrations],
            [
                "register_p2_im_message_receive_v1",
                "register_p2_card_action_trigger",
            ],
        )

    def test_shared_runtime_captures_text_reply(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_123",
                    "create_time": "1000",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )
        result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(result["text"], "hello")
        self.assertEqual(result["message_id"], "om_reply")
        self.assertEqual(runtime._pending_by_question_id["ask_123"].status, "answered")

    def test_shared_runtime_captures_file_reply_without_text(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_123",
                    "create_time": "1000",
                    "msg_type": "file",
                    "content": '{"file_key":"file_123","file_name":"report.pdf"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )
        result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(result["text"], "")
        self.assertEqual(result["resource_refs"][0]["file_key"], "file_123")
        self.assertEqual(result["resource_refs"][0]["message_id"], "om_reply")

    def test_shared_runtime_captures_post_reply_with_text_and_image(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_123",
                    "create_time": "1000",
                    "msg_type": "post",
                    "content": (
                        '{"title":"","content":[['
                        '{"tag":"img","image_key":"img_123","width":2000,"height":1333}'
                        '],[{"tag":"text","text":"你收到这条消息了吗","style":[]}]]}'
                    ),
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )
        result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(result["text"], "你收到这条消息了吗")
        self.assertEqual(result["message_type"], "post")
        self.assertEqual(result["resource_refs"][0]["image_key"], "img_123")

    def test_shared_runtime_captures_card_choice(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )

        response = runtime.handle_event(
            "card.action.trigger",
            {
                "operator": {"open_id": "ou_owner"},
                "action": {
                    "value": {
                        "action": "feishu_ask_user_choice",
                        "question_id": "ask_123",
                        "answer": "Yes",
                    }
                },
                "context": {"open_message_id": "om_question", "open_chat_id": "oc_123"},
            },
        )
        result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(result["text"], "Yes")
        self.assertEqual(result["message_type"], "card_action")
        self.assertEqual(response.toast.content, "已收到你的选择")

    def test_shared_runtime_ignores_non_p2p_text_reply(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )

        with self.assertRaises(PendingQuestionTimeout):
            runtime.wait_for_question("ask_123", 0)

    def test_shared_runtime_captures_group_text_reply_when_target_chat_known(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_group",
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_group",
                    "chat_type": "group",
                    "create_time": "1000",
                    "message_type": "text",
                    "content": '{"text":"hello group"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )
        result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(result["text"], "hello group")
        self.assertEqual(result["chat_id"], "oc_group")

    def test_shared_runtime_ignores_text_reply_for_target_selection_question(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="select_target_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "select_target_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_p2p",
                    "chat_type": "p2p",
                    "create_time": "1000",
                    "message_type": "text",
                    "content": '{"text":"project-alpha"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )

        with self.assertRaises(PendingQuestionTimeout):
            runtime.wait_for_question("select_target_123", 0)

        response = runtime.handle_event(
            "card.action.trigger",
            {
                "operator": {"open_id": "ou_owner"},
                "action": {
                    "value": {
                        "action": "feishu_select_chat_target",
                        "question_id": "select_target_123",
                        "selection_kind": "new_chat",
                    },
                    "form_value": {
                        "new_chat_name": "project-alpha",
                    },
                },
                "context": {"open_message_id": "om_question", "open_chat_id": "oc_p2p"},
            },
        )
        result = runtime.wait_for_question("select_target_123", 1)

        self.assertEqual(result["text"], "project-alpha")
        self.assertEqual(result["message_type"], "card_action")
        self.assertEqual(response.toast.content, "已收到你的选择")

    def test_shared_runtime_allows_target_selection_alongside_pending_ask(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_ask",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_ask",
            sent_at_ms=1_000,
            target_chat_id="oc_ask",
        )
        runtime.register_pending_question(
            question_id="select_target_123",
            target_open_id="ou_owner",
            question="select",
            question_message_id="om_select",
            reserve_open_id_slot=False,
        )
        runtime.mark_waiting_for_reply(
            "select_target_123",
            question_message_id="om_select",
            sent_at_ms=1_000,
        )

        selection_response = runtime.handle_event(
            "card.action.trigger",
            {
                "operator": {"open_id": "ou_owner"},
                "action": {
                    "value": {
                        "action": "feishu_select_chat_target",
                        "question_id": "select_target_123",
                        "selection_kind": "current_conversation",
                    }
                },
                "context": {"open_message_id": "om_select", "open_chat_id": "oc_p2p"},
            },
        )
        selection_result = runtime.wait_for_question("select_target_123", 1)

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_ask",
                    "chat_type": "group",
                    "create_time": "1000",
                    "message_type": "text",
                    "content": '{"text":"hello ask"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )
        ask_result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(selection_result["text"], "current_conversation")
        self.assertEqual(selection_result["message_type"], "card_action")
        self.assertEqual(selection_response.toast.content, "已收到你的选择")
        self.assertEqual(ask_result["text"], "hello ask")

    def test_shared_runtime_ignores_reply_before_question_is_waiting(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="",
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_123",
                    "create_time": "2000",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )

        with self.assertRaises(PendingQuestionTimeout):
            runtime.wait_for_question("ask_123", 0)

    def test_shared_runtime_ignores_reply_older_than_sent_at(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=2_000,
            target_chat_id="oc_123",
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_123",
                    "create_time": "1",
                    "message_type": "text",
                    "content": '{"text":"old hello"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )

        with self.assertRaises(PendingQuestionTimeout):
            runtime.wait_for_question("ask_123", 0)

    def test_shared_runtime_ignores_card_choice_for_different_question_message(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )

        runtime.handle_event(
            "card.action.trigger",
            {
                "operator": {"open_id": "ou_owner"},
                "action": {
                    "value": {
                        "action": "feishu_ask_user_choice",
                        "question_id": "ask_123",
                        "answer": "Yes",
                    }
                },
                "context": {"open_message_id": "om_other", "open_chat_id": "oc_123"},
            },
        )

        with self.assertRaises(PendingQuestionTimeout):
            runtime.wait_for_question("ask_123", 0)

    def test_shared_runtime_captures_card_input_submission(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )

        response = runtime.handle_event(
            "card.action.trigger",
            {
                "operator": {"open_id": "ou_owner"},
                "action": {
                    "value": {
                        "action": "feishu_select_chat_target",
                        "question_id": "ask_123",
                        "selection_kind": "new_chat",
                    },
                    "form_value": {
                        "new_chat_name": "project-alpha",
                    },
                },
                "context": {"open_message_id": "om_question", "open_chat_id": "oc_123"},
            },
        )
        result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(result["text"], "project-alpha")
        self.assertEqual(result["display_text"], "新建群聊：project-alpha")
        self.assertEqual(result["card_action"]["value"]["selection_kind"], "new_chat")
        self.assertEqual(response.toast.content, "已收到你的选择")

    def test_shared_runtime_ignores_reply_from_different_chat_when_target_chat_known(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_expected",
        )

        runtime.handle_event(
            "im.message.receive_v1",
            {
                "message": {
                    "message_id": "om_reply",
                    "chat_id": "oc_other",
                    "create_time": "1000",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
                "sender": {"sender_id": {"open_id": "ou_owner"}},
            },
        )

        with self.assertRaises(PendingQuestionTimeout):
            runtime.wait_for_question("ask_123", 0)

    def test_shared_runtime_aborts_pending_question_when_longconn_fails(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeFailingSDK)
        runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )

        runtime.start()
        deadline = time.monotonic() + 1
        while runtime.long_connection_state() != "failed" and time.monotonic() < deadline:
            time.sleep(0.01)

        with self.assertRaises(PendingQuestionAborted):
            runtime.wait_for_question("ask_123", 1)

    def test_shared_runtime_reports_terminal_failure_callback(self) -> None:
        processor = FakeEventProcessor()
        failures: list[str] = []
        runtime = FeishuSharedLongConnectionRuntime(
            self._settings(),
            processor,
            sdk=FakeFailingSDK,
            on_terminal_failure=lambda exc: failures.append(str(exc)),
        )

        runtime.start()
        deadline = time.monotonic() + 1
        while not failures and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(failures, ["ws failed"])

    def test_shared_runtime_returns_captured_reply_even_after_terminal_failure(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        record = runtime.register_pending_question(
            question_id="ask_123",
            target_open_id="ou_owner",
            question="Q",
            question_message_id="om_question",
        )
        runtime.mark_waiting_for_reply(
            "ask_123",
            question_message_id="om_question",
            sent_at_ms=1_000,
            target_chat_id="oc_123",
        )
        record.resolve({"ok": True, "text": "hello", "message_id": "om_reply"})
        runtime._startup_error = RuntimeError("ws failed")

        result = runtime.wait_for_question("ask_123", 1)

        self.assertEqual(result["text"], "hello")

    def test_shared_runtime_refuses_restart_after_terminal_failure(self) -> None:
        processor = FakeEventProcessor()
        runtime = FeishuSharedLongConnectionRuntime(self._settings(), processor, sdk=FakeSDK)
        runtime._startup_error = RuntimeError("ws failed")

        with self.assertRaises(LongConnectionSetupError):
            runtime.ensure_started()
