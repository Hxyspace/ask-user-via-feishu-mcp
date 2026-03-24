from __future__ import annotations

import logging

from ask_user_via_feishu.config import SERVER_TRANSPORT, Settings
from ask_user_via_feishu.logging_utils import configure_logging
from ask_user_via_feishu.server import create_server



def main() -> None:
    settings = Settings.from_env()
    settings.validate()
    configure_logging(settings.log_level)
    logging.getLogger(__name__).info("Starting ask-user-via-feishu with settings=%s", settings.redacted())
    server = create_server(settings)
    server.run(transport=SERVER_TRANSPORT)
