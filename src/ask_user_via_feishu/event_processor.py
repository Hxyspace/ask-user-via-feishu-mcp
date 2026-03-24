from __future__ import annotations

from typing import Any

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.event_handlers import CardActionEventHandler, EventRouter, MessageReceiveEventHandler


class FeishuEventProcessor:
    def __init__(self, settings: Settings) -> None:
        self._event_router = EventRouter()
        self.register_handler("im.message.receive_v1", MessageReceiveEventHandler(settings))
        self.register_handler("card.action.trigger", CardActionEventHandler(settings))

    def process_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._event_router.dispatch(payload)

    def register_handler(self, event_type: str, handler: Any) -> None:
        self._event_router.register(event_type, handler)
