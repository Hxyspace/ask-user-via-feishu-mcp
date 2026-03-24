from __future__ import annotations

from ask_user_via_feishu.clients import FeishuAuthClient, FeishuMessageClient
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.event_processor import FeishuEventProcessor
from ask_user_via_feishu.services import MessageService, TokenManager



def build_message_service(settings: Settings) -> MessageService:
    auth_client = FeishuAuthClient(settings.base_url, settings.api_timeout_seconds)
    message_client = FeishuMessageClient(settings.base_url, settings.api_timeout_seconds)
    token_manager = TokenManager(auth_client, settings)
    return MessageService(message_client, token_manager, settings)



def build_event_processor(settings: Settings) -> FeishuEventProcessor:
    return FeishuEventProcessor(settings)
