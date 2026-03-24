from __future__ import annotations

import httpx

from ask_user_via_feishu.errors import FeishuAPIError, FeishuAuthError


class FeishuAuthClient:
    def __init__(self, base_url: str, timeout_seconds: int, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def get_tenant_access_token(self, app_id: str, app_secret: str) -> tuple[str, int]:
        payload = {"app_id": app_id, "app_secret": app_secret}
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.post(
                "/open-apis/auth/v3/tenant_access_token/internal",
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FeishuAuthError(f"Failed to fetch tenant access token: {exc}") from exc

        body = response.json()
        if body.get("code") != 0:
            raise FeishuAPIError(
                body.get("msg", "Failed to fetch tenant access token."),
                code=body.get("code"),
                status_code=response.status_code,
            )

        access_token = body.get("tenant_access_token")
        expire = body.get("expire")
        if not access_token or not isinstance(expire, int):
            raise FeishuAuthError("Feishu auth response did not contain a valid token.")
        return str(access_token), expire
