from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import time
from dataclasses import dataclass
import uuid as uuid_lib
from typing import Any

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.errors import FeishuAPIError, MessageValidationError, RetryableAskError
from ask_user_via_feishu.longconn import LongConnectionSetupError
from ask_user_via_feishu.services.message_service import MessageService
from ask_user_via_feishu.shared_longconn import (
    FeishuSharedLongConnectionRuntime,
    PendingQuestionAborted,
    PendingQuestionTimeout,
)

logger = logging.getLogger(__name__)

ASK_AUTO_RECALL_SENTINEL = "[AUTO_RECALL]"
ASK_AUTO_RECALL_ANSWER = (
    "Retry the same question now. Do not stop, do not summarize, and do not end the conversation. "
    "Keep asking the user until a valid reply is received."
)
ASK_RESOURCES_ONLY_ANSWER = (
    "The user only sent resource files, and the file paths have already been returned. "
    "Review the files and ask a follow-up question now. Do not stop, do not summarize, and do not end "
    "the conversation. Keep waiting until the user provides a valid reply."
)
ASK_LOCAL_FALLBACK_ANSWER = (
    "The shared Feishu ask flow was interrupted twice by daemon long-connection failures. "
    "Switch to local ask_user now. Do not stop, do not summarize, and do not end the conversation."
)


@dataclass(frozen=True)
class AskWaitOptions:
    timeout_seconds: int
    reminder_max_attempts: int
    timeout_reminder_text: str
    timeout_default_answer: str


def build_wait_options(settings: Settings) -> AskWaitOptions:
    timeout_seconds = settings.ask_timeout_seconds
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0.")
    return AskWaitOptions(
        timeout_seconds=timeout_seconds,
        reminder_max_attempts=settings.ask_reminder_max_attempts,
        timeout_reminder_text=settings.ask_timeout_reminder_text.strip(),
        timeout_default_answer=settings.ask_timeout_default_answer.strip(),
    )


def build_ask_user_options_card(*, question_id: str, question: str, choices: list[str]) -> dict[str, Any]:
    use_compact_buttons = any(len(choice) > 10 for choice in choices)
    actions = []
    for index, choice in enumerate(choices, start=1):
        button_text = f"选项{index}" if use_compact_buttons else choice
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": button_text},
                "type": "primary" if index == 1 else "default",
                "value": {
                    "action": "feishu_ask_user_choice",
                    "question_id": question_id,
                    "answer": choice,
                },
            }
        )
    choice_summary = None
    if use_compact_buttons:
        choice_summary = "\n".join(f"{index}. {choice}" for index, choice in enumerate(choices, start=1))
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": question},
        *(
            [{"tag": "markdown", "content": f"**选项说明**\n{choice_summary}"}]
            if choice_summary
            else []
        ),
    ]
    if actions:
        elements.append({"tag": "action", "actions": actions})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": "也可以直接发送文本消息回复。"},
            ],
        }
    )
    return {
        "config": {"update_multi": True, "wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "提问"},
        },
        "elements": elements,
    }


def build_ask_user_answered_card(*, question: str, answer: str) -> dict[str, Any]:
    return {
        "config": {"update_multi": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "已收到回答"},
        },
        "elements": [
            {"tag": "markdown", "content": f"**问题**：{question}\n\n**回答**：{answer}"},
        ],
    }


def build_ask_user_expired_card(*, question: str, notice: str) -> dict[str, Any]:
    return {
        "config": {"update_multi": True},
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": "问题已过期"},
        },
        "elements": [
            {"tag": "markdown", "content": f"**问题**：{question}\n\n**状态**：已过期\n\n**说明**：{notice}"},
        ],
    }


class AskRuntimeOrchestrator:
    def __init__(
        self,
        settings: Settings,
        service: MessageService,
        shared_runtime: FeishuSharedLongConnectionRuntime,
        *,
        download_root: Path | None = None,
    ) -> None:
        self._settings = settings
        self._service = service
        self._shared_runtime = shared_runtime
        self._download_root = None if download_root is None else download_root.expanduser().resolve()
        self._pending_processing_reaction: dict[str, str] | None = None

    async def ask(
        self,
        *,
        question: str,
        choices: list[str] | None,
        uuid: str | None,
        receive_id_type: str,
        receive_id: str,
        wait_options: AskWaitOptions,
        allowed_actor_open_id: str | None = None,
        question_id: str | None = None,
        card: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        question_text = question.strip()
        if not question_text:
            raise ValueError("question must not be empty.")
        resolved_receive_id_type = (receive_id_type or "open_id").strip() or "open_id"
        resolved_receive_id = receive_id.strip()
        if not resolved_receive_id:
            raise ValueError("receive_id must not be empty.")
        target_open_id = (allowed_actor_open_id or self._settings.owner_open_id).strip()
        if not target_open_id:
            raise ValueError("allowed_actor_open_id must not be empty.")
        try:
            self._shared_runtime.ensure_started()
        except LongConnectionSetupError as exc:
            raise RetryableAskError(
                "Shared Feishu long connection is unavailable before sending the question.",
                retry_stage="before_send",
            ) from exc
        await self._best_effort_clear_processing_reaction()
        resolved_question_id = str(question_id or f"ask_{uuid_lib.uuid4().hex[:8]}").strip()
        if not resolved_question_id:
            raise ValueError("question_id must not be empty.")
        normalized_choices = [choice.strip() for choice in (choices or []) if choice and choice.strip()]
        resolved_card = card
        if resolved_card is None:
            resolved_card = build_ask_user_options_card(
                question_id=resolved_question_id,
                question=question_text,
                choices=normalized_choices,
            )
        if not isinstance(resolved_card, dict) or not resolved_card:
            raise ValueError("card must be a non-empty JSON object.")
        question_message_id = ""
        self._shared_runtime.register_pending_question(
            question_id=resolved_question_id,
            target_open_id=target_open_id,
            question=question_text,
            question_message_id=question_message_id,
            reserve_open_id_slot=not resolved_question_id.startswith("select_target_"),
        )
        try:
            send_result = await self._service.send_interactive(
                receive_id_type=resolved_receive_id_type,
                receive_id=resolved_receive_id,
                card=resolved_card,
                uuid=uuid,
            )
            resolved_receive_id = str(send_result.get("receive_id") or resolved_receive_id)
            question_message_id = str(send_result.get("message_id") or "")
            self._shared_runtime.mark_waiting_for_reply(
                resolved_question_id,
                question_message_id=question_message_id,
                sent_at_ms=_resolve_sent_at_ms(send_result),
                target_chat_id=str(send_result.get("chat_id") or ""),
            )

            timeout_attempt = 0
            while True:
                try:
                    wait_result = await asyncio.to_thread(
                        self._shared_runtime.wait_for_question,
                        resolved_question_id,
                        wait_options.timeout_seconds,
                    )
                    break
                except PendingQuestionAborted as exc:
                    if question_message_id:
                        await self._best_effort_update_question_card(
                            message_id=question_message_id,
                            card=build_ask_user_expired_card(
                                question=question_text,
                                notice="共享长连接已中断。该问题已过期，请忽略此卡片；系统会自动重新发起一次提问。",
                            ),
                        )
                    raise RetryableAskError(
                        "Shared Feishu long connection failed while waiting for the reply.",
                        retry_stage="after_send",
                    ) from exc
                except PendingQuestionTimeout:
                    timeout_attempt += 1
                    timeout_result = await self._handle_ask_timeout(
                        question_id=resolved_question_id,
                        question_text=question_text,
                        question_message_id=question_message_id,
                        reminder_receive_id_type=resolved_receive_id_type,
                        reminder_receive_id=resolved_receive_id,
                        wait_options=wait_options,
                        timeout_attempt=timeout_attempt,
                    )
                    if timeout_result.get("wait_continues"):
                        continue
                    return timeout_result

            await self._best_effort_mark_reply_processing(
                reply_message_id=str(wait_result.get("message_id") or ""),
                reply_message_type=str(wait_result.get("message_type") or ""),
            )
            downloaded_paths = await self._service.download_reply_resources(
                question_id=resolved_question_id,
                resource_refs=list(wait_result.get("resource_refs") or []),
                target_root=self._download_root,
            )
            user_answer = str(wait_result.get("text") or "").strip()
            display_answer = str(wait_result.get("display_text") or "").strip()
            if question_message_id:
                card_answer = display_answer or user_answer or ("已收到资源文件" if downloaded_paths else "")
                if not user_answer and downloaded_paths:
                    user_answer = ASK_RESOURCES_ONLY_ANSWER
                await self._best_effort_update_question_card(
                    message_id=question_message_id,
                    card=build_ask_user_answered_card(
                        question=question_text,
                        answer=card_answer,
                    ),
                )
            elif not user_answer and downloaded_paths:
                user_answer = ASK_RESOURCES_ONLY_ANSWER
            result = {
                "ok": True,
                "question_id": resolved_question_id,
                "status": "answered",
                "user_answer": user_answer,
                "downloaded_paths": downloaded_paths,
            }
            card_action = wait_result.get("card_action")
            if isinstance(card_action, dict):
                result["card_action"] = card_action
            return result
        finally:
            self._shared_runtime.unregister_pending_question(resolved_question_id)

    async def _handle_ask_timeout(
        self,
        *,
        question_id: str,
        question_text: str,
        question_message_id: str,
        reminder_receive_id_type: str,
        reminder_receive_id: str,
        wait_options: AskWaitOptions,
        timeout_attempt: int,
    ) -> dict[str, Any]:
        if wait_options.timeout_reminder_text and timeout_attempt <= wait_options.reminder_max_attempts:
            try:
                await self._service.send_text(
                    receive_id_type=reminder_receive_id_type,
                    receive_id=reminder_receive_id,
                    text=wait_options.timeout_reminder_text,
                )
            except (FeishuAPIError, MessageValidationError) as exc:
                logger.warning(
                    "Failed to send timeout reminder question_id=%s attempt=%s: %s",
                    question_id,
                    timeout_attempt,
                    exc,
                )

        reminder_limit_reached = timeout_attempt > wait_options.reminder_max_attempts
        if question_message_id and reminder_limit_reached:
            notice = wait_options.timeout_reminder_text or "未在规定时间内收到回答。"
            if wait_options.timeout_default_answer and wait_options.timeout_default_answer != ASK_AUTO_RECALL_SENTINEL:
                notice = f"未在规定时间内收到回答。系统已按默认回答处理：{wait_options.timeout_default_answer}"
            if wait_options.timeout_default_answer == ASK_AUTO_RECALL_SENTINEL:
                notice = "未在规定时间内收到回答。该问题已过期，系统会要求 LLM 重新发起一次提问。"
            await self._best_effort_update_question_card(
                message_id=question_message_id,
                card=build_ask_user_expired_card(question=question_text, notice=notice),
            )

        if reminder_limit_reached and wait_options.timeout_default_answer == ASK_AUTO_RECALL_SENTINEL:
            return {
                "ok": True,
                "question_id": question_id,
                "status": "answered",
                "user_answer": ASK_AUTO_RECALL_ANSWER,
                "downloaded_paths": [],
            }

        if reminder_limit_reached and wait_options.timeout_default_answer:
            return {
                "ok": True,
                "question_id": question_id,
                "status": "answered",
                "user_answer": wait_options.timeout_default_answer,
                "downloaded_paths": [],
            }

        if reminder_limit_reached:
            return {
                "ok": True,
                "question_id": question_id,
                "status": "timeout",
                "user_answer": "",
                "downloaded_paths": [],
            }

        return {
            "ok": False,
            "wait_continues": True,
        }

    async def _best_effort_update_question_card(self, *, message_id: str, card: dict[str, Any]) -> None:
        try:
            await self._service.update_interactive(message_id=message_id, card=card)
        except (FeishuAPIError, MessageValidationError) as exc:
            logger.warning("Failed to update ask_user card message_id=%s: %s", message_id, exc)

    async def _best_effort_clear_processing_reaction(self) -> None:
        if self._pending_processing_reaction is None:
            return
        try:
            await self._service.delete_reaction(
                message_id=self._pending_processing_reaction["message_id"],
                reaction_id=self._pending_processing_reaction["reaction_id"],
            )
            self._pending_processing_reaction = None
        except (FeishuAPIError, MessageValidationError) as exc:
            logger.warning("Failed to clear processing reaction: %s", exc)

    async def _best_effort_mark_reply_processing(self, *, reply_message_id: str, reply_message_type: str) -> None:
        if not self._settings.reaction_enabled:
            return
        if not reply_message_id.strip() or reply_message_type == "card_action":
            return
        try:
            created = await self._service.create_reaction(
                message_id=reply_message_id,
                emoji_type=self._settings.reaction_emoji_type,
            )
            self._pending_processing_reaction = {
                "message_id": str(created.get("message_id") or reply_message_id),
                "reaction_id": str(created.get("reaction_id") or ""),
            }
        except (FeishuAPIError, MessageValidationError) as exc:
            logger.warning("Failed to mark reply as processing message_id=%s: %s", reply_message_id, exc)


def _resolve_sent_at_ms(send_result: dict[str, Any]) -> int:
    sent_at_ms = int(send_result.get("create_time_ms") or 0)
    if sent_at_ms > 0:
        return sent_at_ms
    return int(time.time() * 1000)
