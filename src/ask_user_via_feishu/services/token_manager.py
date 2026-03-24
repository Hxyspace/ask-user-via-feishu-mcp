from __future__ import annotations

import asyncio
import time

from ask_user_via_feishu.clients.feishu_auth import FeishuAuthClient
from ask_user_via_feishu.config import Settings


class TokenManager:
    def __init__(
        self,
        auth_client: FeishuAuthClient,
        settings: Settings,
        *,
        refresh_buffer_seconds: int = 60,
    ) -> None:
        self._auth_client = auth_client
        self._settings = settings
        self._refresh_buffer_seconds = refresh_buffer_seconds
        self._cached_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        now = time.monotonic()
        if self._cached_token and now < self._expires_at:
            return self._cached_token
        async with self._lock:
            now = time.monotonic()
            if self._cached_token and now < self._expires_at:
                return self._cached_token
            token, expire_seconds = await self._auth_client.get_tenant_access_token(
                self._settings.app_id,
                self._settings.app_secret,
            )
            safe_expire = max(expire_seconds - self._refresh_buffer_seconds, 1)
            self._cached_token = token
            self._expires_at = time.monotonic() + safe_expire
            return token
