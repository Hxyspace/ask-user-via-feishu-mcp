from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ask_user_via_feishu.errors import FeishuAPIError


class FeishuMessageClient:
    def __init__(self, base_url: str, timeout_seconds: int, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def send_message(
        self,
        access_token: str,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: str,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        if uuid:
            payload["uuid"] = uuid
        return await self._request_json(
            "POST",
            f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            access_token=access_token,
            json_body=payload,
        )

    async def upload_image(self, access_token: str, *, image_path: str) -> dict[str, Any]:
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise FeishuAPIError(f"Image file does not exist: {path}")
        files = {
            "image_type": (None, "message"),
            "image": (path.name, path.read_bytes(), "application/octet-stream"),
        }
        return await self._request_json("POST", "/open-apis/im/v1/images", access_token=access_token, files=files)

    async def upload_file(
        self,
        access_token: str,
        *,
        file_path: str,
        file_type: str,
        file_name: str,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FeishuAPIError(f"File does not exist: {path}")
        files: dict[str, Any] = {
            "file_type": (None, file_type),
            "file_name": (None, file_name),
            "file": (file_name, path.read_bytes(), "application/octet-stream"),
        }
        if duration_ms is not None:
            files["duration"] = (None, str(duration_ms))
        return await self._request_json("POST", "/open-apis/im/v1/files", access_token=access_token, files=files)

    async def update_message_card(
        self,
        access_token: str,
        *,
        message_id: str,
        card: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request_json(
            "PATCH",
            f"/open-apis/im/v1/messages/{message_id}",
            access_token=access_token,
            json_body={"content": json.dumps(card, ensure_ascii=False)},
        )

    async def create_message_reaction(
        self,
        access_token: str,
        *,
        message_id: str,
        emoji_type: str,
    ) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/open-apis/im/v1/messages/{message_id}/reactions",
            access_token=access_token,
            json_body={"reaction_type": {"emoji_type": emoji_type}},
        )

    async def delete_message_reaction(
        self,
        access_token: str,
        *,
        message_id: str,
        reaction_id: str,
    ) -> dict[str, Any]:
        return await self._request_json(
            "DELETE",
            f"/open-apis/im/v1/messages/{message_id}/reactions/{reaction_id}",
            access_token=access_token,
        )

    async def download_message_resource(
        self,
        access_token: str,
        *,
        message_id: str,
        file_key: str,
        resource_type: str,
    ) -> dict[str, Any]:
        return await self._request_bytes(
            "GET",
            f"/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type={resource_type}",
            access_token=access_token,
            follow_redirects=True,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.request(method, path, json=json_body, files=files, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FeishuAPIError(
                f"Feishu API request failed: {exc}. Response body: {response.text}",
                status_code=response.status_code,
            ) from exc
        body = response.json()
        if body.get("code") != 0:
            raise FeishuAPIError(
                body.get("msg", "Feishu returned an error."),
                code=body.get("code"),
                status_code=response.status_code,
            )
        return body

    async def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        follow_redirects: bool = False,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            transport=self._transport,
            follow_redirects=follow_redirects,
        ) as client:
            response = await client.request(method, path, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FeishuAPIError(
                f"Feishu API request failed: {exc}. Response body: {response.text}",
                status_code=response.status_code,
            ) from exc
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type.lower():
            body = response.json()
            if body.get("code") != 0:
                raise FeishuAPIError(
                    body.get("msg", "Feishu returned an error."),
                    code=body.get("code"),
                    status_code=response.status_code,
                )
        return {
            "content": response.content,
            "content_type": content_type,
            "content_disposition": response.headers.get("Content-Disposition", ""),
        }
