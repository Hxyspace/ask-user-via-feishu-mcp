from __future__ import annotations

import asyncio
import importlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ask_user_via_feishu.ask_state import AskStatusSnapshot, TargetQueueStatus
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.event_handlers import parse_message_content
from ask_user_via_feishu.event_processor import FeishuEventProcessor
from ask_user_via_feishu.longconn import FeishuLongConnectionSubscriber, LongConnectionSetupError

logger = logging.getLogger(__name__)

SELECT_TARGET_NEW_CHAT_FIELD = "new_chat_name"


def _is_target_selection_question(question_id: str) -> bool:
    return question_id.startswith("select_target_")


class PendingQuestionTimeout(RuntimeError):
    """Raised when a shared-runtime question does not receive an answer in time."""


class PendingQuestionAborted(RuntimeError):
    """Raised when a shared-runtime question is aborted by terminal longconn failure."""


@dataclass
class PendingQuestion:
    question_id: str
    target_open_id: str
    question: str
    question_message_id: str
    ask_kind: str = "ordinary"
    receive_id_type: str = "open_id"
    receive_id: str = ""
    delivery_key: str = ""
    reserve_open_id_slot: bool = True
    status: str = "pending_send"
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    sent_at_ms: int = 0
    target_chat_id: str = ""
    condition: threading.Condition = field(default_factory=threading.Condition)
    result: dict[str, Any] | None = None

    def resolve(self, result: dict[str, Any]) -> None:
        with self.condition:
            if self.result is None:
                self.result = result
                self.status = "answered"
            self.condition.notify_all()


class FeishuSharedLongConnectionRuntime:
    def __init__(
        self,
        settings: Settings,
        event_processor: FeishuEventProcessor,
        sdk: Any | None = None,
        *,
        on_terminal_failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._settings = settings
        self._event_processor = event_processor
        self._subscriber = FeishuLongConnectionSubscriber(settings, event_processor, sdk=sdk)
        self._lock = threading.Lock()
        self._pending_by_question_id: dict[str, PendingQuestion] = {}
        self._pending_by_open_id: dict[str, PendingQuestion] = {}
        self._thread: threading.Thread | None = None
        self._startup_error: BaseException | None = None
        self._on_terminal_failure = on_terminal_failure

    def start(self) -> None:
        with self._lock:
            if self._startup_error is not None:
                raise LongConnectionSetupError(str(self._startup_error)) from self._startup_error
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run_forever, daemon=True)
            self._thread.start()
            logger.info("Started shared Feishu long-connection runtime thread.")

    def ensure_started(self) -> None:
        self.start()

    def register_pending_question(
        self,
        *,
        question_id: str,
        target_open_id: str,
        question: str,
        question_message_id: str,
        ask_kind: str = "ordinary",
        receive_id_type: str = "open_id",
        receive_id: str = "",
        reserve_open_id_slot: bool = True,
    ) -> PendingQuestion:
        normalized_question_id = question_id.strip()
        normalized_open_id = target_open_id.strip()
        normalized_ask_kind = ask_kind.strip() or "ordinary"
        normalized_receive_id_type = receive_id_type.strip() or "open_id"
        normalized_receive_id = receive_id.strip() or normalized_open_id
        if not normalized_question_id:
            raise ValueError("question_id must not be empty.")
        if not normalized_open_id:
            raise ValueError("target_open_id must not be empty.")
        if normalized_ask_kind not in {"ordinary", "bootstrap_selection"}:
            raise ValueError("ask_kind must be either ordinary or bootstrap_selection.")
        with self._lock:
            existing = self._pending_by_open_id.get(normalized_open_id)
            if reserve_open_id_slot and existing is not None:
                raise ValueError(
                    "A pending Feishu question for this open_id already exists. Concurrent questions for the same user are not supported."
                )
            record = PendingQuestion(
                question_id=normalized_question_id,
                target_open_id=normalized_open_id,
                question=question,
                question_message_id=question_message_id,
                ask_kind=normalized_ask_kind,
                receive_id_type=normalized_receive_id_type,
                receive_id=normalized_receive_id,
                delivery_key=f"{normalized_receive_id_type}:{normalized_receive_id}",
                reserve_open_id_slot=reserve_open_id_slot,
            )
            self._pending_by_question_id[normalized_question_id] = record
            if reserve_open_id_slot:
                self._pending_by_open_id[normalized_open_id] = record
            return record

    def mark_waiting_for_reply(
        self,
        question_id: str,
        *,
        question_message_id: str,
        sent_at_ms: int,
        target_chat_id: str = "",
    ) -> None:
        with self._lock:
            record = self._pending_by_question_id.get(question_id.strip())
            if record is None:
                raise ValueError("Pending question not found.")
            record.question_message_id = question_message_id.strip()
            record.sent_at_ms = sent_at_ms
            record.target_chat_id = target_chat_id.strip()
            record.status = "waiting_reply"

    def wait_for_question(self, question_id: str, timeout_seconds: int) -> dict[str, Any]:
        with self._lock:
            record = self._pending_by_question_id.get(question_id.strip())
        if record is None:
            raise ValueError("Pending question not found.")
        deadline = time.monotonic() + timeout_seconds
        while True:
            with record.condition:
                if record.result is not None:
                    return dict(record.result)
                startup_error = self._startup_error
                if startup_error is not None:
                    raise PendingQuestionAborted("Shared Feishu long connection is no longer available.") from startup_error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PendingQuestionTimeout(
                        f"No matching Feishu event received within {timeout_seconds} seconds."
                    )
                record.condition.wait(min(remaining, 0.2))

    def unregister_pending_question(self, question_id: str) -> None:
        with self._lock:
            record = self._pending_by_question_id.pop(question_id.strip(), None)
            if record is not None and record.reserve_open_id_slot:
                self._pending_by_open_id.pop(record.target_open_id, None)

    def has_pending_question(self) -> bool:
        with self._lock:
            return bool(self._pending_by_question_id)

    def current_pending_question_id(self) -> str:
        with self._lock:
            for question_id in self._pending_by_question_id:
                return question_id
        return ""

    def ask_status_snapshot(self) -> AskStatusSnapshot:
        with self._lock:
            records = list(self._pending_by_question_id.values())
        queues_by_delivery_key: dict[str, dict[str, Any]] = {}
        queue_exempt_question_ids: list[str] = []
        active_ask_count = 0
        queued_ask_count = 0
        for record in records:
            if record.ask_kind != "ordinary":
                queue_exempt_question_ids.append(record.question_id)
                continue
            entry = queues_by_delivery_key.setdefault(
                record.delivery_key,
                {
                    "delivery_key": record.delivery_key,
                    "receive_id_type": record.receive_id_type,
                    "receive_id": record.receive_id,
                    "active_question_id": "",
                    "queued_question_ids": [],
                },
            )
            if not entry["active_question_id"]:
                entry["active_question_id"] = record.question_id
                active_ask_count += 1
            else:
                entry["queued_question_ids"].append(record.question_id)
                queued_ask_count += 1
        queues_by_target = tuple(
            TargetQueueStatus(
                delivery_key=str(entry["delivery_key"]),
                receive_id_type=str(entry["receive_id_type"]),
                receive_id=str(entry["receive_id"]),
                active_question_id=str(entry["active_question_id"]),
                queued_question_ids=tuple(str(question_id) for question_id in entry["queued_question_ids"]),
            )
            for _, entry in sorted(queues_by_delivery_key.items())
        )
        return AskStatusSnapshot(
            active_ask_count=active_ask_count,
            queued_ask_count=queued_ask_count,
            queues_by_target=queues_by_target,
            queue_exempt_question_ids=tuple(sorted(queue_exempt_question_ids)),
        )

    def long_connection_state(self) -> str:
        if self._startup_error is not None:
            return "failed"
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return "running"
        return "stopped"

    def handle_event(self, event_type: str, data: Any, *, schema: str = "2.0") -> Any:
        payload = self._subscriber._normalize_payload(event_type, schema=schema, data=data)
        intercepted = self._intercept_pending_question(event_type, payload)
        if intercepted is not None:
            return self._subscriber._build_sdk_response(event_type, intercepted)
        result = self._event_processor.process_payload(payload)
        return self._subscriber._build_sdk_response(event_type, result)

    def _run_forever(self) -> None:
        ws_client_class = self._subscriber._sdk.ws.Client
        use_real_ws_loop = str(getattr(ws_client_class, "__module__", "")).startswith("lark_oapi.")
        try:
            ws_client_module = importlib.import_module("lark_oapi.ws.client") if use_real_ws_loop else None
        except ImportError:  # pragma: no cover
            ws_client_module = None
        previous_loop = getattr(ws_client_module, "loop", None)
        loop = asyncio.new_event_loop() if ws_client_module is not None else None
        if ws_client_module is not None and loop is not None:
            ws_client_module.loop = loop
            asyncio.set_event_loop(loop)
        try:
            event_handler = self._build_event_handler()
            client = self._subscriber._sdk.ws.Client(
                self._settings.app_id,
                self._settings.app_secret,
                event_handler=event_handler,
                log_level=self._subscriber._resolve_sdk_log_level(),
            )
            client.start()
        except BaseException as exc:  # noqa: BLE001
            self._startup_error = exc
            self._notify_pending_question_updates()
            if self._on_terminal_failure is not None:
                try:
                    self._on_terminal_failure(exc)
                except Exception:  # pragma: no cover
                    logger.exception("Shared runtime terminal-failure callback failed.")
            logger.exception("Shared Feishu long-connection runtime stopped unexpectedly: %s", exc)
        finally:
            if loop is not None and ws_client_module is not None:
                try:
                    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:  # pragma: no cover
                    pass
                try:
                    loop.close()
                finally:
                    ws_client_module.loop = previous_loop
                    asyncio.set_event_loop(previous_loop)

    def _notify_pending_question_updates(self) -> None:
        with self._lock:
            records = list(self._pending_by_question_id.values())
        for record in records:
            with record.condition:
                record.condition.notify_all()

    def _build_event_handler(self) -> Any:
        builder = self._subscriber._sdk.EventDispatcherHandler.builder("", "")
        for event_type in self._subscriber._event_types:
            builder = self._subscriber._register_v2_event_callback(builder, event_type, self._build_v2_callback(event_type))
        return builder.build()

    def _build_v2_callback(self, event_type: str) -> Any:
        def handle(data: Any) -> Any:
            return self.handle_event(event_type, data, schema="2.0")

        return handle

    def _intercept_pending_question(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if event_type == "im.message.receive_v1":
            return self._capture_message_reply(payload)
        if event_type == "card.action.trigger":
            return self._capture_card_choice(payload)
        return None

    def _capture_message_reply(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event = payload.get("event") or {}
        sender_open_id = str(event.get("sender", {}).get("sender_id", {}).get("open_id") or "").strip()
        if not sender_open_id:
            return None
        message = event.get("message") or {}
        content = parse_message_content(message)
        message_type = str(message.get("message_type") or message.get("msg_type") or "").strip().lower()
        text = _extract_reply_text(content, message_type=message_type)
        if not message_type:
            if str(content.get("image_key") or "").strip():
                message_type = "image"
            elif str(content.get("file_key") or "").strip():
                message_type = "file"
            elif text:
                message_type = "text"
        message_id = str(message.get("message_id") or "").strip()
        message_chat_id = str(message.get("chat_id") or "").strip()
        message_create_time_ms = _parse_event_timestamp_ms(message.get("create_time"))
        resource_refs = _extract_resource_refs(content, message_id=message_id)
        if not text and not resource_refs:
            return None
        with self._lock:
            record = self._pending_by_open_id.get(sender_open_id)
        if record is None or record.status != "waiting_reply":
            return None
        if _is_target_selection_question(record.question_id):
            return None
        chat_type = str(message.get("chat_type") or event.get("chat_type") or "").strip().lower()
        if not record.target_chat_id and chat_type and chat_type != "p2p":
            return None
        if record.sent_at_ms and message_create_time_ms is not None and message_create_time_ms < record.sent_at_ms:
            return None
        if record.target_chat_id and message_chat_id != record.target_chat_id:
            return None
        record.resolve(
            {
                "ok": True,
                "sender_open_id": sender_open_id,
                "chat_id": message_chat_id,
                "message_id": message_id,
                "message_type": message_type,
                "text": text,
                "message_content": content,
                "resource_refs": resource_refs,
                "create_time_ms": message_create_time_ms or 0,
                "callback_response": {},
            }
        )
        logger.info(
            "Captured shared-runtime reply question_id=%s sender=%s type=%s",
            record.question_id,
            sender_open_id,
            message_type,
        )
        return {"handled": True, "reply_sent": False}

    def _capture_card_choice(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event = payload.get("event") or {}
        operator_open_id = str(event.get("operator", {}).get("open_id") or "").strip()
        action_payload = event.get("action", {})
        action_value = action_payload.get("value", {})
        if not isinstance(action_value, dict):
            action_value = {}
        form_value = action_payload.get("form_value")
        if not isinstance(form_value, dict):
            form_value = {}
        target_question_id = str(action_value.get("question_id") or "").strip()
        action = str(action_value.get("action") or "").strip()
        if not target_question_id or not action:
            return None
        with self._lock:
            record = self._pending_by_question_id.get(target_question_id)
        open_message_id = str(event.get("context", {}).get("open_message_id") or "").strip()
        open_chat_id = str(event.get("context", {}).get("open_chat_id") or "").strip()
        chat_type = str(event.get("context", {}).get("chat_type") or event.get("chat_type") or "").strip().lower()
        if record is None or record.status != "waiting_reply" or operator_open_id != record.target_open_id:
            return None
        if record.question_message_id and open_message_id and open_message_id != record.question_message_id:
            return None
        if record.target_chat_id:
            if open_chat_id != record.target_chat_id:
                return None
        elif chat_type and chat_type != "p2p":
            return None
        answer = ""
        display_text = ""
        toast_content = "已收到你的选择"
        if action == "feishu_ask_user_choice":
            answer = str(action_value.get("answer") or "").strip()
            if not answer:
                return None
            display_text = answer
        elif action == "feishu_select_chat_target":
            selection_kind = str(action_value.get("selection_kind") or "").strip()
            if selection_kind == "current_conversation":
                answer = "current_conversation"
                display_text = "继续使用当前会话"
            elif selection_kind == "existing_chat":
                chat_name = str(action_value.get("chat_name") or "").strip()
                chat_id = str(action_value.get("chat_id") or "").strip()
                if not chat_id:
                    return None
                answer = chat_id
                display_text = f"切换到群聊：{chat_name or chat_id}"
            elif selection_kind == "new_chat":
                answer = str(form_value.get(SELECT_TARGET_NEW_CHAT_FIELD) or "").strip()
                if not answer:
                    return {
                        "handled": True,
                        "reply_sent": False,
                        "callback_response": {
                            "toast": {"type": "warning", "content": "请先填写群聊名称"},
                        },
                    }
                display_text = f"新建群聊：{answer}"
            else:
                return None
        else:
            return None
        card_action = {
            "action": action,
            "value": dict(action_value),
        }
        record.resolve(
            {
                "ok": True,
                "sender_open_id": operator_open_id,
                "chat_id": open_chat_id,
                "message_id": open_message_id,
                "message_type": "card_action",
                "text": answer,
                "display_text": display_text,
                "message_content": {"text": answer, "card_action": card_action},
                "card_action": card_action,
                "create_time_ms": 0,
                "callback_response": {
                    "toast": {"type": "success", "content": toast_content},
                },
            }
        )
        logger.info(
            "Captured shared-runtime card choice question_id=%s operator=%s",
            record.question_id,
            operator_open_id,
        )
        return {
            "handled": True,
            "reply_sent": False,
            "callback_response": {
                "toast": {"type": "success", "content": toast_content},
            },
        }


def _extract_resource_refs(message_content: dict[str, Any], *, message_id: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    image_key = str(message_content.get("image_key") or "").strip()
    if image_key:
        refs.append({"kind": "image", "message_id": message_id, "image_key": image_key})
    file_key = str(message_content.get("file_key") or "").strip()
    if file_key:
        refs.append(
            {
                "kind": "file",
                "message_id": message_id,
                "file_key": file_key,
                "file_name": str(message_content.get("file_name") or "").strip(),
            }
        )
    post_content = message_content.get("content")
    if isinstance(post_content, list):
        for paragraph in post_content:
            if not isinstance(paragraph, list):
                continue
            for element in paragraph:
                if not isinstance(element, dict):
                    continue
                if str(element.get("tag") or "").strip() != "img":
                    continue
                post_image_key = str(element.get("image_key") or "").strip()
                if not post_image_key:
                    continue
                refs.append({"kind": "image", "message_id": message_id, "image_key": post_image_key})
    return refs


def _extract_reply_text(message_content: dict[str, Any], *, message_type: str) -> str:
    text = str(message_content.get("text") or "").strip()
    if text:
        return text
    if message_type != "post":
        return ""
    text_parts: list[str] = []
    post_content = message_content.get("content")
    if not isinstance(post_content, list):
        return ""
    for paragraph in post_content:
        if not isinstance(paragraph, list):
            continue
        paragraph_parts: list[str] = []
        for element in paragraph:
            if not isinstance(element, dict):
                continue
            tag = str(element.get("tag") or "").strip()
            if tag in {"text", "a"}:
                value = str(element.get("text") or "").strip()
                if value:
                    paragraph_parts.append(value)
            elif tag == "at":
                user_id = str(element.get("user_id") or "").strip()
                if user_id:
                    paragraph_parts.append(f"@{user_id}")
        paragraph_text = "".join(paragraph_parts).strip()
        if paragraph_text:
            text_parts.append(paragraph_text)
    return "\n\n".join(text_parts).strip()


def _parse_event_timestamp_ms(value: Any) -> int | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    timestamp = int(normalized)
    if timestamp <= 0:
        return None
    return timestamp
