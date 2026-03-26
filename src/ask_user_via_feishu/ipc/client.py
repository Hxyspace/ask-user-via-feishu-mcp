from __future__ import annotations

from typing import Any

import httpx

from ask_user_via_feishu.ask_runtime import AskWaitOptions
from ask_user_via_feishu.daemon.bootstrap import DaemonConnectionInfo
from ask_user_via_feishu.schemas import FeishuFileType, FeishuPostContent


class DaemonTransportError(RuntimeError):
    """Raised when the local daemon cannot be reached over IPC."""


class SharedLongConnDaemonClient:
    def __init__(self, connection_info: DaemonConnectionInfo) -> None:
        self._base_url = f"http://127.0.0.1:{connection_info.metadata.port}"
        self._headers = {"Authorization": f"Bearer {connection_info.token}"}

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=None, headers=self._headers) as client:
                response = await client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise DaemonTransportError(f"daemon request failed: {exc}") from exc
        data = response.json()
        if response.status_code >= 400:
            error = str(data.get("error") or f"daemon request failed with status {response.status_code}")
            if response.status_code < 500:
                raise ValueError(error)
            raise RuntimeError(error)
        if not data.get("ok"):
            raise RuntimeError(str(data.get("error") or "daemon request failed"))
        return data

    async def ask_and_wait(
        self,
        *,
        question: str,
        choices: list[str] | None,
        uuid: str | None,
        receive_id_type: str,
        receive_id: str,
        client_id: str,
        client_request_id: str,
        wait_options: AskWaitOptions,
    ) -> dict[str, Any]:
        payload = {
            "question": question,
            "choices": list(choices or []),
            "uuid": uuid,
            "receive_id_type": receive_id_type,
            "receive_id": receive_id,
            "client_id": client_id,
            "client_request_id": client_request_id,
            "timeout_seconds": wait_options.timeout_seconds,
            "reminder_max_attempts": wait_options.reminder_max_attempts,
            "timeout_reminder_text": wait_options.timeout_reminder_text,
            "timeout_default_answer": wait_options.timeout_default_answer,
        }
        data = await self._post_json("/v1/ask_and_wait", payload)
        return {
            "ok": bool(data.get("ok")),
            "question_id": str(data.get("question_id") or ""),
            "status": str(data.get("status") or ""),
            "user_answer": str(data.get("user_answer") or ""),
            "downloaded_paths": list(data.get("downloaded_paths") or []),
        }

    async def send_text_message(
        self,
        *,
        text: str,
        uuid: str | None,
        receive_id_type: str,
        receive_id: str,
    ) -> dict[str, Any]:
        return await self._post_json(
            "/v1/send_text_message",
            {
                "text": text,
                "uuid": uuid,
                "receive_id_type": receive_id_type,
                "receive_id": receive_id,
            },
        )

    async def send_image_message(
        self,
        *,
        image_path: str,
        uuid: str | None,
        receive_id_type: str,
        receive_id: str,
    ) -> dict[str, Any]:
        return await self._post_json(
            "/v1/send_image_message",
            {
                "image_path": image_path,
                "uuid": uuid,
                "receive_id_type": receive_id_type,
                "receive_id": receive_id,
            },
        )

    async def send_file_message(
        self,
        *,
        file_path: str,
        file_type: FeishuFileType,
        file_name: str | None,
        duration_ms: int | None,
        uuid: str | None,
        receive_id_type: str,
        receive_id: str,
    ) -> dict[str, Any]:
        return await self._post_json(
            "/v1/send_file_message",
            {
                "file_path": file_path,
                "file_type": file_type,
                "file_name": file_name,
                "duration_ms": duration_ms,
                "uuid": uuid,
                "receive_id_type": receive_id_type,
                "receive_id": receive_id,
            },
        )

    async def send_post_message(
        self,
        *,
        title: str,
        content: FeishuPostContent,
        locale: str,
        uuid: str | None,
        receive_id_type: str,
        receive_id: str,
    ) -> dict[str, Any]:
        return await self._post_json(
            "/v1/send_post_message",
            {
                "title": title,
                "content": content,
                "locale": locale,
                "uuid": uuid,
                "receive_id_type": receive_id_type,
                "receive_id": receive_id,
            },
        )
