from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ask_user_via_feishu.config import Settings

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class SettingsTest(unittest.TestCase):
    def test_defaults_follow_slimmed_config_surface(self) -> None:
        settings = Settings.from_env({
            "APP_ID": "cli_123",
            "APP_SECRET": "secret_123",
            "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
        })
        self.assertEqual(settings.ask_timeout_seconds, 600)
        self.assertEqual(settings.ask_timeout_reminder_text, "请及时回复！！！")
        self.assertTrue(settings.reaction_enabled)

    def test_runtime_config_populates_owner_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'config.json'
            config_path.write_text(
                json.dumps(
                    {
                        "app_id": "cli_cfg",
                        "app_secret": "secret_cfg",
                        "owner_open_id": "ou_owner",
                        "ask": {"timeout_seconds": 120},
                    }
                ),
                encoding='utf-8',
            )
            settings = Settings.from_env({"RUNTIME_CONFIG_PATH": str(config_path)})
        self.assertEqual(settings.app_id, "cli_cfg")
        self.assertEqual(settings.app_secret, "secret_cfg")
        self.assertEqual(settings.owner_open_id, "ou_owner")
        self.assertEqual(settings.ask_timeout_seconds, 120)

    def test_empty_timeout_default_answer_env_is_preserved(self) -> None:
        settings = Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
                "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
                "ASK_TIMEOUT_DEFAULT_ANSWER": "",
            }
        )
        self.assertEqual(settings.ask_timeout_default_answer, "")

    def test_validate_requires_owner_open_id(self) -> None:
        settings = Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
            }
        )

        with self.assertRaisesRegex(ValueError, "OWNER_OPEN_ID is required"):
            settings.validate()

    def test_validate_allows_missing_explicit_runtime_config_path(self) -> None:
        settings = Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
                "OWNER_OPEN_ID": "ou_owner",
                "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
            }
        )

        settings.validate()

    def test_validate_allows_zero_reminder_attempts(self) -> None:
        settings = Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
                "OWNER_OPEN_ID": "ou_owner",
                "ASK_REMINDER_MAX_ATTEMPTS": "0",
            }
        )

        settings.validate()
