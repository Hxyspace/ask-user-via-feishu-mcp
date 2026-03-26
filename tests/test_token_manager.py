from __future__ import annotations

import asyncio
import unittest

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.services.token_manager import TokenManager

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class FakeAuthClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_tenant_access_token(self, app_id: str, app_secret: str) -> tuple[str, int]:
        self.calls += 1
        return (f"tenant_token_{self.calls}", 7200)


class TokenManagerTest(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
                "OWNER_OPEN_ID": "ou_owner",
                "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
            }
        )

    def test_get_token_can_refresh_across_different_event_loops(self) -> None:
        auth_client = FakeAuthClient()
        manager = TokenManager(auth_client, self._settings())

        first = asyncio.run(manager.get_token())
        manager._expires_at = 0.0
        second = asyncio.run(manager.get_token())

        self.assertEqual(first, "tenant_token_1")
        self.assertEqual(second, "tenant_token_2")
        self.assertEqual(auth_client.calls, 2)

    def test_get_token_reuses_unexpired_token_in_memory(self) -> None:
        auth_client = FakeAuthClient()
        manager = TokenManager(auth_client, self._settings())

        first = asyncio.run(manager.get_token())
        second = asyncio.run(manager.get_token())

        self.assertEqual(first, "tenant_token_1")
        self.assertEqual(second, "tenant_token_1")
        self.assertEqual(auth_client.calls, 1)
