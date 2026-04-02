from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
import unittest

from ask_user_via_feishu.ask_runtime import (
    ASK_AUTO_RECALL_ANSWER,
    ASK_RESOURCES_ONLY_ANSWER,
    AskWaitOptions,
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


class ImmediateSendableRuntimeMixin:
    def wait_until_sendable(self, question_id: str) -> None:
        return None


class FakeFileOnlyRuntime(ImmediateSendableRuntimeMixin):
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


class FakeTimeoutRuntime(ImmediateSendableRuntimeMixin):
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


class FakeAnsweredRuntime(ImmediateSendableRuntimeMixin):
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


class FakeQueuedAnswerRuntime(ImmediateSendableRuntimeMixin):
    def __init__(self, results: list[dict[str, object]]) -> None:
        self._results = [dict(result) for result in results]

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        return None

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
        return None

    def unregister_pending_question(self, question_id: str) -> None:
        return None

    def wait_for_question(self, question_id: str, timeout_seconds: int):
        return self._results.pop(0)


class FakeRollbackRuntime(ImmediateSendableRuntimeMixin):
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


class FakeMissingChatIdMessageService(FakeTimeoutMessageService):
    async def send_interactive(self, **kwargs):
        self.sent_interactive.append(kwargs)
        receive_id = str(kwargs.get("receive_id") or "ou_owner")
        return {
            "ok": True,
            "message_id": "om_question",
            "receive_id": receive_id,
            "chat_id": "",
            "create_time_ms": 1234567890123,
        }


class FakeFailingSendMessageService(FakeTimeoutMessageService):
    async def send_interactive(self, **kwargs):
        self.sent_interactive.append(kwargs)
        raise MessageValidationError("send failed")


class FakeFailingReminderMessageService(FakeTimeoutMessageService):
    async def send_text(self, **kwargs):
        self.sent_texts.append(kwargs)
        raise MessageValidationError("reminder failed")


class FakeAbortWhileQueuedRuntime(FakeAnsweredRuntime):
    def wait_until_sendable(self, question_id: str) -> None:
        raise PendingQuestionAborted("ws failed")


class FakeSequencedMessageService(FakeTimeoutMessageService):
    async def send_interactive(self, **kwargs):
        self.sent_interactive.append(kwargs)
        receive_id_type = str(kwargs.get("receive_id_type") or "open_id")
        receive_id = str(kwargs.get("receive_id") or "ou_owner")
        index = len(self.sent_interactive)
        return {
            "ok": True,
            "message_id": f"om_question_{index}",
            "receive_id": receive_id,
            "chat_id": receive_id if receive_id_type == "chat_id" else "oc_p2p",
            "create_time_ms": 1234567890123 + index,
        }


class FakeBlockingFailFirstQueuedMessageService(FakeTimeoutMessageService):
    def __init__(self) -> None:
        super().__init__()
        self.first_send_started = asyncio.Event()
        self.release_first_send = asyncio.Event()

    async def send_interactive(self, **kwargs):
        self.sent_interactive.append(kwargs)
        receive_id_type = str(kwargs.get("receive_id_type") or "open_id")
        receive_id = str(kwargs.get("receive_id") or "ou_owner")
        if len(self.sent_interactive) == 1:
            self.first_send_started.set()
            await asyncio.wait_for(self.release_first_send.wait(), timeout=1)
            raise MessageValidationError("send failed")
        return {
            "ok": True,
            "message_id": f"om_question_{len(self.sent_interactive)}",
            "receive_id": receive_id,
            "chat_id": receive_id if receive_id_type == "chat_id" else "oc_p2p",
            "create_time_ms": 1234567890123 + len(self.sent_interactive),
        }


class FakeQueuedRuntime:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.delivery_queues: dict[str, list[str]] = {}
        self.mark_waiting_calls: list[dict[str, object]] = []
        self.unregistered_question_ids: list[str] = []

    def ensure_started(self) -> None:
        return None

    def register_pending_question(self, **kwargs):
        question_id = str(kwargs["question_id"])
        delivery_key = f"{kwargs.get('receive_id_type', 'open_id')}:{kwargs.get('receive_id') or kwargs.get('target_open_id')}"
        reserve_delivery_slot = bool(kwargs.get("reserve_delivery_slot", True))
        queue = self.delivery_queues.setdefault(delivery_key, []) if reserve_delivery_slot else []
        status = "pending_send" if not reserve_delivery_slot or not queue else "queued"
        if reserve_delivery_slot:
            queue.append(question_id)
        self.records[question_id] = {
            "question_id": question_id,
            "delivery_key": delivery_key,
            "reserve_delivery_slot": reserve_delivery_slot,
            "status": status,
            "condition": threading.Condition(),
            "result": None,
        }
        return None

    def wait_until_sendable(self, question_id: str) -> None:
        record = self.records[question_id]
        while True:
            with record["condition"]:
                if record["status"] != "queued":
                    return
                record["condition"].wait(0.05)

    def mark_waiting_for_reply(self, question_id: str, **kwargs) -> None:
        record = self.records[question_id]
        record["status"] = "waiting_reply"
        self.mark_waiting_calls.append({"question_id": question_id, **kwargs})

    def wait_for_question(self, question_id: str, timeout_seconds: int):
        record = self.records[question_id]
        deadline = time.monotonic() + timeout_seconds
        while True:
            with record["condition"]:
                if record["result"] is not None:
                    return dict(record["result"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PendingQuestionTimeout(f"Timed out after {timeout_seconds} seconds")
                record["condition"].wait(min(remaining, 0.05))

    def resolve_question(self, question_id: str, result: dict[str, object]) -> None:
        record = self.records[question_id]
        with record["condition"]:
            record["result"] = result
            record["status"] = "answered"
            record["condition"].notify_all()

    def unregister_pending_question(self, question_id: str) -> None:
        record = self.records.pop(question_id, None)
        self.unregistered_question_ids.append(question_id)
        if record is None:
            return
        if not record["reserve_delivery_slot"]:
            return
        delivery_key = str(record["delivery_key"])
        queue = self.delivery_queues.get(delivery_key, [])
        if not queue:
            return
        was_active = queue[0] == question_id
        queue = [queued_question_id for queued_question_id in queue if queued_question_id != question_id]
        if not queue:
            self.delivery_queues.pop(delivery_key, None)
            return
        self.delivery_queues[delivery_key] = queue
        if not was_active:
            return
        promoted_record = self.records[queue[0]]
        with promoted_record["condition"]:
            promoted_record["status"] = "pending_send"
            promoted_record["condition"].notify_all()


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
                question_id=kwargs.get("question_id"),
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

    def test_same_delivery_second_ask_waits_to_send_until_first_finishes(self) -> None:
        settings = self._settings()
        fake_service = FakeSequencedMessageService()
        runtime = FakeQueuedRuntime()
        orchestrator = AskRuntimeOrchestrator(settings, fake_service, runtime)

        async def run_scenario() -> tuple[dict[str, object], dict[str, object]]:
            first_task = asyncio.create_task(
                orchestrator.ask(
                    question="第一问",
                    choices=None,
                    uuid=None,
                    receive_id_type="chat_id",
                    receive_id="oc_group_123",
                    question_id="ask_first",
                    wait_options=AskWaitOptions(
                        timeout_seconds=3,
                        reminder_max_attempts=0,
                        timeout_reminder_text="",
                        timeout_default_answer="",
                    ),
                )
            )
            await asyncio.sleep(0.1)
            second_task = asyncio.create_task(
                orchestrator.ask(
                    question="第二问",
                    choices=None,
                    uuid=None,
                    receive_id_type="chat_id",
                    receive_id="oc_group_123",
                    question_id="ask_second",
                    wait_options=AskWaitOptions(
                        timeout_seconds=1,
                        reminder_max_attempts=0,
                        timeout_reminder_text="",
                        timeout_default_answer="",
                    ),
                )
            )
            await asyncio.sleep(0.2)
            self.assertEqual(len(fake_service.sent_interactive), 1)
            self.assertIn("第一问", fake_service.sent_interactive[0]["card"]["elements"][0]["content"])
            await asyncio.sleep(1.2)
            self.assertEqual(len(fake_service.sent_interactive), 1)

            runtime.resolve_question(
                "ask_first",
                {
                    "message_id": "om_reply_1",
                    "chat_id": "oc_group_123",
                    "message_type": "text",
                    "text": "first answer",
                    "message_content": {"text": "first answer"},
                    "callback_response": {},
                },
            )
            first_result = await first_task
            await asyncio.sleep(0.2)
            self.assertEqual(len(fake_service.sent_interactive), 2)
            self.assertIn("第二问", fake_service.sent_interactive[1]["card"]["elements"][0]["content"])

            runtime.resolve_question(
                "ask_second",
                {
                    "message_id": "om_reply_2",
                    "chat_id": "oc_group_123",
                    "message_type": "text",
                    "text": "second answer",
                    "message_content": {"text": "second answer"},
                    "callback_response": {},
                },
            )
            second_result = await second_task
            return first_result, second_result

        first_result, second_result = asyncio.run(run_scenario())

        self.assertEqual(first_result["status"], "answered")
        self.assertEqual(first_result["user_answer"], "first answer")
        self.assertEqual(second_result["status"], "answered")
        self.assertEqual(second_result["user_answer"], "second answer")
        self.assertEqual(
            [call["question_id"] for call in runtime.mark_waiting_calls],
            ["ask_first", "ask_second"],
        )
        self.assertEqual(runtime.unregistered_question_ids, ["ask_first", "ask_second"])

    def test_returns_retryable_error_if_queued_question_cannot_be_sent(self) -> None:
        settings = self._settings()
        fake_service = FakeTimeoutMessageService()

        with self.assertRaises(RetryableAskError) as error:
            self._run_ask(settings, fake_service, FakeAbortWhileQueuedRuntime())

        self.assertEqual(error.exception.retry_stage, "before_send")
        self.assertEqual(fake_service.sent_interactive, [])

    def test_same_delivery_second_ask_sends_after_first_send_failure(self) -> None:
        settings = self._settings()
        fake_service = FakeBlockingFailFirstQueuedMessageService()
        runtime = FakeQueuedRuntime()
        orchestrator = AskRuntimeOrchestrator(settings, fake_service, runtime)

        async def run_scenario() -> tuple[str, dict[str, object]]:
            first_task = asyncio.create_task(
                orchestrator.ask(
                    question="第一问",
                    choices=None,
                    uuid=None,
                    receive_id_type="chat_id",
                    receive_id="oc_group_123",
                    question_id="ask_first",
                    wait_options=AskWaitOptions(
                        timeout_seconds=3,
                        reminder_max_attempts=0,
                        timeout_reminder_text="",
                        timeout_default_answer="",
                    ),
                )
            )
            await fake_service.first_send_started.wait()
            second_task = asyncio.create_task(
                orchestrator.ask(
                    question="第二问",
                    choices=None,
                    uuid=None,
                    receive_id_type="chat_id",
                    receive_id="oc_group_123",
                    question_id="ask_second",
                    wait_options=AskWaitOptions(
                        timeout_seconds=3,
                        reminder_max_attempts=0,
                        timeout_reminder_text="",
                        timeout_default_answer="",
                    ),
                )
            )
            await asyncio.sleep(0.1)
            self.assertEqual(len(fake_service.sent_interactive), 1)

            fake_service.release_first_send.set()
            with self.assertRaises(MessageValidationError):
                await first_task

            await asyncio.sleep(0.1)
            self.assertEqual(len(fake_service.sent_interactive), 2)
            runtime.resolve_question(
                "ask_second",
                {
                    "message_id": "om_reply_2",
                    "chat_id": "oc_group_123",
                    "message_type": "text",
                    "text": "second answer",
                    "message_content": {"text": "second answer"},
                    "callback_response": {},
                },
            )
            return "failed", await second_task

        first_status, second_result = asyncio.run(run_scenario())

        self.assertEqual(first_status, "failed")
        self.assertEqual(second_result["status"], "answered")
        self.assertEqual(second_result["user_answer"], "second answer")
        self.assertEqual(runtime.unregistered_question_ids, ["ask_first", "ask_second"])

    def test_target_selection_question_does_not_reserve_delivery_slot(self) -> None:
        settings = self._settings(ASK_REMINDER_MAX_ATTEMPTS="0", ASK_TIMEOUT_DEFAULT_ANSWER="")
        fake_service = FakeTimeoutMessageService()
        runtime = FakeTimeoutRuntime()

        result = self._run_ask(
            settings,
            fake_service,
            runtime,
            question_id="select_target_123",
            card={"header": {"title": {"tag": "plain_text", "content": "选择会话"}}, "elements": []},
        )

        self.assertEqual(result["status"], "timeout")
        self.assertEqual(runtime.registered_questions[0]["question_id"], "select_target_123")
        self.assertEqual(runtime.registered_questions[0]["ask_kind"], "bootstrap_selection")
        self.assertEqual(runtime.registered_questions[0]["receive_id_type"], "open_id")
        self.assertEqual(runtime.registered_questions[0]["receive_id"], "ou_owner")
        self.assertFalse(runtime.registered_questions[0]["reserve_delivery_slot"])

    def test_chat_target_uses_receive_id_as_fallback_target_chat_id(self) -> None:
        settings = self._settings()
        fake_service = FakeMissingChatIdMessageService()
        runtime = FakeTrackingRuntime()

        result = self._run_ask(
            settings,
            fake_service,
            runtime,
            receive_id_type="chat_id",
            receive_id="oc_group",
        )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(runtime.waiting_calls[0]["target_chat_id"], "oc_group")

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

    def test_different_target_ask_does_not_clear_previous_processing_reaction(self) -> None:
        settings = self._settings(REACTION_ENABLED="true")
        fake_service = FakeTimeoutMessageService()
        runtime = FakeQueuedAnswerRuntime(
            [
                {
                    "message_id": "om_reply_p2p",
                    "chat_id": "oc_p2p",
                    "message_type": "text",
                    "text": "first",
                    "message_content": {"text": "first"},
                    "callback_response": {},
                },
                {
                    "message_id": "om_reply_group",
                    "chat_id": "oc_group_123",
                    "message_type": "text",
                    "text": "second",
                    "message_content": {"text": "second"},
                    "callback_response": {},
                },
            ]
        )
        orchestrator = AskRuntimeOrchestrator(settings, fake_service, runtime)

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
                receive_id_type="chat_id",
                receive_id="oc_group_123",
                wait_options=build_wait_options(settings),
            )
        )

        self.assertEqual(first_result["status"], "answered")
        self.assertEqual(second_result["status"], "answered")
        self.assertEqual(
            fake_service.created_reactions,
            [
                {"message_id": "om_reply_p2p", "emoji_type": "Typing"},
                {"message_id": "om_reply_group", "emoji_type": "Typing"},
            ],
        )
        self.assertEqual(fake_service.deleted_reactions, [])

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
