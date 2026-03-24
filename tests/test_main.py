from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ask_user_via_feishu.config import SERVER_TRANSPORT
from ask_user_via_feishu.main import main


class MainTest(unittest.TestCase):
    def test_main_validates_settings_before_starting_server(self) -> None:
        fake_settings = Mock()
        fake_settings.log_level = "INFO"
        fake_settings.redacted.return_value = {"app_secret": "***"}
        fake_server = Mock()

        with (
            patch("ask_user_via_feishu.main.Settings.from_env", return_value=fake_settings),
            patch("ask_user_via_feishu.main.configure_logging"),
            patch("ask_user_via_feishu.main.create_server", return_value=fake_server),
        ):
            main()

        fake_settings.validate.assert_called_once_with()
        fake_server.run.assert_called_once_with(transport=SERVER_TRANSPORT)
