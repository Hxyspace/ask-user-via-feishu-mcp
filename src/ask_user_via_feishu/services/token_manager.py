from __future__ import annotations

import asyncio
import time
import threading
import weakref

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
        self._lock_registry = weakref.WeakKeyDictionary()
        self._lock_registry_guard = threading.Lock()
        self._state_guard = threading.Lock()

    def _lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._lock_registry_guard:
            lock = self._lock_registry.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._lock_registry[loop] = lock
            return lock

    async def get_token(self) -> str:
        now = time.monotonic()
        with self._state_guard:
            if self._cached_token and now < self._expires_at:
                return self._cached_token
        async with self._lock_for_current_loop():
            now = time.monotonic()
            with self._state_guard:
                if self._cached_token and now < self._expires_at:
                    return self._cached_token
            token, expire_seconds = await self._auth_client.get_tenant_access_token(
                self._settings.app_id,
                self._settings.app_secret,
            )
            safe_expire = max(expire_seconds - self._refresh_buffer_seconds, 1)
            with self._state_guard:
                self._cached_token = token
                self._expires_at = time.monotonic() + safe_expire
            return token
