from __future__ import annotations

import asyncio
import logging
import uuid as uuid_lib
from typing import Any

from mcp.server.fastmcp import FastMCP

from ask_user_via_feishu.ask_runtime import (
    ASK_AUTO_RECALL_ANSWER,
    ASK_LOCAL_FALLBACK_ANSWER,
    ASK_RESOURCES_ONLY_ANSWER,
    AskWaitOptions,
    build_ask_user_options_card as _build_ask_user_options_card,
    build_wait_options,
)
from ask_user_via_feishu.config import SERVER_NAME, Settings
from ask_user_via_feishu.daemon.bootstrap import DaemonBootstrapError, ensure_daemon_running
from ask_user_via_feishu.ipc.client import (
    DaemonAskRetryableError,
    DaemonTransportError,
    SharedLongConnDaemonClient,
)
from ask_user_via_feishu.runtime import build_message_service
from ask_user_via_feishu.schemas import FeishuFileType, FeishuPostContent

logger = logging.getLogger(__name__)

SELECT_TARGET_NEW_CHAT_FIELD = "new_chat_name"

DEFAULT_ENABLED_MCP_TOOLS = {
    "ask_user_via_feishu",
    "send_file_message",
    "send_image_message",
    "send_post_message",
    "send_text_message",
}


def _resolve_enabled_mcp_tools() -> set[str]:
    return set(DEFAULT_ENABLED_MCP_TOOLS)


def _public_send_result() -> dict[str, Any]:
    return {"ok": True}


def _public_ask_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "question_id": str(result.get("question_id") or ""),
        "status": str(result.get("status") or ""),
        "user_answer": str(result.get("user_answer") or ""),
        "downloaded_paths": list(result.get("downloaded_paths") or []),
    }


def _build_retry_uuid(uuid: str | None, *, retry_stage: str) -> str | None:
    normalized = str(uuid or "").strip() or None
    if retry_stage != "after_send" or normalized is None:
        return normalized
    return f"{normalized}_retry_{uuid_lib.uuid4().hex[:8]}"


def _local_ask_fallback_result() -> dict[str, Any]:
    return {
        "ok": True,
        "question_id": "",
        "status": "answered",
        "user_answer": ASK_LOCAL_FALLBACK_ANSWER,
        "downloaded_paths": [],
    }


def _configured_chat_target(settings: Settings) -> dict[str, str] | None:
    chat_id = settings.chat_id.strip()
    if not chat_id:
        return None
    return {
        "receive_id_type": "chat_id",
        "receive_id": chat_id,
    }


def _build_target_selection_card(
    *,
    question_id: str,
    candidate_chats: list[dict[str, str]],
) -> dict[str, Any]:
    current_conversation_actions: list[dict[str, Any]] = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "当前会话"},
            "type": "primary",
            "value": {
                "action": "feishu_select_chat_target",
                "question_id": question_id,
                "selection_kind": "current_conversation",
            },
        }
    ]
    existing_chat_actions: list[dict[str, Any]] = []
    for chat in candidate_chats:
        chat_id = str(chat.get("chat_id") or "").strip()
        chat_name = str(chat.get("name") or "").strip()
        if not chat_id:
            continue
        existing_chat_actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": chat_name or chat_id},
                "type": "default",
                "value": {
                    "action": "feishu_select_chat_target",
                    "question_id": question_id,
                    "selection_kind": "existing_chat",
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                },
            }
        )
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "请选择后续消息发送与提问所使用的飞书会话：",
            },
        },
        {"tag": "action", "actions": current_conversation_actions},
    ]
    if existing_chat_actions:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**现有群聊**\n选择一个群聊作为后续交流会话：",
                },
            }
        )
        elements.append({"tag": "action", "actions": existing_chat_actions})
    else:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**现有群聊**\n当前没有发现可直接切换的群聊。",
                },
            }
        )
    elements.extend(
        [
            {
                "tag": "form",
                "name": "select_target_new_chat_form",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "**新建群聊**\n输入群名并提交：",
                    },
                    {
                        "tag": "input",
                        "name": SELECT_TARGET_NEW_CHAT_FIELD,
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "例如：project-alpha",
                        },
                    },
                    {
                        "tag": "button",
                        "name": f"select_target_new_chat_submit_{question_id}",
                        "text": {"tag": "plain_text", "content": "提交"},
                        "type": "primary",
                        "action_type": "form_submit",
                        "value": {
                            "action": "feishu_select_chat_target",
                            "question_id": question_id,
                            "selection_kind": "new_chat",
                        },
                    },
                ],
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "该选择只保存在当前 MCP 进程内存中，重启后需要重新选择；若配置了 CHAT_ID，则优先使用配置值。",
                    }
                ],
            },
        ]
    )
    return {
        "config": {"update_multi": True, "wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "选择会话"},
        },
        "elements": elements,
    }


def create_server(settings: Settings) -> FastMCP:
    service = build_message_service(settings)
    enabled_mcp_tools = _resolve_enabled_mcp_tools()
    selected_target = _configured_chat_target(settings)
    target_lock = asyncio.Lock()

    def tool_enabled(name: str) -> bool:
        return name in enabled_mcp_tools

    def _resolve_ask_wait_options() -> AskWaitOptions:
        return build_wait_options(settings)

    def _owner_receive_target() -> dict[str, str]:
        receive_id = settings.owner_open_id.strip()
        if not receive_id:
            raise ValueError("OWNER_OPEN_ID is required for this owner-only MCP server.")
        return {
            "receive_id_type": "open_id",
            "receive_id": receive_id,
        }

    async def _get_daemon_client() -> SharedLongConnDaemonClient:
        connection_info = await asyncio.to_thread(ensure_daemon_running, settings)
        return SharedLongConnDaemonClient(connection_info)

    async def _daemon_ask_impl(
        *,
        question: str,
        choices: list[str] | None,
        uuid: str | None,
        receive_id_type: str,
        receive_id: str,
        allowed_actor_open_id: str | None = None,
        wait_options: AskWaitOptions | None = None,
        question_id: str | None = None,
        card: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        question_text = question.strip()
        if not question_text:
            raise ValueError("question must not be empty.")
        resolved_wait_options = wait_options or _resolve_ask_wait_options()
        resolved_receive_id_type = str(receive_id_type or "open_id").strip() or "open_id"
        resolved_receive_id = str(receive_id or "").strip()
        if not resolved_receive_id:
            raise ValueError("receive_id must not be empty.")
        resolved_allowed_actor_open_id = str(allowed_actor_open_id or settings.owner_open_id).strip()
        if not resolved_allowed_actor_open_id:
            raise ValueError("allowed_actor_open_id must not be empty.")
        request_uuid = str(uuid or "").strip() or None
        retry_after_terminal_failure = False
        for attempt in range(2):
            try:
                client = await _get_daemon_client()
                return await client.ask_and_wait(
                    question=question_text,
                    choices=choices,
                    uuid=request_uuid,
                    receive_id_type=resolved_receive_id_type,
                    receive_id=resolved_receive_id,
                    wait_options=resolved_wait_options,
                    allowed_actor_open_id=resolved_allowed_actor_open_id,
                    question_id=question_id,
                    card=card,
                )
            except DaemonAskRetryableError as exc:
                retry_after_terminal_failure = True
                if attempt == 0:
                    logger.warning(
                        "Shared daemon ask interrupted at stage=%s; retrying once on a fresh daemon.",
                        exc.retry_stage,
                    )
                    request_uuid = _build_retry_uuid(request_uuid, retry_stage=exc.retry_stage)
                    continue
                logger.warning(
                    "Shared daemon ask interrupted again at stage=%s; returning local ask fallback.",
                    exc.retry_stage,
                )
                return _local_ask_fallback_result()
            except (DaemonBootstrapError, DaemonTransportError) as exc:
                if retry_after_terminal_failure:
                    logger.warning(
                        "Shared daemon ask retry could not reach a fresh daemon; returning local ask fallback: %s",
                        exc,
                    )
                    return _local_ask_fallback_result()
                raise
        return _local_ask_fallback_result()

    async def _resolve_bootstrap_target(result: dict[str, Any], *, candidate_chats: list[dict[str, str]]) -> dict[str, str]:
        candidate_by_id = {
            str(chat.get("chat_id") or "").strip(): chat
            for chat in candidate_chats
            if str(chat.get("chat_id") or "").strip()
        }
        card_action = result.get("card_action")
        if isinstance(card_action, dict):
            action = str(card_action.get("action") or "").strip()
            action_value = card_action.get("value")
            if action == "feishu_select_chat_target" and isinstance(action_value, dict):
                selection_kind = str(action_value.get("selection_kind") or "").strip()
                if selection_kind == "current_conversation":
                    return _owner_receive_target()
                if selection_kind == "existing_chat":
                    chat_id = str(action_value.get("chat_id") or "").strip()
                    chat = candidate_by_id.get(chat_id)
                    if chat is None:
                        raise ValueError("Selected chat is no longer available.")
                    return {
                        "receive_id_type": "chat_id",
                        "receive_id": chat_id,
                    }
                if selection_kind == "new_chat":
                    chat_name = str(result.get("user_answer") or "").strip()
                    created = await service.create_owner_chat(name=chat_name, uuid=None)
                    return {
                        "receive_id_type": "chat_id",
                        "receive_id": str(created.get("chat_id") or "").strip(),
                    }
        raise ValueError("Target selection must be submitted from the selection card.")

    async def _bootstrap_target_selection() -> dict[str, str]:
        owner_target = _owner_receive_target()
        candidate_chats = await service.list_owner_chats()
        selection_question_id = f"select_target_{uuid_lib.uuid4().hex[:8]}"
        wait_options = _resolve_ask_wait_options()
        bootstrap_wait_options = AskWaitOptions(
            timeout_seconds=wait_options.timeout_seconds,
            reminder_max_attempts=wait_options.reminder_max_attempts,
            timeout_reminder_text=wait_options.timeout_reminder_text,
            timeout_default_answer="",
        )
        raw_result = await _daemon_ask_impl(
            question="请选择后续飞书会话",
            choices=None,
            uuid=f"select_target_{uuid_lib.uuid4().hex}",
            receive_id_type=owner_target["receive_id_type"],
            receive_id=owner_target["receive_id"],
            wait_options=bootstrap_wait_options,
            question_id=selection_question_id,
            card=_build_target_selection_card(
                question_id=selection_question_id,
                candidate_chats=candidate_chats,
            ),
        )
        if str(raw_result.get("user_answer") or "").strip() == ASK_LOCAL_FALLBACK_ANSWER:
            raise RuntimeError("Feishu chat target selection requires the shared daemon to stay available.")
        if str(raw_result.get("status") or "").strip() != "answered":
            raise RuntimeError("Feishu chat target selection timed out.")
        resolved = await _resolve_bootstrap_target(raw_result, candidate_chats=candidate_chats)
        logger.info(
            "Resolved active Feishu target receive_id_type=%s receive_id=%s",
            resolved.get("receive_id_type"),
            resolved.get("receive_id"),
        )
        return resolved

    async def _resolve_active_target() -> dict[str, str]:
        nonlocal selected_target
        if selected_target is not None:
            return dict(selected_target)
        async with target_lock:
            if selected_target is None:
                selected_target = await _bootstrap_target_selection()
            return dict(selected_target)

    async def _send_via_daemon_with_fallback(
        *,
        operation_name: str,
        daemon_call: Any,
        local_call: Any,
    ) -> None:
        try:
            client = await _get_daemon_client()
            await daemon_call(client)
            return
        except (DaemonBootstrapError, DaemonTransportError) as exc:
            logger.warning(
                "Shared daemon unavailable for %s; falling back to direct send: %s",
                operation_name,
                exc,
            )
        await local_call()

    async def _send_owner_message(
        *,
        operation_name: str,
        log_message: str,
        daemon_method_name: str,
        local_method_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        logger.info(log_message)
        target = await _resolve_active_target()
        resolved_payload = {
            **payload,
            "receive_id_type": target["receive_id_type"],
            "receive_id": target["receive_id"],
        }
        await _send_via_daemon_with_fallback(
            operation_name=operation_name,
            daemon_call=lambda client: getattr(client, daemon_method_name)(**resolved_payload),
            local_call=lambda: getattr(service, local_method_name)(**resolved_payload),
        )
        return _public_send_result()

    async def _ask_user_via_feishu_daemon_impl(
        *,
        question: str,
        choices: list[str] | None,
        uuid: str | None,
    ) -> dict[str, Any]:
        target = await _resolve_active_target()
        raw_result = await _daemon_ask_impl(
            question=question,
            choices=choices,
            uuid=uuid,
            receive_id_type=target["receive_id_type"],
            receive_id=target["receive_id"],
            wait_options=_resolve_ask_wait_options(),
        )
        return _public_ask_result(raw_result)

    mcp = FastMCP(SERVER_NAME)

    if tool_enabled("send_text_message"):

        @mcp.tool()
        async def send_text_message(
            text: str,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a text message to the active Feishu target."""
            return await _send_owner_message(
                operation_name="send_text_message",
                log_message="Sending text message to configured owner",
                daemon_method_name="send_text_message",
                local_method_name="send_text",
                payload={
                    "text": text,
                    "uuid": uuid,
                },
            )

    if tool_enabled("send_image_message"):

        @mcp.tool()
        async def send_image_message(
            image_path: str,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send an image message to the active Feishu target."""
            return await _send_owner_message(
                operation_name="send_image_message",
                log_message="Sending image message to configured owner",
                daemon_method_name="send_image_message",
                local_method_name="send_image",
                payload={
                    "image_path": image_path,
                    "uuid": uuid,
                },
            )

    if tool_enabled("send_file_message"):

        @mcp.tool()
        async def send_file_message(
            file_path: str,
            file_type: FeishuFileType = "stream",
            file_name: str | None = None,
            duration_ms: int | None = None,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a file message to the active Feishu target. `file_type` must be one of opus, mp4, pdf, doc, xls, ppt, stream; use `stream` for other file types."""
            return await _send_owner_message(
                operation_name="send_file_message",
                log_message="Sending file message to configured owner",
                daemon_method_name="send_file_message",
                local_method_name="send_file",
                payload={
                    "file_path": file_path,
                    "file_type": file_type,
                    "file_name": file_name,
                    "duration_ms": duration_ms,
                    "uuid": uuid,
                },
            )

    if tool_enabled("send_post_message"):

        @mcp.tool()
        async def send_post_message(
            title: str,
            content: FeishuPostContent,
            locale: str = "zh_cn",
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a Feishu post rich-text message to the active Feishu target using official text, a, at, img, media, emotion, hr, code_block, or md nodes."""
            return await _send_owner_message(
                operation_name="send_post_message",
                log_message="Sending post message to configured owner",
                daemon_method_name="send_post_message",
                local_method_name="send_post",
                payload={
                    "title": title,
                    "content": content,
                    "locale": locale,
                    "uuid": uuid,
                },
            )

    if tool_enabled("ask_user_via_feishu"):

        @mcp.tool()
        async def ask_user_via_feishu(
            question: str,
            choices: list[str] | None = None,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a question to the active Feishu target, optionally with button choices, then wait for the owner reply or selection."""
            return await _ask_user_via_feishu_daemon_impl(
                question=question,
                choices=choices,
                uuid=uuid,
            )

    return mcp
