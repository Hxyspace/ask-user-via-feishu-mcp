from __future__ import annotations

import asyncio
import logging
import uuid as uuid_lib
from typing import Any

from mcp.server.fastmcp import FastMCP

from ask_user_via_feishu.config import SERVER_NAME, Settings
from ask_user_via_feishu.errors import FeishuAPIError, MessageValidationError
from ask_user_via_feishu.runtime import build_event_processor, build_message_service
from ask_user_via_feishu.schemas import FeishuFileType, FeishuPostContent
from ask_user_via_feishu.shared_longconn import FeishuSharedLongConnectionRuntime, PendingQuestionTimeout

logger = logging.getLogger(__name__)

DEFAULT_ENABLED_MCP_TOOLS = {
    "ask_user_via_feishu",
    "send_file_message",
    "send_image_message",
    "send_post_message",
    "send_text_message",
}
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



def _resolve_enabled_mcp_tools() -> set[str]:
    return set(DEFAULT_ENABLED_MCP_TOOLS)



def _build_ask_user_options_card(*, question_id: str, question: str, choices: list[str]) -> dict[str, Any]:
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



def _build_ask_user_answered_card(*, question: str, answer: str) -> dict[str, Any]:
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



def _build_ask_user_expired_card(*, question: str, notice: str) -> dict[str, Any]:
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


def _public_send_result() -> dict[str, Any]:
    return {"ok": True}



def create_server(settings: Settings) -> FastMCP:
    service = build_message_service(settings)
    event_processor = build_event_processor(settings)
    shared_longconn_runtime: FeishuSharedLongConnectionRuntime | None = None
    pending_processing_reaction: dict[str, str] | None = None

    def get_shared_longconn_runtime() -> FeishuSharedLongConnectionRuntime:
        nonlocal shared_longconn_runtime
        if shared_longconn_runtime is None:
            shared_longconn_runtime = FeishuSharedLongConnectionRuntime(settings, event_processor)
        return shared_longconn_runtime

    enabled_mcp_tools = _resolve_enabled_mcp_tools()

    def tool_enabled(name: str) -> bool:
        return name in enabled_mcp_tools

    def _resolve_ask_wait_options() -> tuple[int, int, str, str]:
        timeout_seconds = settings.ask_timeout_seconds
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")
        reminder_max_attempts = settings.ask_reminder_max_attempts
        return (
            timeout_seconds,
            reminder_max_attempts,
            settings.ask_timeout_reminder_text.strip(),
            settings.ask_timeout_default_answer.strip(),
        )

    async def _best_effort_update_question_card(*, message_id: str, card: dict[str, Any]) -> None:
        try:
            await service.update_interactive(message_id=message_id, card=card)
        except (FeishuAPIError, MessageValidationError) as exc:
            logger.warning("Failed to update ask_user card message_id=%s: %s", message_id, exc)

    async def _best_effort_clear_processing_reaction() -> None:
        nonlocal pending_processing_reaction
        if pending_processing_reaction is None:
            return
        try:
            await service.delete_reaction(
                message_id=pending_processing_reaction["message_id"],
                reaction_id=pending_processing_reaction["reaction_id"],
            )
            pending_processing_reaction = None
        except (FeishuAPIError, MessageValidationError) as exc:
            logger.warning("Failed to clear processing reaction: %s", exc)

    async def _best_effort_mark_reply_processing(*, reply_message_id: str, reply_message_type: str) -> None:
        nonlocal pending_processing_reaction
        if not settings.reaction_enabled:
            return
        if not reply_message_id.strip() or reply_message_type == "card_action":
            return
        try:
            created = await service.create_reaction(
                message_id=reply_message_id,
                emoji_type=settings.reaction_emoji_type,
            )
            pending_processing_reaction = {
                "message_id": str(created.get("message_id") or reply_message_id),
                "reaction_id": str(created.get("reaction_id") or ""),
            }
        except (FeishuAPIError, MessageValidationError) as exc:
            logger.warning("Failed to mark reply as processing message_id=%s: %s", reply_message_id, exc)

    async def _handle_ask_timeout(
        *,
        question_id: str,
        question_text: str,
        question_message_id: str,
        target_open_id: str,
        reminder_max_attempts: int,
        timeout_reminder_text: str,
        timeout_default_answer: str,
        timeout_attempt: int,
        ) -> dict[str, Any]:
        if timeout_reminder_text and timeout_attempt <= reminder_max_attempts:
            await service.send_text(
                receive_id_type="open_id",
                receive_id=target_open_id,
                text=timeout_reminder_text,
            )

        reminder_limit_reached = timeout_attempt > reminder_max_attempts
        if question_message_id and reminder_limit_reached:
            notice = timeout_reminder_text or "未在规定时间内收到回答。"
            if timeout_default_answer and timeout_default_answer != ASK_AUTO_RECALL_SENTINEL:
                notice = f"未在规定时间内收到回答。系统已按默认回答处理：{timeout_default_answer}"
            if timeout_default_answer == ASK_AUTO_RECALL_SENTINEL:
                notice = "未在规定时间内收到回答。该问题已过期，系统会要求 LLM 重新发起一次提问。"
            await _best_effort_update_question_card(
                message_id=question_message_id,
                card=_build_ask_user_expired_card(question=question_text, notice=notice),
            )

        if reminder_limit_reached and timeout_default_answer == ASK_AUTO_RECALL_SENTINEL:
            return {
                "ok": True,
                "question_id": question_id,
                "status": "answered",
                "user_answer": ASK_AUTO_RECALL_ANSWER,
                "downloaded_paths": [],
            }

        if reminder_limit_reached and timeout_default_answer:
            return {
                "ok": True,
                "question_id": question_id,
                "status": "answered",
                "user_answer": timeout_default_answer,
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

    async def _ask_user_via_feishu_shared_runtime_impl(
        *,
        question: str,
        choices: list[str] | None,
        uuid: str | None,
    ) -> dict[str, Any]:
        question_text = question.strip()
        if not question_text:
            raise ValueError("question must not be empty.")
        owner_open_id = settings.owner_open_id.strip()
        if not owner_open_id:
            raise ValueError("owner_open_id must be configured for owner-only ask_user_via_feishu.")
        target_open_id = owner_open_id
        timeout_seconds, reminder_max_attempts, reminder_text, default_answer = _resolve_ask_wait_options()
        shared_runtime = get_shared_longconn_runtime()
        shared_runtime.ensure_started()
        await _best_effort_clear_processing_reaction()
        question_id = f"ask_{uuid_lib.uuid4().hex[:8]}"
        normalized_choices = [choice.strip() for choice in (choices or []) if choice and choice.strip()]
        question_message_id = ""
        shared_runtime.register_pending_question(
            question_id=question_id,
            target_open_id=target_open_id,
            question=question_text,
            question_message_id=question_message_id,
        )
        try:
            send_result = await service.send_interactive(
                receive_id_type="open_id",
                receive_id=target_open_id,
                card=_build_ask_user_options_card(
                    question_id=question_id,
                    question=question_text,
                    choices=normalized_choices,
                ),
                uuid=uuid,
            )
            target_open_id = str(send_result.get("receive_id") or target_open_id)
            question_message_id = str(send_result.get("message_id") or "")

            timeout_attempt = 0
            while True:
                try:
                    wait_result = await asyncio.to_thread(shared_runtime.wait_for_question, question_id, timeout_seconds)
                    break
                except PendingQuestionTimeout:
                    timeout_attempt += 1
                    timeout_result = await _handle_ask_timeout(
                        question_id=question_id,
                        question_text=question_text,
                        question_message_id=question_message_id,
                        target_open_id=target_open_id,
                        reminder_max_attempts=reminder_max_attempts,
                        timeout_reminder_text=reminder_text,
                        timeout_default_answer=default_answer,
                        timeout_attempt=timeout_attempt,
                    )
                    if timeout_result.get("wait_continues"):
                        continue
                    return timeout_result

            await _best_effort_mark_reply_processing(
                reply_message_id=str(wait_result.get("message_id") or ""),
                reply_message_type=str(wait_result.get("message_type") or ""),
            )
            if question_message_id:
                downloaded_paths = await service.download_reply_resources(
                    question_id=question_id,
                    resource_refs=list(wait_result.get("resource_refs") or []),
                )
                user_answer = str(wait_result.get("text") or "").strip()
                card_answer = user_answer or ("已收到资源文件" if downloaded_paths else "")
                if not user_answer and downloaded_paths:
                    user_answer = ASK_RESOURCES_ONLY_ANSWER
                await _best_effort_update_question_card(
                    message_id=question_message_id,
                    card=_build_ask_user_answered_card(
                        question=question_text,
                        answer=card_answer,
                    ),
                )
            else:
                downloaded_paths = await service.download_reply_resources(
                    question_id=question_id,
                    resource_refs=list(wait_result.get("resource_refs") or []),
                )
                user_answer = str(wait_result.get("text") or "").strip()
                if not user_answer and downloaded_paths:
                    user_answer = ASK_RESOURCES_ONLY_ANSWER
            return {
                "ok": True,
                "question_id": question_id,
                "status": "answered",
                "user_answer": user_answer,
                "downloaded_paths": downloaded_paths,
            }
        finally:
            shared_runtime.unregister_pending_question(question_id)

    mcp = FastMCP(SERVER_NAME)

    if tool_enabled("send_text_message"):

        @mcp.tool()
        async def send_text_message(
            text: str,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a text message to the configured owner."""
            logger.info("Sending text message to configured owner")
            await service.send_text(
                receive_id_type="open_id",
                receive_id="",
                text=text,
                uuid=uuid,
            )
            return _public_send_result()

    if tool_enabled("send_image_message"):

        @mcp.tool()
        async def send_image_message(
            image_path: str,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send an image message to the configured owner."""
            logger.info("Sending image message to configured owner")
            await service.send_image(
                receive_id_type="open_id",
                receive_id="",
                image_path=image_path,
                uuid=uuid,
            )
            return _public_send_result()

    if tool_enabled("send_file_message"):

        @mcp.tool()
        async def send_file_message(
            file_path: str,
            file_type: FeishuFileType = "stream",
            file_name: str | None = None,
            duration_ms: int | None = None,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a file message to the configured owner. `file_type` must be one of opus, mp4, pdf, doc, xls, ppt, stream; use `stream` for other file types."""
            logger.info("Sending file message to configured owner")
            await service.send_file(
                receive_id_type="open_id",
                receive_id="",
                file_path=file_path,
                file_type=file_type,
                file_name=file_name,
                duration_ms=duration_ms,
                uuid=uuid,
            )
            return _public_send_result()

    if tool_enabled("send_post_message"):

        @mcp.tool()
        async def send_post_message(
            title: str,
            content: FeishuPostContent,
            locale: str = "zh_cn",
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a Feishu post rich-text message to the configured owner using paragraph arrays of text, a, at, or img elements."""
            logger.info("Sending post message to configured owner")
            await service.send_post(
                receive_id_type="open_id",
                receive_id="",
                title=title,
                content=content,
                locale=locale,
                uuid=uuid,
            )
            return _public_send_result()

    if tool_enabled("ask_user_via_feishu"):

        @mcp.tool()
        async def ask_user_via_feishu(
            question: str,
            choices: list[str] | None = None,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a question to the owner, optionally with button choices, then wait for reply or selection."""
            return await _ask_user_via_feishu_shared_runtime_impl(
                question=question,
                choices=choices,
                uuid=uuid,
            )

    return mcp
