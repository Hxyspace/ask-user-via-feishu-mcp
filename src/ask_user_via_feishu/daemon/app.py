from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from ask_user_via_feishu.ask_runtime import AskRuntimeOrchestrator, AskWaitOptions
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.daemon.runtime import runtime_dir_for_settings
from ask_user_via_feishu.errors import RetryableAskError
from ask_user_via_feishu.runtime import build_event_processor, build_message_service
from ask_user_via_feishu.shared_longconn import FeishuSharedLongConnectionRuntime
from ask_user_via_feishu.daemon.server import SharedLongConnDaemonServer


class SharedLongConnDaemonApp:
    def __init__(self, settings: Settings, *, runtime_dir: Path | None = None) -> None:
        target_runtime_dir = runtime_dir_for_settings(settings) if runtime_dir is None else runtime_dir.expanduser().resolve()
        self._runtime_dir = target_runtime_dir
        self._daemon_state = "serving"
        self._failure_reason = ""
        self._lifecycle_lock = threading.Lock()
        self._retirement_thread: threading.Thread | None = None
        self._terminal_shutdown_delay_seconds = 1.0
        self._message_service = build_message_service(settings)
        event_processor = build_event_processor(settings)
        shared_runtime = FeishuSharedLongConnectionRuntime(
            settings,
            event_processor,
            on_terminal_failure=self._handle_terminal_failure,
        )
        self._ask_runtime = AskRuntimeOrchestrator(
            settings,
            self._message_service,
            shared_runtime,
            download_root=self._runtime_dir / "attachments",
        )
        self._shared_runtime = shared_runtime
        self._initialized = False
        self._server = SharedLongConnDaemonServer(
            settings,
            target_runtime_dir,
            ask_handler=self._ask_and_wait,
            send_handlers={
                "/v1/send_text_message": self._send_text_message,
                "/v1/send_image_message": self._send_image_message,
                "/v1/send_file_message": self._send_file_message,
                "/v1/send_post_message": self._send_post_message,
            },
            status_provider=self._status,
        )

    def serve_forever(self) -> None:
        self.initialize()
        self._server.serve_forever()

    def initialize(self) -> None:
        if self._initialized:
            return
        asyncio.run(self._message_service.health_check())
        self._initialized = True

    def _ask_and_wait(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_accepting_asks()
        choices_value = payload.get("choices") or []
        if choices_value is not None and not isinstance(choices_value, list):
            raise ValueError("choices must be a JSON array when provided.")
        wait_options = AskWaitOptions(
            timeout_seconds=int(payload.get("timeout_seconds") or 0),
            reminder_max_attempts=int(payload.get("reminder_max_attempts") or 0),
            timeout_reminder_text=str(payload.get("timeout_reminder_text") or ""),
            timeout_default_answer=str(payload.get("timeout_default_answer") or ""),
        )
        return asyncio.run(
            self._ask_runtime.ask(
                question=str(payload.get("question") or ""),
                choices=[str(choice) for choice in choices_value] if isinstance(choices_value, list) else None,
                uuid=str(payload.get("uuid") or "") or None,
                receive_id_type=str(payload.get("receive_id_type") or "open_id"),
                receive_id=str(payload.get("receive_id") or ""),
                wait_options=wait_options,
            )
        )

    def _status(self) -> dict[str, Any]:
        with self._lifecycle_lock:
            daemon_state = self._daemon_state
            failure_reason = self._failure_reason
        return {
            "daemon_state": daemon_state,
            "failure_reason": failure_reason,
            "long_connection_state": self._shared_runtime.long_connection_state(),
            "pending_ask": self._shared_runtime.has_pending_question(),
            "pending_question_id": self._shared_runtime.current_pending_question_id(),
        }

    @staticmethod
    def _common_send_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "receive_id_type": str(payload.get("receive_id_type") or "open_id"),
            "receive_id": str(payload.get("receive_id") or ""),
            "uuid": str(payload.get("uuid") or "") or None,
        }

    def _run_message_service(self, method_name: str, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(getattr(self._message_service, method_name)(**kwargs))

    def _send_text_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_message_service(
            "send_text",
            **self._common_send_kwargs(payload),
            text=str(payload.get("text") or ""),
        )

    def _send_image_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_message_service(
            "send_image",
            **self._common_send_kwargs(payload),
            image_path=str(payload.get("image_path") or ""),
        )

    def _send_file_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        duration_value = payload.get("duration_ms")
        return self._run_message_service(
            "send_file",
            **self._common_send_kwargs(payload),
            file_path=str(payload.get("file_path") or ""),
            file_type=str(payload.get("file_type") or "stream"),
            file_name=str(payload.get("file_name") or "") or None,
            duration_ms=int(duration_value) if duration_value is not None else None,
        )

    def _send_post_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        content_value = payload.get("content")
        if not isinstance(content_value, list):
            raise ValueError("content must be a JSON array when provided.")
        return self._run_message_service(
            "send_post",
            **self._common_send_kwargs(payload),
            title=str(payload.get("title") or ""),
            content=content_value,
            locale=str(payload.get("locale") or "zh_cn"),
        )

    def _ensure_accepting_asks(self) -> None:
        with self._lifecycle_lock:
            daemon_state = self._daemon_state
            failure_reason = self._failure_reason
        if daemon_state == "serving":
            return
        detail = failure_reason or f"daemon state is {daemon_state}"
        raise RetryableAskError(
            f"Shared daemon is not accepting new asks: {detail}",
            retry_stage="before_send",
        )

    def _handle_terminal_failure(self, exc: BaseException) -> None:
        failure_reason = str(exc).strip() or exc.__class__.__name__
        should_schedule = False
        with self._lifecycle_lock:
            if self._daemon_state in {"terminal_failed", "shutting_down"}:
                return
            self._daemon_state = "terminal_failed"
            self._failure_reason = failure_reason
            should_schedule = True
        if should_schedule:
            self._schedule_retirement()

    def _schedule_retirement(self) -> None:
        with self._lifecycle_lock:
            if self._retirement_thread is not None and self._retirement_thread.is_alive():
                return

            def retire() -> None:
                time.sleep(self._terminal_shutdown_delay_seconds)
                with self._lifecycle_lock:
                    if self._daemon_state != "shutting_down":
                        self._daemon_state = "shutting_down"
                self._server.shutdown()

            self._retirement_thread = threading.Thread(target=retire, daemon=True)
            self._retirement_thread.start()


def run_shared_longconn_daemon(settings: Settings, *, runtime_dir: Path | None = None) -> None:
    app = SharedLongConnDaemonApp(settings, runtime_dir=runtime_dir)
    app.serve_forever()
