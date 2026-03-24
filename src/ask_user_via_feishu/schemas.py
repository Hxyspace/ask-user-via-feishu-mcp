from __future__ import annotations

from typing import Literal, TypeAlias, get_args

from typing_extensions import TypedDict

ReceiveIdType = Literal["open_id", "union_id", "user_id", "chat_id", "email"]
FeishuFileType = Literal["opus", "mp4", "pdf", "doc", "xls", "ppt", "stream"]
FeishuPostTag = Literal["text", "a", "at", "img"]


class FeishuPostText(TypedDict):
    tag: Literal["text"]
    text: str


class FeishuPostLink(TypedDict):
    tag: Literal["a"]
    text: str
    href: str


class FeishuPostAt(TypedDict):
    tag: Literal["at"]
    user_id: str


class FeishuPostImage(TypedDict):
    tag: Literal["img"]
    image_key: str


FeishuPostElement: TypeAlias = FeishuPostText | FeishuPostLink | FeishuPostAt | FeishuPostImage
FeishuPostParagraph: TypeAlias = list[FeishuPostElement]
FeishuPostContent: TypeAlias = list[FeishuPostParagraph]

VALID_FEISHU_FILE_TYPES: tuple[str, ...] = get_args(FeishuFileType)
VALID_FEISHU_POST_TAGS: tuple[str, ...] = get_args(FeishuPostTag)
