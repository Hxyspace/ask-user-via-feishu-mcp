from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ask_user_via_feishu.config import Settings
from ask_user_via_feishu.errors import MessageValidationError
from ask_user_via_feishu.schemas import FeishuPostContent
from ask_user_via_feishu.services import TokenManager
from ask_user_via_feishu.services.message_service import MessageService

MISSING_RUNTIME_CONFIG = "/home/yuan/code/llm/ask_user_via_feishu/tests/__no_runtime_config__.json"


class FakeTokenManager:
    async def get_token(self) -> str:
        return "tenant_token"


class FakeAuthClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_tenant_access_token(self, app_id: str, app_secret: str) -> tuple[str, int]:
        self.calls += 1
        return (f"tenant_token_{self.calls}", 7200)


class FakeMessageClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send_message(self, access_token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("send_message", {"access_token": access_token, **kwargs}))
        return {"code": 0, "data": {"message_id": "om_123", "chat_id": "oc_p2p", "create_time": "1234567890123"}}

    async def upload_image(self, access_token: str, *, image_path: str) -> dict[str, Any]:
        self.calls.append(("upload_image", {"access_token": access_token, "image_path": image_path}))
        return {"code": 0, "data": {"image_key": "img_123"}}

    async def upload_file(self, access_token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("upload_file", {"access_token": access_token, **kwargs}))
        return {"code": 0, "data": {"file_key": "file_123"}}

    async def update_message_card(self, access_token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("update_message_card", {"access_token": access_token, **kwargs}))
        return {"code": 0, "data": {"message_id": kwargs["message_id"]}}

    async def download_message_resource(self, access_token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("download_message_resource", {"access_token": access_token, **kwargs}))
        if kwargs["resource_type"] == "image":
            return {
                "content": b"image-bytes",
                "content_type": "image/png",
                "content_disposition": "",
            }
        return {
            "content": b"file-bytes",
            "content_type": "application/pdf",
            "content_disposition": 'attachment; filename="downloaded.pdf"',
        }

    async def create_message_reaction(self, access_token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_message_reaction", {"access_token": access_token, **kwargs}))
        return {"code": 0, "data": {"reaction_id": "react_123"}}

    async def delete_message_reaction(self, access_token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_message_reaction", {"access_token": access_token, **kwargs}))
        return {"code": 0, "data": {}}


class MessageServiceTest(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings.from_env(
            {
                "APP_ID": "cli_123",
                "APP_SECRET": "secret_123",
                "OWNER_OPEN_ID": "ou_owner",
                "RUNTIME_CONFIG_PATH": MISSING_RUNTIME_CONFIG,
            }
        )

    def test_send_text_uses_owner_as_default_target(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())

        result = asyncio.run(service.send_text(receive_id_type="open_id", receive_id="", text="hello"))

        self.assertEqual(
            result,
            {
                "ok": True,
                "message_id": "om_123",
                "receive_id": "ou_owner",
                "chat_id": "oc_p2p",
                "create_time_ms": 1234567890123,
            },
        )
        self.assertEqual(client.calls[0][0], "send_message")
        self.assertEqual(client.calls[0][1]["receive_id"], "ou_owner")
        self.assertEqual(client.calls[0][1]["msg_type"], "post")
        self.assertEqual(
            json.loads(client.calls[0][1]["content"]),
            {"zh_cn": {"content": [[{"tag": "md", "text": "hello", }]]}},
        )

    def test_send_image_rejects_path_and_key_together(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())

        with self.assertRaises(MessageValidationError):
            asyncio.run(
                service.send_image(
                    receive_id_type="open_id",
                    receive_id="",
                    image_path="",
                )
            )

    def test_send_file_rejects_unsupported_file_type(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())

        with self.assertRaises(MessageValidationError):
            asyncio.run(
                service.upload_file(
                    file_path="/tmp/a.bin",
                    file_type="zip",  # type: ignore[arg-type]
                )
            )

    def test_create_and_delete_reaction(self) -> None:
        client = FakeMessageClient()
        settings = self._settings()
        service = MessageService(client, FakeTokenManager(), settings)

        created = asyncio.run(service.create_reaction(message_id="om_123"))
        deleted = asyncio.run(service.delete_reaction(message_id="om_123", reaction_id="react_123"))

        self.assertTrue(created["ok"])
        self.assertTrue(deleted["ok"])
        self.assertEqual([name for name, _ in client.calls], ["create_message_reaction", "delete_message_reaction"])

    def test_send_post_accepts_supported_elements(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())
        content: FeishuPostContent = [
            [{"tag": "text", "text": "文档："}, {"tag": "a", "text": "README", "href": "https://example.com"}],
            [{"tag": "at", "user_id": "ou_owner"}],
            [{"tag": "img", "image_key": "img_123"}],
            [{"tag": "media", "file_key": "file_123"}],
            [{"tag": "emotion", "emoji_type": "SMILE"}],
            [{"tag": "hr"}],
            [{"tag": "code_block", "language": "GO", "text": "func main() int64 {\n    return 0\n}"}],
            [{"tag": "md", "text": "**mention user:**<at user_id=\"ou_owner\">Owner</at>"}],
        ]

        result = asyncio.run(
            service.send_post(
                receive_id_type="open_id",
                receive_id="",
                title="demo",
                content=content,
            )
        )

        self.assertEqual(
            result,
            {
                "ok": True,
                "message_id": "om_123",
                "receive_id": "ou_owner",
                "chat_id": "oc_p2p",
                "create_time_ms": 1234567890123,
            },
        )
        self.assertEqual(client.calls[0][0], "send_message")
        self.assertEqual(client.calls[0][1]["msg_type"], "post")
        payload = json.loads(client.calls[0][1]["content"])
        self.assertEqual(payload["zh_cn"]["title"], "demo")
        self.assertEqual(payload["zh_cn"]["content"], content)

    def test_send_post_rejects_unknown_tag(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())

        with self.assertRaises(MessageValidationError):
            asyncio.run(
                service.send_post(
                    receive_id_type="open_id",
                    receive_id="",
                    title="demo",
                    content=[[{"tag": "unknown", "text": "OK"}]],  # type: ignore[list-item]
                )
            )

    def test_send_post_rejects_media_without_file_key(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())

        with self.assertRaises(MessageValidationError):
            asyncio.run(
                service.send_post(
                    receive_id_type="open_id",
                    receive_id="",
                    title="demo",
                    content=[[{"tag": "media", "image_key": "img_123"}]],  # type: ignore[list-item]
                )
            )

    def test_send_post_rejects_markdown_mixed_with_other_elements(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())

        with self.assertRaises(MessageValidationError):
            asyncio.run(
                service.send_post(
                    receive_id_type="open_id",
                    receive_id="",
                    title="demo",
                    content=[
                        [
                            {"tag": "md", "text": "**bold**"},
                            {"tag": "text", "text": "should not be here"},
                        ]
                    ],
                )
            )

    def test_send_post_can_run_after_send_text_on_a_different_event_loop(self) -> None:
        client = FakeMessageClient()
        auth_client = FakeAuthClient()
        service = MessageService(client, TokenManager(auth_client, self._settings()), self._settings())
        content: FeishuPostContent = [[{"tag": "text", "text": "hello"}]]

        first = asyncio.run(service.send_text(receive_id_type="open_id", receive_id="", text="first"))
        service._token_manager._expires_at = 0.0  # force token refresh through a new loop
        second = asyncio.run(
            service.send_post(
                receive_id_type="open_id",
                receive_id="",
                title="demo",
                content=content,
            )
        )

        self.assertEqual(first["message_id"], "om_123")
        self.assertEqual(second["message_id"], "om_123")
        self.assertEqual(auth_client.calls, 2)

    def test_download_reply_resources_saves_files_under_receive_files(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())
        expected_bucket = datetime.now().strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            expected_paths: list[str] = []
            downloaded_bytes: list[bytes] = []
            target_root = Path(tmpdir) / "attachments"
            paths = asyncio.run(
                service.download_reply_resources(
                    question_id="ask_123",
                    resource_refs=[
                        {"kind": "image", "message_id": "om_image", "image_key": "img_123"},
                        {"kind": "file", "message_id": "om_file", "file_key": "file_123", "file_name": "report.pdf"},
                    ],
                    target_root=target_root,
                )
            )
            expected_paths = list(paths)
            downloaded_bytes = [Path(path).read_bytes() for path in paths]

        self.assertEqual(len(expected_paths), 2)
        self.assertTrue(expected_paths[0].endswith(".png"))
        self.assertTrue(expected_paths[1].endswith("report.pdf"))
        self.assertEqual(downloaded_bytes, [b"image-bytes", b"file-bytes"])
        for path in expected_paths:
            self.assertIn(str(Path("attachments") / expected_bucket), path)

    def test_download_reply_resources_uses_fallback_name_for_same_day_collision(self) -> None:
        client = FakeMessageClient()
        service = MessageService(client, FakeTokenManager(), self._settings())
        expected_bucket = datetime.now().strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            target_root = Path(tmpdir) / "attachments"
            bucket_dir = target_root / expected_bucket
            bucket_dir.mkdir(parents=True, exist_ok=True)
            (bucket_dir / "report.pdf").write_bytes(b"existing-1")

            paths = asyncio.run(
                service.download_reply_resources(
                    question_id="ask_123",
                    resource_refs=[
                        {"kind": "file", "message_id": "om_file", "file_key": "file_123", "file_name": "report.pdf"},
                    ],
                    target_root=target_root,
                )
            )

        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("report_file_file_123.pdf"))
