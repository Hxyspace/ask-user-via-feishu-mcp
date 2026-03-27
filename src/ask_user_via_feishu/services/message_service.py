from __future__ import annotations

import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ask_user_via_feishu.clients.feishu_messages import FeishuMessageClient
from ask_user_via_feishu.config import SERVER_NAME, SERVER_VERSION, Settings
from ask_user_via_feishu.errors import MessageValidationError
from ask_user_via_feishu.schemas import (
    VALID_FEISHU_FILE_TYPES,
    VALID_FEISHU_POST_TAGS,
    FeishuFileType,
    FeishuPostContent,
    ReceiveIdType,
)
from ask_user_via_feishu.services.token_manager import TokenManager


class MessageService:
    def __init__(
        self,
        message_client: FeishuMessageClient,
        token_manager: TokenManager,
        settings: Settings,
    ) -> None:
        self._message_client = message_client
        self._token_manager = token_manager
        self._settings = settings

    async def health_check(self) -> dict[str, Any]:
        await self._token_manager.get_token()
        return {
            "ok": True,
            "service": SERVER_NAME,
            "version": SERVER_VERSION,
            "auth_configured": True,
            "token_fetch_ok": True,
        }

    async def send_text(
        self,
        *,
        receive_id_type: ReceiveIdType,
        receive_id: str,
        text: str,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        resolved_receive_id_type, resolved_receive_id = self._resolve_receive_target(receive_id_type, receive_id)
        if not text.strip():
            raise MessageValidationError("text must not be empty.")
        token = await self._token_manager.get_token()
        response = await self._message_client.send_message(
            token,
            receive_id_type=resolved_receive_id_type,
            receive_id=resolved_receive_id,
            msg_type="text",
            content=json.dumps({"text": text}, ensure_ascii=False),
            uuid=uuid,
        )
        return self._normalize_result(response, resolved_receive_id)

    async def upload_image(self, *, image_path: str) -> dict[str, Any]:
        if not image_path.strip():
            raise MessageValidationError("image_path must not be empty.")
        token = await self._token_manager.get_token()
        response = await self._message_client.upload_image(token, image_path=image_path)
        data = response.get("data") or {}
        image_key = str(data.get("image_key") or "").strip()
        if not image_key:
            raise MessageValidationError("Image upload did not return a valid image_key.")
        return {
            "ok": True,
            "image_key": image_key,
            "image_path": image_path,
        }

    async def send_image(
        self,
        *,
        receive_id_type: ReceiveIdType,
        receive_id: str,
        image_path: str,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        resolved_receive_id_type, resolved_receive_id = self._resolve_receive_target(receive_id_type, receive_id)
        if not image_path.strip():
            raise MessageValidationError("image_path must not be empty.")
        upload_result = await self.upload_image(image_path=image_path)
        resolved_image_key = str(upload_result.get("image_key") or "").strip()
        token = await self._token_manager.get_token()
        response = await self._message_client.send_message(
            token,
            receive_id_type=resolved_receive_id_type,
            receive_id=resolved_receive_id,
            msg_type="image",
            content=json.dumps({"image_key": resolved_image_key}, ensure_ascii=False),
            uuid=uuid,
        )
        result = self._normalize_result(response, resolved_receive_id)
        return result

    async def upload_file(
        self,
        *,
        file_path: str,
        file_type: FeishuFileType = "stream",
        file_name: str | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        if not file_path.strip():
            raise MessageValidationError("file_path must not be empty.")
        path = Path(file_path).expanduser()
        resolved_file_name = (file_name or path.name).strip()
        resolved_file_type = file_type.strip().lower()
        if not resolved_file_name:
            raise MessageValidationError("file_name could not be determined from file_path.")
        if resolved_file_type not in VALID_FEISHU_FILE_TYPES:
            raise MessageValidationError(
                "file_type must be one of opus, mp4, pdf, doc, xls, ppt, stream. "
                "Use stream for other file types."
            )
        if duration_ms is not None and duration_ms < 0:
            raise MessageValidationError("duration_ms must be greater than or equal to 0.")
        token = await self._token_manager.get_token()
        response = await self._message_client.upload_file(
            token,
            file_path=file_path,
            file_type=resolved_file_type,
            file_name=resolved_file_name,
            duration_ms=duration_ms,
        )
        data = response.get("data") or {}
        file_key = str(data.get("file_key") or "").strip()
        if not file_key:
            raise MessageValidationError("File upload did not return a valid file_key.")
        return {
            "ok": True,
            "file_key": file_key,
            "file_path": file_path,
            "file_name": resolved_file_name,
            "file_type": resolved_file_type,
        }

    async def send_file(
        self,
        *,
        receive_id_type: ReceiveIdType,
        receive_id: str,
        file_path: str,
        file_type: FeishuFileType = "stream",
        file_name: str | None = None,
        duration_ms: int | None = None,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        resolved_receive_id_type, resolved_receive_id = self._resolve_receive_target(receive_id_type, receive_id)
        if not file_path.strip():
            raise MessageValidationError("file_path must not be empty.")
        upload_result = await self.upload_file(
            file_path=file_path,
            file_type=file_type,
            file_name=file_name,
            duration_ms=duration_ms,
        )
        resolved_file_key = str(upload_result.get("file_key") or "").strip()
        token = await self._token_manager.get_token()
        response = await self._message_client.send_message(
            token,
            receive_id_type=resolved_receive_id_type,
            receive_id=resolved_receive_id,
            msg_type="file",
            content=json.dumps({"file_key": resolved_file_key}, ensure_ascii=False),
            uuid=uuid,
        )
        result = self._normalize_result(response, resolved_receive_id)
        return result

    async def send_post(
        self,
        *,
        receive_id_type: ReceiveIdType,
        receive_id: str,
        title: str,
        content: FeishuPostContent,
        locale: str = "zh_cn",
        uuid: str | None = None,
    ) -> dict[str, Any]:
        resolved_receive_id_type, resolved_receive_id = self._resolve_receive_target(receive_id_type, receive_id)
        if not title.strip():
            raise MessageValidationError("title must not be empty.")
        validated_content = self._validate_post_content(content)
        if not locale.strip():
            raise MessageValidationError("locale must not be empty.")
        token = await self._token_manager.get_token()
        response = await self._message_client.send_message(
            token,
            receive_id_type=resolved_receive_id_type,
            receive_id=resolved_receive_id,
            msg_type="post",
            content=json.dumps({locale: {"title": title, "content": validated_content}}, ensure_ascii=False),
            uuid=uuid,
        )
        result = self._normalize_result(response, resolved_receive_id)
        return result

    async def send_interactive(
        self,
        *,
        receive_id_type: ReceiveIdType,
        receive_id: str,
        card: dict[str, Any],
        uuid: str | None = None,
    ) -> dict[str, Any]:
        resolved_receive_id_type, resolved_receive_id = self._resolve_receive_target(receive_id_type, receive_id)
        if not isinstance(card, dict) or not card:
            raise MessageValidationError("card must be a non-empty JSON object.")
        token = await self._token_manager.get_token()
        response = await self._message_client.send_message(
            token,
            receive_id_type=resolved_receive_id_type,
            receive_id=resolved_receive_id,
            msg_type="interactive",
            content=json.dumps(card, ensure_ascii=False),
            uuid=uuid,
        )
        return self._normalize_result(response, resolved_receive_id)

    async def update_interactive(self, *, message_id: str, card: dict[str, Any]) -> dict[str, Any]:
        if not message_id.strip():
            raise MessageValidationError("message_id must not be empty.")
        if not isinstance(card, dict) or not card:
            raise MessageValidationError("card must be a non-empty JSON object.")
        token = await self._token_manager.get_token()
        response = await self._message_client.update_message_card(token, message_id=message_id, card=card)
        return {
            "ok": True,
            "message_id": message_id,
            "updated": True,
            "data": response.get("data"),
        }

    async def create_reaction(
        self,
        *,
        message_id: str,
        emoji_type: str | None = None,
    ) -> dict[str, Any]:
        if not message_id.strip():
            raise MessageValidationError("message_id must not be empty.")
        resolved_emoji_type = (emoji_type or self._settings.reaction_emoji_type).strip()
        if not resolved_emoji_type:
            raise MessageValidationError("emoji_type must not be empty.")
        token = await self._token_manager.get_token()
        created = await self._message_client.create_message_reaction(
            token,
            message_id=message_id,
            emoji_type=resolved_emoji_type,
        )
        reaction = created.get("data") or {}
        reaction_id = str(reaction.get("reaction_id") or "").strip()
        if not reaction_id:
            raise MessageValidationError("Message reaction create did not return a reaction_id.")
        return {
            "ok": True,
            "message_id": message_id,
            "reaction_id": reaction_id,
            "emoji_type": resolved_emoji_type,
        }

    async def delete_reaction(self, *, message_id: str, reaction_id: str) -> dict[str, Any]:
        if not message_id.strip():
            raise MessageValidationError("message_id must not be empty.")
        if not reaction_id.strip():
            raise MessageValidationError("reaction_id must not be empty.")
        token = await self._token_manager.get_token()
        deleted = await self._message_client.delete_message_reaction(
            token,
            message_id=message_id,
            reaction_id=reaction_id,
        )
        return {
            "ok": True,
            "message_id": message_id,
            "reaction_id": reaction_id,
            "deleted": deleted.get("code") == 0,
        }

    async def download_reply_resources(
        self,
        *,
        question_id: str,
        resource_refs: list[dict[str, Any]],
        target_root: Path | None,
    ) -> list[str]:
        resolved_question_id = question_id.strip()
        if not resolved_question_id:
            raise MessageValidationError("question_id must not be empty.")
        if not resource_refs:
            return []
        if target_root is None:
            raise MessageValidationError("target_root must not be empty.")
        target_dir = (target_root.expanduser().resolve() / self._download_bucket_name()).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        token = await self._token_manager.get_token()
        saved_paths: list[str] = []
        seen: set[tuple[str, str]] = set()
        for resource_ref in resource_refs:
            kind = str(resource_ref.get("kind") or "").strip().lower()
            message_id = str(resource_ref.get("message_id") or "").strip()
            if not message_id:
                raise MessageValidationError("resource ref message_id must not be empty.")
            if kind == "image":
                image_key = str(resource_ref.get("image_key") or "").strip()
                if not image_key or (kind, image_key) in seen:
                    continue
                seen.add((kind, image_key))
                download = await self._message_client.download_message_resource(
                    token,
                    message_id=message_id,
                    file_key=image_key,
                    resource_type="image",
                )
                target_path = self._build_download_target_path(
                    target_dir=target_dir,
                    fallback_name=f"image_{image_key[:12]}",
                    suggested_name="",
                    content_type=str(download.get("content_type") or ""),
                )
                target_path.write_bytes(download.get("content") or b"")
                saved_paths.append(str(target_path))
                continue
            if kind == "file":
                file_key = str(resource_ref.get("file_key") or "").strip()
                if not file_key or (kind, file_key) in seen:
                    continue
                seen.add((kind, file_key))
                download = await self._message_client.download_message_resource(
                    token,
                    message_id=message_id,
                    file_key=file_key,
                    resource_type="file",
                )
                suggested_name = str(resource_ref.get("file_name") or "").strip() or self._extract_download_filename(
                    str(download.get("content_disposition") or "")
                )
                target_path = self._build_download_target_path(
                    target_dir=target_dir,
                    fallback_name=f"file_{file_key[:12]}",
                    suggested_name=suggested_name,
                    content_type=str(download.get("content_type") or ""),
                )
                target_path.write_bytes(download.get("content") or b"")
                saved_paths.append(str(target_path))
        return saved_paths

    def _resolve_receive_target(self, receive_id_type: str, receive_id: str) -> tuple[str, str]:
        resolved_receive_id_type = (receive_id_type or "open_id").strip() or "open_id"
        resolved_receive_id = (receive_id or "").strip()
        if not resolved_receive_id:
            resolved_receive_id = (
                self._settings.owner_open_id.strip()
            )
            resolved_receive_id_type = "open_id"
        if not resolved_receive_id:
            raise MessageValidationError(
                "receive_id is required when owner_open_id is not configured."
            )
        return resolved_receive_id_type, resolved_receive_id

    def _validate_post_content(self, content: FeishuPostContent) -> FeishuPostContent:
        if not isinstance(content, list) or not content:
            raise MessageValidationError("content must be a non-empty two-dimensional array.")
        for paragraph_index, paragraph in enumerate(content, start=1):
            if not isinstance(paragraph, list) or not paragraph:
                raise MessageValidationError(
                    f"content paragraph {paragraph_index} must be a non-empty array of post elements."
                )
            for element_index, element in enumerate(paragraph, start=1):
                if not isinstance(element, dict):
                    raise MessageValidationError(
                        f"content element {paragraph_index}.{element_index} must be a JSON object."
                    )
                tag = str(element.get("tag") or "").strip()
                if tag not in VALID_FEISHU_POST_TAGS:
                    raise MessageValidationError("post element tag must be one of text, a, at, img.")
                if tag == "text":
                    self._require_post_string_field(element, "text", paragraph_index, element_index)
                elif tag == "a":
                    self._require_post_string_field(element, "text", paragraph_index, element_index)
                    self._require_post_string_field(element, "href", paragraph_index, element_index)
                elif tag == "at":
                    self._require_post_string_field(element, "user_id", paragraph_index, element_index)
                elif tag == "img":
                    self._require_post_string_field(element, "image_key", paragraph_index, element_index)
        return content

    def _require_post_string_field(
        self,
        element: dict[str, Any],
        field_name: str,
        paragraph_index: int,
        element_index: int,
    ) -> None:
        value = str(element.get(field_name) or "").strip()
        if not value:
            raise MessageValidationError(
                f"post element {paragraph_index}.{element_index} field '{field_name}' must be a non-empty string."
            )

    def _build_download_target_path(
        self,
        *,
        target_dir: Path,
        fallback_name: str,
        suggested_name: str,
        content_type: str,
    ) -> Path:
        raw_name = Path(suggested_name).name.strip()
        if not raw_name or raw_name in {".", ".."}:
            suffix = self._guess_extension(content_type)
            raw_name = f"{fallback_name}{suffix}"
        candidate = target_dir / raw_name
        if not candidate.exists():
            return candidate
        return target_dir / f"{candidate.stem}_{fallback_name}{candidate.suffix}"

    def _download_bucket_name(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _guess_extension(self, content_type: str) -> str:
        mime_type = content_type.split(";", 1)[0].strip().lower()
        if not mime_type:
            return ".bin"
        guessed = mimetypes.guess_extension(mime_type)
        return guessed or ".bin"

    def _extract_download_filename(self, content_disposition: str) -> str:
        normalized = content_disposition.strip()
        if not normalized:
            return ""
        filename_star = "filename*="
        if filename_star in normalized:
            encoded = normalized.split(filename_star, 1)[1].split(";", 1)[0].strip().strip('"')
            if "''" in encoded:
                encoded = encoded.split("''", 1)[1]
            return Path(unquote(encoded)).name.strip()
        filename_plain = "filename="
        if filename_plain in normalized:
            value = normalized.split(filename_plain, 1)[1].split(";", 1)[0].strip().strip('"')
            return Path(value).name.strip()
        return ""

    @staticmethod
    def _normalize_result(
        response: dict[str, Any],
        receive_id: str,
    ) -> dict[str, Any]:
        data = response.get("data") or {}
        return {
            "ok": True,
            "message_id": str(data.get("message_id") or ""),
            "receive_id": receive_id,
            "chat_id": str(data.get("chat_id") or ""),
            "create_time_ms": MessageService._coerce_timestamp_ms(data.get("create_time")),
        }

    @staticmethod
    def _coerce_timestamp_ms(value: Any) -> int:
        normalized = str(value or "").strip()
        if not normalized:
            return 0
        timestamp = int(normalized)
        if timestamp <= 0:
            return 0
        if timestamp < 10_000_000_000:
            return timestamp * 1000
        if timestamp < 10_000_000_000_000:
            return timestamp
        if timestamp < 10_000_000_000_000_000:
            return timestamp // 1000
        return timestamp // 1_000_000
