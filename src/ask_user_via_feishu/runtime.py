from __future__ import annotations

from ask_user_via_feishu.clients import FeishuSDKClient
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.event_processor import FeishuEventProcessor
from ask_user_via_feishu.services import MessageService



def build_message_service(settings: Settings) -> MessageService:
    sdk_client = FeishuSDKClient(settings)
    return MessageService(sdk_client, settings)



def build_event_processor(settings: Settings) -> FeishuEventProcessor:
    return FeishuEventProcessor(settings)
