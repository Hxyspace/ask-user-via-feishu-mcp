from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ask_user_via_feishu import __version__


DEFAULT_RUNTIME_CONFIG_PATH = ""
SERVER_NAME = (__package__ or "ask_user_via_feishu").replace("_", "-")
SERVER_VERSION = __version__
SERVER_TRANSPORT = "stdio"


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    base_url: str = "https://open.feishu.cn"
    api_timeout_seconds: int = 10
    runtime_config_path: str = DEFAULT_RUNTIME_CONFIG_PATH
    log_level: str = "INFO"
    owner_open_id: str = ""
    reaction_enabled: bool = True
    reaction_emoji_type: str = "Typing"
    ask_timeout_seconds: int = 600
    ask_reminder_max_attempts: int = 10
    ask_timeout_reminder_text: str = "请及时回复！！！"
    ask_timeout_default_answer: str = "[AUTO_RECALL]"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = dict(os.environ if environ is None else environ)
        runtime_config_path = (env.get("RUNTIME_CONFIG_PATH") or DEFAULT_RUNTIME_CONFIG_PATH).strip()
        runtime_config = _load_runtime_config(runtime_config_path)

        return cls(
            app_id=_first_non_empty(
                env.get("APP_ID"),
                _get_config_string(runtime_config, ("app_id",)),
            ),
            app_secret=_first_non_empty(
                env.get("APP_SECRET"),
                _get_config_string(runtime_config, ("app_secret",)),
            ),
            base_url=_first_non_empty(
                env.get("BASE_URL"),
                _get_config_string(runtime_config, ("base_url",)),
                default="https://open.feishu.cn",
            ),
            api_timeout_seconds=_resolve_int(
                env.get("API_TIMEOUT_SECONDS"),
                _get_config_value(runtime_config, ("api_timeout_seconds",)),
                default=10,
            ),
            runtime_config_path=runtime_config_path,
            log_level=_first_non_empty(env.get("LOG_LEVEL"), default="INFO"),
            owner_open_id=_first_non_empty(
                env.get("OWNER_OPEN_ID"),
                _get_config_string(runtime_config, ("owner_open_id",)),
                default="",
            ),
            reaction_enabled=_resolve_bool(
                env.get("REACTION_ENABLED"),
                _get_config_value(runtime_config, ("reaction", "enabled")),
                default=True,
            ),
            reaction_emoji_type=_first_non_empty(
                env.get("REACTION_EMOJI_TYPE"),
                _get_config_string(runtime_config, ("reaction", "emoji_type")),
                default="Typing",
            ),
            ask_timeout_seconds=_resolve_int(
                env.get("ASK_TIMEOUT_SECONDS"),
                _get_config_value(runtime_config, ("ask", "timeout_seconds")),
                default=600,
            ),
            ask_reminder_max_attempts=_resolve_int(
                env.get("ASK_REMINDER_MAX_ATTEMPTS"),
                _get_config_value(runtime_config, ("ask", "reminder_max_attempts")),
                default=10,
            ),
            ask_timeout_reminder_text=_resolve_string_value(
                env,
                "ASK_TIMEOUT_REMINDER_TEXT",
                runtime_config,
                ("ask", "timeout_reminder_text"),
                default="请及时回复！！！",
            ),
            ask_timeout_default_answer=_resolve_string_value(
                env,
                "ASK_TIMEOUT_DEFAULT_ANSWER",
                runtime_config,
                ("ask", "timeout_default_answer"),
                default="[AUTO_RECALL]",
                allow_empty=True,
            ),
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "app_secret": "***" if self.app_secret else "",
            "base_url": self.base_url,
            "api_timeout_seconds": self.api_timeout_seconds,
            "runtime_config_path": self.runtime_config_path,
            "log_level": self.log_level,
            "owner_open_id": self.owner_open_id,
            "reaction_enabled": self.reaction_enabled,
            "reaction_emoji_type": self.reaction_emoji_type,
            "ask_timeout_seconds": self.ask_timeout_seconds,
            "ask_reminder_max_attempts": self.ask_reminder_max_attempts,
            "ask_timeout_reminder_text": self.ask_timeout_reminder_text,
            "ask_timeout_default_answer": self.ask_timeout_default_answer,
        }

    def validate(self) -> None:
        if not self.app_id.strip():
            raise ValueError("APP_ID is required.")
        if not self.app_secret.strip():
            raise ValueError("APP_SECRET is required.")
        if not self.owner_open_id.strip():
            raise ValueError("OWNER_OPEN_ID is required for this owner-only MCP server.")
        if self.api_timeout_seconds <= 0:
            raise ValueError("API_TIMEOUT_SECONDS must be greater than 0.")



def _load_runtime_config(path_value: str) -> dict[str, Any]:
    if not path_value.strip():
        return {}
    path = Path(path_value).expanduser()
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Runtime config JSON must be an object.")
    return data



def _get_config_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current



def _get_config_string(data: dict[str, Any], path: tuple[str, ...]) -> str:
    value = _get_config_value(data, path)
    if value is None:
        return ""
    return str(value).strip()



def _resolve_bool(*values: Any, default: bool) -> bool:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if not normalized:
            continue
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value: {value}")
    return default



def _resolve_int(*values: Any, default: int) -> int:
    for value in values:
        if value is None:
            continue
        if isinstance(value, int):
            return value
        normalized = str(value).strip()
        if not normalized:
            continue
        return int(normalized)
    return default



def _first_non_empty(*values: Any, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return default


def _resolve_string_value(
    env: Mapping[str, str],
    env_key: str,
    runtime_config: dict[str, Any],
    path: tuple[str, ...],
    *,
    default: str = "",
    allow_empty: bool = True,
) -> str:
    if env_key in env:
        value = str(env.get(env_key, "")).strip()
        return value if (allow_empty or value) else default
    config_value = _get_config_value(runtime_config, path)
    if config_value is not None:
        value = str(config_value).strip()
        return value if (allow_empty or value) else default
    return default
