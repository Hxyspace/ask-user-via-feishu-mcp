from __future__ import annotations

import asyncio
from pathlib import Path
import unittest

from ask_user_via_feishu.ask_runtime import (
    ASK_AUTO_RECALL_ANSWER,
    ASK_RESOURCES_ONLY_ANSWER,
    AskRuntimeOrchestrator,
    build_wait_options,
)
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.errors import MessageValidationError, RetryableAskError
from ask_user_via_feishu.longconn import LongConnectionSetupError
from ask_user_via_feishu.shared_longconn import PendingQuestionAborted, PendingQuestionTimeout

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
        receive_id_type = str(kwargs.get("receive_id_type") or "open_id")
        receive_id = str(kwargs.get("receive_id") or "ou_owner")
        return {
            "ok": True,
            "message_id": "om_question",
            "receive_id": receive_id,
            "chat_id": receive_id if receive_id_type == "chat_id" else "oc_p2p",
            "create_time_ms": 1234567890123,
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


class FakeFileOnlyRuntime:
    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        return None

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
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
    def __init__(self) -> None:
        super().__init__()
        self.download_calls: list[dict] = []

    async def download_reply_resources(self, **kwargs):
        self.download_calls.append(kwargs)
        return ["/tmp/receive_files/ask_123/report.pdf"]


class FakeTimeoutRuntime:
    last_instance = None

    def __init__(self) -> None:
        self.registered_questions: list[dict] = []
        self.unregistered_question_ids: list[str] = []
        type(self).last_instance = self

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        self.registered_questions.append(kwargs)
        return None

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        self.unregistered_question_ids.append(question_id)

    def wait_for_question(self, question_id: str, timeout_seconds: int):
        raise PendingQuestionTimeout(f"Timed out after {timeout_seconds} seconds")


class FakeAnsweredRuntime:
    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        return None

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        return None

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
    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        raise ValueError(
            "A pending Feishu question for this open_id already exists. Concurrent questions for the same user are not supported."
        )

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        return None


class FakeRollbackRuntime:
    last_instance = None

    def __init__(self) -> None:
        self.registered_questions: list[dict] = []
        self.unregistered_question_ids: list[str] = []
        type(self).last_instance = self

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        self.registered_questions.append(kwargs)
        return None

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        self.unregistered_question_ids.append(question_id)


class FakeTerminalBeforeSendRuntime:
    def ensure_started(self) -> None:
        raise LongConnectionSetupError("ws failed")


class FakeTerminalAfterSendRuntime(FakeRollbackRuntime):
    def ensure_started(self) -> None:
        return None

    def wait_for_question(self, question_id: str, timeout_seconds: int):
        raise PendingQuestionAborted("ws failed")


class FakeTrackingRuntime(FakeAnsweredRuntime):
    def __init__(self) -> None:
        self.waiting_calls: list[dict[str, object]] = []

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
        self.waiting_calls.append({"question_id": question_id, **kwargs})


class FakeFailingSendMessageService(FakeTimeoutMessageService):
    async def send_interactive(self, **kwargs):
        self.sent_interactive.append(kwargs)
        raise MessageValidationError("send failed")


class FakeFailingReminderMessageService(FakeTimeoutMessageService):
    async def send_text(self, **kwargs):
        self.sent_texts.append(kwargs)
        raise MessageValidationError("reminder failed")


class AskRuntimeTest(unittest.TestCase):
    def _settings(self, **overrides: str) -> Settings:
        env = {
            "APP_ID": "cli_123",
            "APP_SECRET": "secret_123",
            "OWNER_OPEN_ID": "ou_owner",
            "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
        }
        env.update(overrides)
        return Settings.from_env(env)

    def _run_ask(self, settings: Settings, service, runtime, **kwargs):
        orchestrator = AskRuntimeOrchestrator(
            settings,
            service,
            runtime,
            download_root=kwargs.get("download_root"),
        )
        return asyncio.run(
            orchestrator.ask(
                question=kwargs.get("question", "还继续吗？"),
                choices=kwargs.get("choices"),
                uuid=kwargs.get("uuid"),
                receive_id_type=kwargs.get("receive_id_type", "open_id"),
                receive_id=kwargs.get("receive_id", settings.owner_open_id),
                wait_options=build_wait_options(settings),
                card=kwargs.get("card"),
            )
        )

    def test_timeout_returns_auto_recall_after_reminder_limit(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="1",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()

        result = self._run_ask(settings, fake_service, FakeTimeoutRuntime())

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], ASK_AUTO_RECALL_ANSWER)
        self.assertEqual(len(fake_service.sent_texts), 1)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_timeout_reminder_failure_does_not_abort_ask(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="1",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeFailingReminderMessageService()

        result = self._run_ask(settings, fake_service, FakeTimeoutRuntime())

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], ASK_AUTO_RECALL_ANSWER)
        self.assertEqual(len(fake_service.sent_texts), 1)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_timeout_with_zero_reminders_exits_on_first_timeout(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="0",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()

        result = self._run_ask(settings, fake_service, FakeTimeoutRuntime())

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], ASK_AUTO_RECALL_ANSWER)
        self.assertEqual(len(fake_service.sent_texts), 0)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_timeout_keeps_same_pending_registration_between_retries(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="2",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()
        runtime = FakeTimeoutRuntime()

        result = self._run_ask(settings, fake_service, runtime)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(fake_service.sent_texts), 2)
        self.assertEqual(len(runtime.registered_questions), 1)
        self.assertEqual(runtime.unregistered_question_ids, [runtime.registered_questions[0]["question_id"]])

    def test_timeout_with_empty_default_returns_plain_timeout(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="1",
            ASK_TIMEOUT_REMINDER_TEXT="请尽快回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="",
        )
        fake_service = FakeTimeoutMessageService()

        result = self._run_ask(settings, fake_service, FakeTimeoutRuntime())

        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["user_answer"], "")
        self.assertEqual(len(fake_service.sent_texts), 1)
        self.assertEqual(len(fake_service.updated_cards), 1)

    def test_fails_before_sending_when_pending_slot_is_unavailable(self) -> None:
        settings = self._settings()
        fake_service = FakeTimeoutMessageService()

        with self.assertRaises(ValueError):
            self._run_ask(settings, fake_service, FakeRejectPendingRuntime())

        self.assertEqual(fake_service.sent_interactive, [])

    def test_unregisters_reserved_pending_when_send_fails(self) -> None:
        settings = self._settings()
        fake_service = FakeFailingSendMessageService()
        runtime = FakeRollbackRuntime()

        with self.assertRaises(MessageValidationError):
            self._run_ask(settings, fake_service, runtime)

        self.assertEqual(len(runtime.registered_questions), 1)
        self.assertEqual(runtime.unregistered_question_ids, [runtime.registered_questions[0]["question_id"]])
        self.assertEqual(len(fake_service.sent_interactive), 1)

    def test_returns_retryable_error_before_send_when_longconn_is_terminal(self) -> None:
        settings = self._settings()
        fake_service = FakeTimeoutMessageService()

        with self.assertRaises(RetryableAskError) as error:
            self._run_ask(settings, fake_service, FakeTerminalBeforeSendRuntime())

        self.assertEqual(error.exception.retry_stage, "before_send")
        self.assertEqual(fake_service.sent_interactive, [])

    def test_expires_sent_card_and_returns_retryable_error_when_longconn_dies_mid_wait(self) -> None:
        settings = self._settings()
        fake_service = FakeTimeoutMessageService()
        runtime = FakeTerminalAfterSendRuntime()

        with self.assertRaises(RetryableAskError) as error:
            self._run_ask(settings, fake_service, runtime)

        self.assertEqual(error.exception.retry_stage, "after_send")
        self.assertEqual(len(fake_service.updated_cards), 1)
        self.assertIn("系统会自动重新发起一次提问", fake_service.updated_cards[0]["card"]["elements"][0]["content"])
        self.assertEqual(runtime.unregistered_question_ids, [runtime.registered_questions[0]["question_id"]])

    def test_returns_answer_when_card_update_fails(self) -> None:
        settings = self._settings(REACTION_ENABLED="true")
        fake_service = FakeTimeoutMessageService()

        async def broken_update_interactive(**kwargs):
            raise MessageValidationError("card update failed")

        fake_service.update_interactive = broken_update_interactive  # type: ignore[method-assign]

        result = self._run_ask(settings, fake_service, FakeAnsweredRuntime())

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], "answer")
        self.assertEqual(fake_service.created_reactions, [{"message_id": "om_reply", "emoji_type": "Typing"}])
        self.assertEqual(fake_service.deleted_reactions, [])

    def test_returns_file_paths_and_reask_hint_for_file_only_reply(self) -> None:
        settings = self._settings()
        fake_service = FakeDownloadMessageService()
        download_root = Path("/tmp/daemon-runtime/attachments")

        result = self._run_ask(
            settings,
            fake_service,
            FakeFileOnlyRuntime(),
            question="把资料发我",
            download_root=download_root,
        )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["user_answer"], ASK_RESOURCES_ONLY_ANSWER)
        self.assertEqual(result["downloaded_paths"], ["/tmp/receive_files/ask_123/report.pdf"])
        self.assertEqual(fake_service.download_calls[0]["target_root"], download_root)

    def test_next_ask_clears_previous_processing_reaction(self) -> None:
        settings = self._settings(REACTION_ENABLED="true")
        fake_service = FakeTimeoutMessageService()
        orchestrator = AskRuntimeOrchestrator(settings, fake_service, FakeAnsweredRuntime())

        first_result = asyncio.run(
            orchestrator.ask(
                question="第一问",
                choices=None,
                uuid=None,
                receive_id_type="open_id",
                receive_id=settings.owner_open_id,
                wait_options=build_wait_options(settings),
            )
        )
        second_result = asyncio.run(
            orchestrator.ask(
                question="第二问",
                choices=None,
                uuid=None,
                receive_id_type="open_id",
                receive_id=settings.owner_open_id,
                wait_options=build_wait_options(settings),
            )
        )

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

    def test_mark_waiting_uses_send_result_chat_and_time(self) -> None:
        settings = self._settings()
        fake_service = FakeTimeoutMessageService()
        runtime = FakeTrackingRuntime()

        result = self._run_ask(settings, fake_service, runtime)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(runtime.waiting_calls[0]["question_message_id"], "om_question")
        self.assertEqual(runtime.waiting_calls[0]["target_chat_id"], "oc_p2p")
        self.assertEqual(runtime.waiting_calls[0]["sent_at_ms"], 1234567890123)

    def test_chat_id_ask_routes_delivery_to_chat_but_keeps_owner_as_allowed_actor(self) -> None:
        settings = self._settings(
            ASK_REMINDER_MAX_ATTEMPTS="1",
            ASK_TIMEOUT_REMINDER_TEXT="请在群里回复",
            ASK_TIMEOUT_DEFAULT_ANSWER="[AUTO_RECALL]",
        )
        fake_service = FakeTimeoutMessageService()
        runtime = FakeTimeoutRuntime()

        result = self._run_ask(
            settings,
            fake_service,
            runtime,
            receive_id_type="chat_id",
            receive_id="oc_group_123",
        )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(fake_service.sent_interactive[0]["receive_id_type"], "chat_id")
        self.assertEqual(fake_service.sent_interactive[0]["receive_id"], "oc_group_123")
        self.assertEqual(fake_service.sent_texts[0]["receive_id_type"], "chat_id")
        self.assertEqual(fake_service.sent_texts[0]["receive_id"], "oc_group_123")
        self.assertEqual(runtime.registered_questions[0]["target_open_id"], "ou_owner")
