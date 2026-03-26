from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from ask_user_via_feishu.config import SERVER_TRANSPORT, Settings
from ask_user_via_feishu.daemon.app import run_shared_longconn_daemon
from ask_user_via_feishu.logging_utils import configure_logging
from ask_user_via_feishu.server import create_server



def main(argv: Sequence[str] | None = None) -> None:
    parsed_args = _parse_args(argv)
    settings = Settings.from_env()
    settings.validate()
    configure_logging(settings.log_level)
    logging.getLogger(__name__).info("Starting ask-user-via-feishu with settings=%s", settings.redacted())
    if parsed_args.shared_longconn_daemon:
        runtime_dir = Path(parsed_args.daemon_runtime_dir).expanduser().resolve() if parsed_args.daemon_runtime_dir else None
        run_shared_longconn_daemon(settings, runtime_dir=runtime_dir)
        return
    server = create_server(settings)
    server.run(transport=SERVER_TRANSPORT)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--shared-longconn-daemon", action="store_true")
    parser.add_argument("--daemon-runtime-dir", default="")
    parsed_args, _unknown = parser.parse_known_args(list(argv) if argv is not None else None)
    return parsed_args
