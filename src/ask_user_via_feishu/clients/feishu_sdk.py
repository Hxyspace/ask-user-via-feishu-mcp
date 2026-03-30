from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi.api.auth.v3 import (
    InternalTenantAccessTokenRequest,
    InternalTenantAccessTokenRequestBody,
)
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
    Emoji,
    GetMessageResourceRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
)

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.errors import FeishuAPIError


class FeishuSDKClient:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or self._build_client(settings)

    async def health_check(self) -> None:
        request = InternalTenantAccessTokenRequest.builder().request_body(
            InternalTenantAccessTokenRequestBody.builder()
            .app_id(self._settings.app_id)
            .app_secret(self._settings.app_secret)
            .build()
        ).build()
        response = await self._client.auth.v3.tenant_access_token.ainternal(request)
        self._ensure_success(response, operation_name="tenant_access_token.internal")

    async def send_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: str,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        body_builder = (
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type(msg_type)
            .content(content)
        )
        if uuid:
            body_builder = body_builder.uuid(uuid)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body_builder.build())
            .build()
        )
        response = await self._client.im.v1.message.acreate(request)
        self._ensure_success(response, operation_name="im.message.create")
        data = getattr(response, "data", None)
        return {
            "code": 0,
            "data": {
                "message_id": str(getattr(data, "message_id", "") or ""),
                "chat_id": str(getattr(data, "chat_id", "") or ""),
                "create_time": getattr(data, "create_time", 0) or 0,
            },
        }

    async def upload_image(self, *, image_path: str) -> dict[str, Any]:
        path = Path(image_path).expanduser().resolve()
        with open(path, "rb") as image_file:
            request = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder().image_type("message").image(image_file).build()
            ).build()
            response = await self._client.im.v1.image.acreate(request)
        self._ensure_success(response, operation_name="im.image.create")
        data = getattr(response, "data", None)
        return {
            "code": 0,
            "data": {
                "image_key": str(getattr(data, "image_key", "") or ""),
            },
        }

    async def upload_file(
        self,
        *,
        file_path: str,
        file_type: str,
        file_name: str,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path).expanduser().resolve()
        body_builder = (
            CreateFileRequestBody.builder()
            .file_type(file_type)
            .file_name(file_name)
        )
        if duration_ms is not None:
            body_builder = body_builder.duration(duration_ms)
        with open(path, "rb") as file_handle:
            request = CreateFileRequest.builder().request_body(
                body_builder.file(file_handle).build()
            ).build()
            response = await self._client.im.v1.file.acreate(request)
        self._ensure_success(response, operation_name="im.file.create")
        data = getattr(response, "data", None)
        return {
            "code": 0,
            "data": {
                "file_key": str(getattr(data, "file_key", "") or ""),
            },
        }

    async def update_message_card(self, *, message_id: str, card: dict[str, Any]) -> dict[str, Any]:
        request = PatchMessageRequest.builder().message_id(message_id).request_body(
            PatchMessageRequestBody.builder().content(json.dumps(card, ensure_ascii=False)).build()
        ).build()
        response = await self._client.im.v1.message.apatch(request)
        self._ensure_success(response, operation_name="im.message.patch")
        return {"code": 0, "data": {"message_id": message_id}}

    async def create_message_reaction(self, *, message_id: str, emoji_type: str) -> dict[str, Any]:
        request = CreateMessageReactionRequest.builder().message_id(message_id).request_body(
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
            .build()
        ).build()
        response = await self._client.im.v1.message_reaction.acreate(request)
        self._ensure_success(response, operation_name="im.message_reaction.create")
        data = getattr(response, "data", None)
        return {
            "code": 0,
            "data": {
                "reaction_id": str(getattr(data, "reaction_id", "") or ""),
            },
        }

    async def delete_message_reaction(self, *, message_id: str, reaction_id: str) -> dict[str, Any]:
        request = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        response = await self._client.im.v1.message_reaction.adelete(request)
        self._ensure_success(response, operation_name="im.message_reaction.delete")
        return {"code": 0, "data": {}}

    async def download_message_resource(
        self,
        *,
        message_id: str,
        file_key: str,
        resource_type: str,
    ) -> dict[str, Any]:
        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type(resource_type)
            .build()
        )
        response = await self._client.im.v1.message_resource.aget(request)
        self._ensure_success(response, operation_name="im.message_resource.get")
        file_stream = getattr(response, "file", None)
        if file_stream is None:
            raise FeishuAPIError("Message resource download did not return a file stream.")
        raw_headers = getattr(getattr(response, "raw", None), "headers", {}) or {}
        return {
            "content_stream": self._prepare_file_stream(file_stream),
            "content_type": str(raw_headers.get("Content-Type") or raw_headers.get("content-type") or ""),
            "content_disposition": str(
                raw_headers.get("Content-Disposition") or raw_headers.get("content-disposition") or ""
            ),
        }

    @staticmethod
    def _build_client(settings: Settings) -> lark.Client:
        log_level = getattr(lark.LogLevel, settings.log_level.upper(), None) or getattr(lark.LogLevel, "INFO", None)
        builder = (
            lark.Client.builder()
            .app_id(settings.app_id)
            .app_secret(settings.app_secret)
            .domain(settings.base_url)
            .timeout(float(settings.api_timeout_seconds))
        )
        if log_level is not None:
            builder = builder.log_level(log_level)
        return builder.build()

    @staticmethod
    def _ensure_success(response: Any, *, operation_name: str) -> None:
        if bool(response.success()):
            return
        raw = getattr(response, "raw", None)
        status_code = getattr(raw, "status_code", None)
        raise FeishuAPIError(
            str(getattr(response, "msg", "") or f"{operation_name} failed."),
            code=getattr(response, "code", None),
            status_code=int(status_code) if status_code is not None else None,
        )

    @staticmethod
    def _prepare_file_stream(file_stream: Any) -> Any:
        if isinstance(file_stream, (bytes, bytearray, memoryview)):
            return io.BytesIO(bytes(file_stream))
        if hasattr(file_stream, "seek"):
            try:
                file_stream.seek(0)
            except (OSError, ValueError):
                pass
        if hasattr(file_stream, "read"):
            return file_stream
        raise FeishuAPIError("Message resource download returned an unreadable file stream.")
