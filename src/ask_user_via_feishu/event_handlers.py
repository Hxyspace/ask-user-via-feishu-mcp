from __future__ import annotations

import json
from typing import Any

from ask_user_via_feishu.config import Settings



def parse_message_content(message: dict[str, Any]) -> dict[str, Any]:
    raw_content = message.get("content")
    if isinstance(raw_content, dict):
        return raw_content
    if isinstance(raw_content, str) and raw_content.strip():
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            return {"text": raw_content}
        if isinstance(parsed, dict):
            return parsed
    return {}


class EventRouter:
    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}

    def register(self, event_type: str, handler: Any) -> None:
        self._handlers[event_type] = handler

    def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_type = _get_event_type(payload)
        handler = self._handlers.get(event_type)
        if handler is None:
            return {"handled": False, "reply_sent": False, "event_type": event_type}
        return handler(payload)


class MessageReceiveEventHandler:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = payload.get("event") or {}
        sender_open_id = str(event.get("sender", {}).get("sender_id", {}).get("open_id") or "").strip()
        policy_reason = _owner_only_policy_reason(self._settings, sender_open_id)
        if policy_reason is not None:
            return {
                "handled": True,
                "reply_sent": False,
                "policy_denied": True,
                "policy_reason": policy_reason,
            }
        message = event.get("message") or {}
        content = parse_message_content(message)
        return {
            "handled": True,
            "reply_sent": False,
            "message_id": str(message.get("message_id") or "").strip(),
            "message_type": str(message.get("message_type") or message.get("msg_type") or "").strip(),
            "text": str(content.get("text") or "").strip(),
            "message_content": content,
        }


class CardActionEventHandler:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = payload.get("event") or {}
        operator_open_id = str(event.get("operator", {}).get("open_id") or "").strip()
        policy_reason = _owner_only_policy_reason(self._settings, operator_open_id)
        if policy_reason is not None:
            return {
                "handled": True,
                "reply_sent": False,
                "policy_denied": True,
                "policy_reason": policy_reason,
                "callback_response": {
                    "toast": {"type": "warning", "content": policy_reason},
                },
            }
        return {
            "handled": True,
            "reply_sent": False,
            "callback_response": {
                "toast": {"type": "info", "content": "已收到操作"},
            },
        }



def _get_event_type(payload: dict[str, Any]) -> str:
    header = payload.get("header") or {}
    return str(header.get("event_type") or payload.get("type") or "unknown")



def _owner_only_policy_reason(settings: Settings, actor_open_id: str) -> str | None:
    owner_open_id = settings.owner_open_id.strip()
    if not owner_open_id:
        return "owner_open_id is not configured."
    if actor_open_id != owner_open_id:
        return "This bot only accepts events from the configured owner."
    return None
