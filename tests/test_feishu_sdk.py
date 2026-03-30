from __future__ import annotations

import asyncio
import io
import json
import tempfile
from types import SimpleNamespace
import unittest

from ask_user_via_feishu.clients.feishu_sdk import FeishuSDKClient
from ask_user_via_feishu.clients.feishu_sdk import DEFAULT_OWNER_CHAT_AVATAR_KEY
from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.errors import FeishuAPIError

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class FakeRawResponse:
    def __init__(self, *, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class FakeSDKResponse:
    def __init__(
        self,
        *,
        ok: bool = True,
        code: int = 0,
        msg: str = "",
        data: object | None = None,
        file: object | None = None,
        raw: FakeRawResponse | None = None,
    ) -> None:
        self.code = code
        self.msg = msg
        self.data = data
        self.file = file
        self.raw = raw or FakeRawResponse()
        self._ok = ok

    def success(self) -> bool:
        return self._ok


class FakeTenantAccessTokenEndpoint:
    def __init__(self, response: FakeSDKResponse) -> None:
        self.response = response
        self.requests = []

    async def ainternal(self, request: object) -> FakeSDKResponse:
        self.requests.append(request)
        return self.response


class FakeMessageEndpoint:
    def __init__(self, *, create_response: FakeSDKResponse, patch_response: FakeSDKResponse) -> None:
        self.create_response = create_response
        self.patch_response = patch_response
        self.create_requests = []
        self.patch_requests = []

    async def acreate(self, request: object) -> FakeSDKResponse:
        self.create_requests.append(request)
        return self.create_response

    async def apatch(self, request: object) -> FakeSDKResponse:
        self.patch_requests.append(request)
        return self.patch_response


class FakeUploadEndpoint:
    def __init__(self, response: FakeSDKResponse) -> None:
        self.response = response
        self.requests = []

    async def acreate(self, request: object) -> FakeSDKResponse:
        self.requests.append(request)
        return self.response


class FakeMessageResourceEndpoint:
    def __init__(self, response: FakeSDKResponse) -> None:
        self.response = response
        self.requests = []

    async def aget(self, request: object) -> FakeSDKResponse:
        self.requests.append(request)
        return self.response


class FakeMessageReactionEndpoint:
    def __init__(self, *, create_response: FakeSDKResponse, delete_response: FakeSDKResponse) -> None:
        self.create_response = create_response
        self.delete_response = delete_response
        self.create_requests = []
        self.delete_requests = []

    async def acreate(self, request: object) -> FakeSDKResponse:
        self.create_requests.append(request)
        return self.create_response

    async def adelete(self, request: object) -> FakeSDKResponse:
        self.delete_requests.append(request)
        return self.delete_response


class FakeChatEndpoint:
    def __init__(self, *, list_response: FakeSDKResponse, create_response: FakeSDKResponse) -> None:
        self.list_response = list_response
        self.create_response = create_response
        self.list_requests = []
        self.create_requests = []

    async def alist(self, request: object) -> FakeSDKResponse:
        self.list_requests.append(request)
        return self.list_response

    async def acreate(self, request: object) -> FakeSDKResponse:
        self.create_requests.append(request)
        return self.create_response


class FakeLarkClient:
    def __init__(
        self,
        *,
        tenant_access_token_response: FakeSDKResponse | None = None,
        message_create_response: FakeSDKResponse | None = None,
        message_patch_response: FakeSDKResponse | None = None,
        image_create_response: FakeSDKResponse | None = None,
        file_create_response: FakeSDKResponse | None = None,
        message_resource_response: FakeSDKResponse | None = None,
        reaction_create_response: FakeSDKResponse | None = None,
        reaction_delete_response: FakeSDKResponse | None = None,
        chat_list_response: FakeSDKResponse | None = None,
        chat_create_response: FakeSDKResponse | None = None,
    ) -> None:
        self.tenant_access_token = FakeTenantAccessTokenEndpoint(
            tenant_access_token_response or FakeSDKResponse()
        )
        self.message = FakeMessageEndpoint(
            create_response=message_create_response
            or FakeSDKResponse(
                data=SimpleNamespace(message_id="om_123", chat_id="oc_123", create_time="1234567890123")
            ),
            patch_response=message_patch_response or FakeSDKResponse(),
        )
        self.image = FakeUploadEndpoint(
            image_create_response or FakeSDKResponse(data=SimpleNamespace(image_key="img_123"))
        )
        self.file = FakeUploadEndpoint(
            file_create_response or FakeSDKResponse(data=SimpleNamespace(file_key="file_123"))
        )
        self.message_resource = FakeMessageResourceEndpoint(
            message_resource_response
            or FakeSDKResponse(
                file=io.BytesIO(b"default-bytes"),
                raw=FakeRawResponse(headers={"Content-Type": "application/octet-stream"}),
            )
        )
        self.message_reaction = FakeMessageReactionEndpoint(
            create_response=reaction_create_response or FakeSDKResponse(data=SimpleNamespace(reaction_id="react_123")),
            delete_response=reaction_delete_response or FakeSDKResponse(),
        )
        self.chat = FakeChatEndpoint(
            list_response=chat_list_response
            or FakeSDKResponse(
                data=SimpleNamespace(
                    items=[
                        SimpleNamespace(
                            chat_id="oc_1",
                            name="alpha",
                            owner_id="ou_owner",
                        )
                    ],
                    has_more=False,
                    page_token="",
                )
            ),
            create_response=chat_create_response
            or FakeSDKResponse(
                data=SimpleNamespace(
                    chat_id="oc_created",
                    name="project-alpha",
                    owner_id="ou_owner",
                )
            ),
        )
        self.auth = SimpleNamespace(v3=SimpleNamespace(tenant_access_token=self.tenant_access_token))
        self.im = SimpleNamespace(
            v1=SimpleNamespace(
                message=self.message,
                image=self.image,
                file=self.file,
                message_resource=self.message_resource,
                message_reaction=self.message_reaction,
                chat=self.chat,
            )
        )


class FeishuSDKClientTest(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
                "OWNER_OPEN_ID": "ou_owner",
                "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
            }
        )

    def test_health_check_uses_internal_token_endpoint(self) -> None:
        fake_client = FakeLarkClient()
        client = FeishuSDKClient(self._settings(), client=fake_client)

        asyncio.run(client.health_check())

        request = fake_client.tenant_access_token.requests[0]
        self.assertEqual(request.request_body.app_id, "cli_123")
        self.assertEqual(request.request_body.app_secret, "secret_123")

    def test_health_check_raises_feishu_api_error_on_failure(self) -> None:
        fake_client = FakeLarkClient(
            tenant_access_token_response=FakeSDKResponse(
                ok=False,
                code=99991663,
                msg="invalid credentials",
                raw=FakeRawResponse(status_code=401),
            )
        )
        client = FeishuSDKClient(self._settings(), client=fake_client)

        with self.assertRaises(FeishuAPIError) as raised:
            asyncio.run(client.health_check())

        self.assertEqual(raised.exception.code, 99991663)
        self.assertEqual(raised.exception.status_code, 401)
        self.assertIn("invalid credentials", str(raised.exception))

    def test_send_message_maps_request_and_response(self) -> None:
        fake_client = FakeLarkClient()
        client = FeishuSDKClient(self._settings(), client=fake_client)

        result = asyncio.run(
            client.send_message(
                receive_id_type="chat_id",
                receive_id="oc_chat",
                msg_type="text",
                content='{"text":"hello"}',
                uuid="uuid-1",
            )
        )

        request = fake_client.message.create_requests[0]
        self.assertEqual(request.receive_id_type, "chat_id")
        self.assertEqual(request.request_body.receive_id, "oc_chat")
        self.assertEqual(request.request_body.msg_type, "text")
        self.assertEqual(request.request_body.content, '{"text":"hello"}')
        self.assertEqual(request.request_body.uuid, "uuid-1")
        self.assertEqual(
            result,
            {
                "code": 0,
                "data": {
                    "message_id": "om_123",
                    "chat_id": "oc_123",
                    "create_time": "1234567890123",
                },
            },
        )

    def test_upload_file_maps_metadata_and_duration(self) -> None:
        fake_client = FakeLarkClient()
        client = FeishuSDKClient(self._settings(), client=fake_client)

        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(b"pdf-data")
            handle.flush()
            result = asyncio.run(
                client.upload_file(
                    file_path=handle.name,
                    file_type="pdf",
                    file_name="report.pdf",
                    duration_ms=321,
                )
            )

        request = fake_client.file.requests[0]
        self.assertEqual(request.request_body.file_type, "pdf")
        self.assertEqual(request.request_body.file_name, "report.pdf")
        self.assertEqual(request.request_body.duration, 321)
        self.assertEqual(result, {"code": 0, "data": {"file_key": "file_123"}})

    def test_update_message_card_serializes_content(self) -> None:
        fake_client = FakeLarkClient()
        client = FeishuSDKClient(self._settings(), client=fake_client)

        result = asyncio.run(client.update_message_card(message_id="om_123", card={"elements": [{"tag": "div"}]}))

        request = fake_client.message.patch_requests[0]
        self.assertEqual(request.message_id, "om_123")
        self.assertEqual(json.loads(request.request_body.content), {"elements": [{"tag": "div"}]})
        self.assertEqual(result, {"code": 0, "data": {"message_id": "om_123"}})

    def test_download_message_resource_returns_stream_and_headers(self) -> None:
        fake_client = FakeLarkClient(
            message_resource_response=FakeSDKResponse(
                file=io.BytesIO(b"image-bytes"),
                raw=FakeRawResponse(
                    headers={
                        "Content-Type": "image/png",
                        "Content-Disposition": 'attachment; filename="image.png"',
                    }
                ),
            )
        )
        client = FeishuSDKClient(self._settings(), client=fake_client)

        result = asyncio.run(
            client.download_message_resource(
                message_id="om_123",
                file_key="img_123",
                resource_type="image",
            )
        )

        request = fake_client.message_resource.requests[0]
        self.assertEqual(request.message_id, "om_123")
        self.assertEqual(request.file_key, "img_123")
        self.assertEqual(request.type, "image")
        self.assertTrue(hasattr(result["content_stream"], "read"))
        self.assertEqual(result["content_stream"].read(), b"image-bytes")
        result["content_stream"].close()
        self.assertEqual(result["content_type"], "image/png")
        self.assertEqual(result["content_disposition"], 'attachment; filename="image.png"')

    def test_create_and_delete_reaction_map_requests(self) -> None:
        fake_client = FakeLarkClient()
        client = FeishuSDKClient(self._settings(), client=fake_client)

        created = asyncio.run(client.create_message_reaction(message_id="om_123", emoji_type="Typing"))
        deleted = asyncio.run(client.delete_message_reaction(message_id="om_123", reaction_id="react_123"))

        create_request = fake_client.message_reaction.create_requests[0]
        delete_request = fake_client.message_reaction.delete_requests[0]
        self.assertEqual(create_request.message_id, "om_123")
        self.assertEqual(create_request.request_body.reaction_type.emoji_type, "Typing")
        self.assertEqual(delete_request.message_id, "om_123")
        self.assertEqual(delete_request.reaction_id, "react_123")
        self.assertEqual(created, {"code": 0, "data": {"reaction_id": "react_123"}})
        self.assertEqual(deleted, {"code": 0, "data": {}})

    def test_list_chats_maps_request_and_items(self) -> None:
        fake_client = FakeLarkClient()
        client = FeishuSDKClient(self._settings(), client=fake_client)

        result = asyncio.run(client.list_chats(user_id_type="open_id", page_size=50))

        request = fake_client.chat.list_requests[0]
        self.assertEqual(request.user_id_type, "open_id")
        self.assertEqual(request.page_size, 50)
        self.assertEqual(
            result,
            {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "chat_id": "oc_1",
                            "name": "alpha",
                            "owner_id": "ou_owner",
                        }
                    ]
                },
            },
        )

    def test_create_chat_maps_request_and_response(self) -> None:
        fake_client = FakeLarkClient()
        client = FeishuSDKClient(self._settings(), client=fake_client)

        result = asyncio.run(
            client.create_chat(
                name="project-alpha",
                owner_open_id="ou_owner",
                uuid="create_123",
            )
        )

        request = fake_client.chat.create_requests[0]
        self.assertEqual(request.user_id_type, "open_id")
        self.assertEqual(request.uuid, "create_123")
        self.assertEqual(request.request_body.name, "project-alpha")
        self.assertEqual(request.request_body.avatar, DEFAULT_OWNER_CHAT_AVATAR_KEY)
        self.assertEqual(request.request_body.owner_id, "ou_owner")
        self.assertEqual(request.request_body.user_id_list, ["ou_owner"])
        self.assertEqual(request.request_body.chat_mode, "group")
        self.assertEqual(request.request_body.chat_type, "private")
        self.assertEqual(request.request_body.group_message_type, "chat")
        self.assertEqual(
            result,
            {
                "code": 0,
                "data": {
                    "chat_id": "oc_created",
                    "name": "project-alpha",
                    "owner_id": "ou_owner",
                },
            },
        )
