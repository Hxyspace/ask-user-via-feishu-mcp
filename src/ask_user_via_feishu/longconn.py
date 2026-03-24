from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.event_processor import FeishuEventProcessor

logger = logging.getLogger(__name__)
DEFAULT_EVENT_TYPES = ["im.message.receive_v1", "card.action.trigger"]


class LongConnectionSetupError(RuntimeError):
    """Raised when the Feishu long-connection subscriber cannot be configured."""


class FeishuLongConnectionSubscriber:
    def __init__(
        self,
        settings: Settings,
        event_processor: FeishuEventProcessor,
        sdk: Any | None = None,
    ) -> None:
        self._settings = settings
        self._event_processor = event_processor
        self._sdk = sdk or self._import_sdk()
        self._event_types = list(DEFAULT_EVENT_TYPES)
        if not self._event_types:
            raise LongConnectionSetupError("At least one long-connection event must be configured.")

    @staticmethod
    def _import_sdk() -> Any:
        try:
            import lark_oapi as lark
        except ImportError as exc:  # pragma: no cover
            raise LongConnectionSetupError(
                "lark-oapi is required for long-connection mode. Install project dependencies with `pip install -e .`."
            ) from exc
        return lark

    def build_event_handler(self) -> Any:
        builder = self._sdk.EventDispatcherHandler.builder("", "")
        for event_type in self._event_types:
            builder = self._register_v2_event(builder, event_type)
        return builder.build()

    def start(self) -> None:
        event_handler = self.build_event_handler()
        client = self._sdk.ws.Client(
            self._settings.app_id,
            self._settings.app_secret,
            event_handler=event_handler,
            log_level=self._resolve_sdk_log_level(),
        )
        logger.info("Starting Feishu long-connection subscriber with event_types=%s", self._event_types)
        client.start()

    def _register_v2_event(self, builder: Any, event_type: str) -> Any:
        return self._register_v2_event_callback(builder, event_type, self._build_callback(event_type, schema="2.0"))

    def _register_v2_event_callback(self, builder: Any, event_type: str, callback: Any) -> Any:
        method_name = f"register_p2_{event_type.replace('.', '_')}"
        register = getattr(builder, method_name, None)
        if callable(register):
            return register(callback)
        customized_register = getattr(builder, "register_p2_customized_event", None)
        if callable(customized_register):
            logger.info("Falling back to register_p2_customized_event for event_type=%s", event_type)
            return customized_register(event_type, callback)
        raise LongConnectionSetupError(
            f"lark-oapi does not expose `{method_name}` or `register_p2_customized_event`. Remove unsupported event type `{event_type}`."
        )

    def _build_callback(self, event_type: str, schema: str) -> Any:
        def handle(data: Any) -> Any:
            payload = self._normalize_payload(event_type, schema, data)
            result = self._event_processor.process_payload(payload)
            logger.info(
                "Processed long-connection event type=%s handled=%s reply_sent=%s",
                event_type,
                result.get("handled"),
                result.get("reply_sent"),
            )
            return self._build_sdk_response(event_type, result)

        return handle

    def _build_sdk_response(self, event_type: str, result: dict[str, Any]) -> Any:
        if event_type != "card.action.trigger":
            return None
        return self._build_card_action_sdk_response(result)

    def _build_card_action_sdk_response(self, result: dict[str, Any]) -> Any:
        callback_response = result.get("callback_response")
        response_module = self._get_card_action_response_module()
        response = response_module.P2CardActionTriggerResponse()
        if not isinstance(callback_response, dict):
            return response
        toast = callback_response.get("toast")
        if isinstance(toast, dict):
            response.toast = response_module.CallBackToast(toast)
        card = callback_response.get("card")
        if isinstance(card, dict):
            card_payload = card if "type" in card and "data" in card else {"type": "raw", "data": card}
            response.card = response_module.CallBackCard(card_payload)
        return response

    def _get_card_action_response_module(self) -> Any:
        event_module = getattr(self._sdk, "event", None)
        callback_module = getattr(event_module, "callback", None)
        model_module = getattr(callback_module, "model", None)
        response_module = getattr(model_module, "p2_card_action_trigger", None)
        if response_module is not None:
            return response_module
        return importlib.import_module("lark_oapi.event.callback.model.p2_card_action_trigger")

    def _normalize_payload(self, event_type: str, schema: str, data: Any) -> dict[str, Any]:
        payload = self._marshal_event(data)
        if "header" in payload and "event" in payload:
            normalized = dict(payload)
            header = dict(normalized.get("header") or {})
            if not header.get("event_type"):
                header["event_type"] = event_type
            normalized["header"] = header
            normalized.setdefault("schema", schema)
            return normalized
        return {
            "schema": schema,
            "header": {"event_type": event_type},
            "event": payload,
        }

    def _marshal_event(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            return data
        raw_json = self._sdk.JSON.marshal(data)
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict):
            raise LongConnectionSetupError("Long-connection event payload must marshal to a JSON object.")
        return parsed

    def _resolve_sdk_log_level(self) -> Any:
        log_level = getattr(self._sdk.LogLevel, self._settings.log_level.upper(), None)
        if log_level is not None:
            return log_level
        return getattr(self._sdk.LogLevel, "INFO", None)
