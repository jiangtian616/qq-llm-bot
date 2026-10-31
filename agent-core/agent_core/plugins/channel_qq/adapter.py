"""QQAdapter：渠道插槽的 QQ 实现。

图片（msg_type=7）先上传成 file_info 再发；图文卡片（msg_type=8）直接带 card 发，发不出去（比如没有权限）就退回成文字。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import aiohttp

from agent_core.core.models import (
    InboundMessage,
    OutboundMessage,
    RawEvent,
    ReplyWindow,
    Scene,
    SendErrorClass,
    SendResult,
)
from agent_core.core.ports.channel import ChannelAdapter, ChannelContext
from agent_core.infra.http import FetchError, client_session, fetch_public
from agent_core.plugins.channel_qq.constants import API_BASE, ErrorCode, FileType
from agent_core.plugins.channel_qq.errors import MediaUnavailableError, QQApiError
from agent_core.plugins.channel_qq.event_parser import EventParser
from agent_core.plugins.channel_qq.openapi_sender import OpenApiSender
from agent_core.plugins.channel_qq.token_manager import TokenManager

log = logging.getLogger(__name__)

_ALREADY_SENT = {ErrorCode.DUPLICATE_MSG_SEQ}
_FATAL = {ErrorCode.MESSAGE_TOO_LONG, ErrorCode.MUTED, ErrorCode.UNSUPPORTED_FILE_FORMAT, ErrorCode.FILE_TOO_LARGE}
_EXPIRED = {ErrorCode.REPLY_EXPIRED_OR_LIMIT}

# 被动回复：群聊 5 分钟内最多 5 次，单聊 60 分钟内最多 4 次。
_REPLY_WINDOWS = {
    Scene.GROUP: ReplyWindow(seconds=5 * 60, max_replies=5),
    Scene.C2C: ReplyWindow(seconds=60 * 60, max_replies=4),
}

TEXT_MSG_TYPE = 0
CARD_MSG_TYPE = 8
# 平台下载不到图片时由本机下载后直接上传数据；直传上限 5 MB，按 base64 之后算。
MAX_UPLOAD_BYTES = 3_750_000
_IMAGE_EXT = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF8", "gif"))


class QQAdapter(ChannelAdapter):
    name = "qq"

    def __init__(self, ctx: ChannelContext, *, api_base: str = API_BASE) -> None:
        super().__init__(ctx)
        self._api_base = api_base
        self._parser = EventParser()
        self._session: aiohttp.ClientSession | None = None
        self._sender: OpenApiSender | None = None

    def parse(self, event: RawEvent) -> InboundMessage | None:
        return self._parser.parse(event)

    def reply_window(self, scene: Scene) -> ReplyWindow:
        return _REPLY_WINDOWS[scene]

    async def send(self, message: OutboundMessage) -> SendResult:
        payload: dict[str, Any] = {"msg_type": message.msg_type, "msg_seq": message.msg_seq}
        if message.card:
            payload["card"] = {"type": "tuwen", "content": message.card}
        elif message.content or not message.image_url:
            payload["content"] = message.content
        if message.image_url:
            file_info = await self._upload_image(message.scene, message.target_openid, message.image_url)
            payload["media"] = {"file_info": file_info}
        if message.reply_to_msg_id:
            payload["msg_id"] = message.reply_to_msg_id
        elif message.reply_to_event_id:
            payload["event_id"] = message.reply_to_event_id
        payload.update(message.extra)
        try:
            body = await self._post_message(message, payload)
        except QQApiError as e:
            # 卡片发不出去（没有权限、字段不合规……）就退回成文字，用同一个 msg_seq；重复、过期、禁言这些照常处理。
            if not message.card or self.classify_send_error(e) != SendErrorClass.FATAL or e.code in _FATAL:
                raise
            log.warning("图文卡片发送失败，改发文字：%s", e)
            payload.pop("card")
            payload["msg_type"] = TEXT_MSG_TYPE
            payload["content"] = card_text(message.card)
            body = await self._post_message(message, payload)
        return SendResult(
            platform_message_id=body.get("id"),
            platform_ts=body.get("timestamp"),
            ref_idx=(body.get("ext_info") or {}).get("ref_idx"),
        )

    def classify_send_error(self, error: Exception) -> SendErrorClass:
        if isinstance(error, MediaUnavailableError):
            return SendErrorClass.FATAL
        if not isinstance(error, QQApiError):
            # 网络错误、超时等。
            return SendErrorClass.RETRYABLE
        if error.code in _ALREADY_SENT:
            return SendErrorClass.ALREADY_SENT
        if error.code in _FATAL:
            return SendErrorClass.FATAL
        if error.code in _EXPIRED:
            return SendErrorClass.EXPIRED
        status = error.http_status or 0
        if 400 <= status < 500 and status != 429:
            return SendErrorClass.FATAL
        return SendErrorClass.RETRYABLE

    async def get_conversation_state(self, scene: Scene, target_openid: str) -> dict[str, Any] | None:
        if scene != Scene.GROUP:
            return None
        return await self._get_sender().get_group_bot_state(target_openid)

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._sender = None

    async def _post_message(self, message: OutboundMessage, payload: dict[str, Any]) -> dict[str, Any]:
        sender = self._get_sender()
        if message.scene == Scene.GROUP:
            return await sender.send_group_message(message.target_openid, payload)
        return await sender.send_c2c_message(message.target_openid, payload)

    async def _upload_image(self, scene: Scene, openid: str, url: str) -> str:
        """先让平台按链接下载；平台下载不到（防盗链、访问不了的站点等）再本机下载后上传数据。

        上传得到的 file_info 有有效期，所以每次发送（包括重试）都重新上传。
        """
        sender = self._get_sender()
        upload = sender.upload_group_file if scene == Scene.GROUP else sender.upload_c2c_file
        try:
            body = await upload(openid, {"file_type": FileType.IMAGE, "url": url, "srv_send_msg": False})
        except QQApiError as e:
            if e.code != ErrorCode.MEDIA_DOWNLOAD_FAILED:
                raise
            data, ext = await self._download_image(url)
            body = await upload(
                openid,
                {
                    "file_type": FileType.IMAGE,
                    "file_name": f"image.{ext}",
                    "file_data": base64.b64encode(data).decode("ascii"),
                    "srv_send_msg": False,
                },
            )
        return body["file_info"]

    async def _download_image(self, url: str) -> tuple[bytes, str]:
        assert self._session is not None
        try:
            fetched = await fetch_public(self._session, url, max_bytes=MAX_UPLOAD_BYTES)
        except FetchError as e:
            raise MediaUnavailableError(f"图片{e}") from e
        for magic, ext in _IMAGE_EXT:
            if fetched.data.startswith(magic):
                return fetched.data, ext
        raise MediaUnavailableError("不是 png / jpg / gif 图片")

    def _get_sender(self) -> OpenApiSender:
        if self._sender is None:
            self._session = client_session(self.ctx.proxy, timeout=aiohttp.ClientTimeout(total=30))
            tokens = TokenManager(self.ctx.app_id, self.ctx.secrets, self._session)
            self._sender = OpenApiSender(self._session, tokens, api_base=self._api_base)
        return self._sender


def card_text(card: dict[str, Any]) -> str:
    """卡片发不出去时改发的文字：标题和摘要，不带网址（QQ 不允许文字消息带链接）。"""
    title, description = card.get("title") or "", card.get("description") or ""
    return f"{title}：{description}" if description and description != title else title
