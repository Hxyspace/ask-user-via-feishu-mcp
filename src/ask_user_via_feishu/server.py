from __future__ import annotations

import asyncio
import logging
import os
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


def create_server(settings: Settings) -> FastMCP:
    service = build_message_service(settings)
    enabled_mcp_tools = _resolve_enabled_mcp_tools()

    def tool_enabled(name: str) -> bool:
        return name in enabled_mcp_tools

    def _resolve_ask_wait_options() -> AskWaitOptions:
        return build_wait_options(settings)

    def _owner_receive_target() -> tuple[str, str]:
        receive_id = settings.owner_open_id.strip()
        if not receive_id:
            raise ValueError("OWNER_OPEN_ID is required for this owner-only MCP server.")
        return ("open_id", receive_id)

    async def _get_daemon_client() -> SharedLongConnDaemonClient:
        connection_info = await asyncio.to_thread(ensure_daemon_running, settings)
        return SharedLongConnDaemonClient(connection_info)

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

    async def _ask_user_via_feishu_daemon_impl(
        *,
        question: str,
        choices: list[str] | None,
        uuid: str | None,
    ) -> dict[str, Any]:
        question_text = question.strip()
        if not question_text:
            raise ValueError("question must not be empty.")
        wait_options = _resolve_ask_wait_options()
        receive_id_type, receive_id = _owner_receive_target()
        client_request_id = uuid or f"ask_{uuid_lib.uuid4().hex}"
        request_uuid = str(uuid or "").strip() or None
        retry_after_terminal_failure = False
        for attempt in range(2):
            try:
                client = await _get_daemon_client()
                return await client.ask_and_wait(
                    question=question_text,
                    choices=choices,
                    uuid=request_uuid,
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    client_id=f"{SERVER_NAME}:{os.getpid()}",
                    client_request_id=client_request_id if attempt == 0 else f"{client_request_id}:retry{attempt}",
                    wait_options=wait_options,
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

    mcp = FastMCP(SERVER_NAME)

    if tool_enabled("send_text_message"):

        @mcp.tool()
        async def send_text_message(
            text: str,
            uuid: str | None = None,
        ) -> dict[str, Any]:
            """Send a text message to the configured owner."""
            logger.info("Sending text message to configured owner")
            receive_id_type, receive_id = _owner_receive_target()
            await _send_via_daemon_with_fallback(
                operation_name="send_text_message",
                daemon_call=lambda client: client.send_text_message(
                    text=text,
                    uuid=uuid,
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                ),
                local_call=lambda: service.send_text(
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    text=text,
                    uuid=uuid,
                ),
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
            receive_id_type, receive_id = _owner_receive_target()
            await _send_via_daemon_with_fallback(
                operation_name="send_image_message",
                daemon_call=lambda client: client.send_image_message(
                    image_path=image_path,
                    uuid=uuid,
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                ),
                local_call=lambda: service.send_image(
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    image_path=image_path,
                    uuid=uuid,
                ),
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
            receive_id_type, receive_id = _owner_receive_target()
            await _send_via_daemon_with_fallback(
                operation_name="send_file_message",
                daemon_call=lambda client: client.send_file_message(
                    file_path=file_path,
                    file_type=file_type,
                    file_name=file_name,
                    duration_ms=duration_ms,
                    uuid=uuid,
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                ),
                local_call=lambda: service.send_file(
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    file_path=file_path,
                    file_type=file_type,
                    file_name=file_name,
                    duration_ms=duration_ms,
                    uuid=uuid,
                ),
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
            receive_id_type, receive_id = _owner_receive_target()
            await _send_via_daemon_with_fallback(
                operation_name="send_post_message",
                daemon_call=lambda client: client.send_post_message(
                    title=title,
                    content=content,
                    locale=locale,
                    uuid=uuid,
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                ),
                local_call=lambda: service.send_post(
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    title=title,
                    content=content,
                    locale=locale,
                    uuid=uuid,
                ),
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
            return await _ask_user_via_feishu_daemon_impl(
                question=question,
                choices=choices,
                uuid=uuid,
            )

    return mcp
