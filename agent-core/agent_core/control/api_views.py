"""控制接口：会话、消息队列、日志三个页面用的路由。"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from agent_core.control.auth import require_token
from agent_core.control.state import ControlState
from agent_core.core.bus import BusEvent
from agent_core.core.clock import now_ms
from agent_core.core.models import Scene

BOT_STATE_NO_PERMISSION = 11253
"""QQ：群机器人状态接口只对白名单机器人开放。"""
BOT_STATE_MIN_INTERVAL_MS = 10 * 60_000


def _ctl(request: Request) -> ControlState:
    return request.app.state.control


def _preview(text: str | None, n: int = 80) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _conversation_label(conv: Any) -> str:
    if conv.display_name:
        return conv.display_name
    kind = "群" if conv.scene == Scene.GROUP else "单聊"
    return f"{kind} {conv.target_openid[-6:]}"


class ConversationUpdate(BaseModel):
    display_name: str | None = None


def build_views_router() -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])

    async def conversation_view(s: ControlState, conv: Any, message_count: int, unmentioned: int) -> dict[str, Any]:
        api_state = await s.db.settings.resolve("qq.bot_state_api", account_id=conv.account_id)
        return {
            "id": conv.id,
            "account_id": conv.account_id,
            "scene": conv.scene,
            "target_openid": conv.target_openid,
            "display_name": conv.display_name,
            "label": _conversation_label(conv),
            "history_epoch": conv.history_epoch,
            "status": conv.status,
            "last_message_at": conv.last_message_at,
            "created_at": conv.created_at,
            "message_count": message_count,
            # 只要收到过未 @ 的群消息，就说明这个群开了「接收全部消息」，界面上按推断显示。
            "received_unmentioned": unmentioned > 0,
            "bot_state": {
                "recv_msg_setting": conv.bot_recv_msg_setting,
                "member_role": conv.bot_member_role,
                "allow_proactive_msg": None
                if conv.bot_allow_proactive_msg is None
                else bool(conv.bot_allow_proactive_msg),
                "joined_at": conv.bot_joined_at,
                "checked_at": conv.bot_state_checked_at,
                "error": conv.bot_state_error,
                "api_unavailable": api_state == "unavailable",
            },
        }

    async def get_conversation(s: ControlState, conversation_id: int) -> Any:
        conv = await s.db.conversations.get(conversation_id)
        if conv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
        return conv

    async def single_view(s: ControlState, conversation_id: int) -> dict[str, Any]:
        conv = await get_conversation(s, conversation_id)
        for conv, n, u in await s.db.conversations.summaries(account_id=conv.account_id, limit=100_000):
            if conv.id == conversation_id:
                return await conversation_view(s, conv, n, u)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")

    # ---- 会话 ----

    @r.get("/conversations")
    async def list_conversations(request: Request, account_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        s = _ctl(request)
        rows = await s.db.conversations.summaries(account_id=account_id, limit=min(max(limit, 1), 1000))
        return [await conversation_view(s, c, n, u) for c, n, u in rows]

    @r.get("/conversations/{conversation_id}/messages")
    async def conversation_messages(
        conversation_id: int, request: Request, limit: int = 100, before_id: int | None = None
    ) -> list[dict[str, Any]]:
        s = _ctl(request)
        await get_conversation(s, conversation_id)
        return await s.db.conversations.messages(conversation_id, limit=min(max(limit, 1), 500), before_id=before_id)

    @r.patch("/conversations/{conversation_id}")
    async def update_conversation(conversation_id: int, body: ConversationUpdate, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        await get_conversation(s, conversation_id)
        if body.display_name is not None:
            await s.db.conversations.update(conversation_id, display_name=body.display_name.strip() or None)
        return await single_view(s, conversation_id)

    @r.post("/conversations/{conversation_id}/clear")
    async def clear_conversation(conversation_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        await get_conversation(s, conversation_id)
        await s.db.conversations.clear_history(conversation_id)
        return await single_view(s, conversation_id)

    @r.post("/conversations/{conversation_id}/bot-state/refresh")
    async def refresh_bot_state(conversation_id: int, request: Request, force: bool = True) -> dict[str, Any]:
        """查询机器人在群里的消息接收设置。只读：这个设置只能在 QQ 客户端的群设置里修改。"""
        s = _ctl(request)
        conv = await get_conversation(s, conversation_id)
        if conv.scene != Scene.GROUP:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "只有群聊有消息接收设置")
        checked = conv.bot_state_checked_at or 0
        if not force and now_ms() - checked < BOT_STATE_MIN_INTERVAL_MS:
            # 接口每分钟限 30 次：自动查询时同一个群 10 分钟内不重复查。
            return await single_view(s, conversation_id)
        channel = await s.connections.channel(conv.account_id)
        try:
            state = await channel.get_conversation_state(Scene.GROUP, conv.target_openid)
        except Exception as e:
            code = getattr(e, "code", None)
            if code == BOT_STATE_NO_PERMISSION:
                error = f"{code}：这个接口只对白名单机器人开放，需要向平台申请权限"
                # 之后不再自动查询，只在手动刷新时重试。
                await s.db.settings.put("account", conv.account_id, "qq.bot_state_api", "unavailable")
            else:
                error = f"{type(e).__name__}: {e}"[:500]
            await s.db.conversations.update(conversation_id, bot_state_error=error, bot_state_checked_at=now_ms())
            return await single_view(s, conversation_id)
        if state is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "这个渠道不支持查询群状态")
        proactive = state.get("allow_proactive_msg")
        await s.db.conversations.update(
            conversation_id,
            bot_recv_msg_setting=_str_or_none(state.get("recv_msg_setting")),
            bot_member_role=_str_or_none(state.get("member_role", state.get("role"))),
            bot_allow_proactive_msg=None if proactive is None else int(bool(proactive)),
            bot_joined_at=_str_or_none(state.get("joined_at")),
            bot_state_checked_at=now_ms(),
            bot_state_error=None,
        )
        await s.db.settings.remove("account", conv.account_id, "qq.bot_state_api")
        return await single_view(s, conversation_id)

    # ---- 消息队列 ----

    @r.get("/queue/inbox")
    async def queue_inbox(request: Request, limit: int = 100) -> list[dict[str, Any]]:
        s = _ctl(request)
        rows = await s.db.inbox.list(status=request.query_params.get("status"), limit=min(max(limit, 1), 500))
        return [
            {
                "id": e.id,
                "account_id": e.account_id,
                "event_id": e.event_id,
                "event_type": e.event_type,
                "status": e.status,
                "received_at": e.received_at,
                "reply_deadline": e.reply_deadline,
                "processed_at": e.processed_at,
                "attempts": e.attempts,
                "last_error": e.last_error,
                "conversation_id": e.conversation_id,
                "preview": _preview(_content_of(e.raw_json)),
            }
            for e in rows
        ]

    @r.post("/queue/inbox/{inbox_id}/retry")
    async def retry_inbox(inbox_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        reason = await s.db.inbox.retry(inbox_id)
        if reason:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
        if s.wake_inbox:
            s.wake_inbox()
        await s.bus.publish(BusEvent("queue.changed"))
        return {"status": "pending"}

    @r.get("/queue/outbox")
    async def queue_outbox(request: Request, limit: int = 100) -> list[dict[str, Any]]:
        s = _ctl(request)
        rows = await s.db.outbox.list(status=request.query_params.get("status"), limit=min(max(limit, 1), 500))
        return [
            {
                "id": o.id,
                "account_id": o.account_id,
                "conversation_id": o.conversation_id,
                "conversation_label": _conversation_label(c),
                "reply_to_msg_id": o.reply_to_msg_id,
                "msg_seq": o.msg_seq,
                "chunk_index": o.chunk_index,
                "chunk_count": o.chunk_count,
                "status": o.status,
                "deadline": o.deadline,
                "attempts": o.attempts,
                "next_attempt_at": o.next_attempt_at,
                "last_error_code": o.last_error_code,
                "last_error": o.last_error,
                "sent_at": o.sent_at,
                "created_at": o.created_at,
                "preview": _preview(json.loads(o.payload_json).get("content")),
            }
            for o, c in rows
        ]

    @r.post("/queue/outbox/{outbox_id}/retry")
    async def retry_outbox(outbox_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        reason = await s.db.outbox.retry(outbox_id)
        if reason:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
        if s.wake_outbox:
            s.wake_outbox()
        await s.bus.publish(BusEvent("queue.changed"))
        return {"status": "pending"}

    @r.post("/queue/outbox/{outbox_id}/cancel")
    async def cancel_outbox(outbox_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        reason = await s.db.outbox.cancel(outbox_id)
        if reason:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
        await s.bus.publish(BusEvent("queue.changed"))
        return {"status": "cancelled"}

    # ---- 日志 ----

    @r.get("/logs")
    async def tail_log(request: Request, lines: int = 300) -> dict[str, Any]:
        """核心服务日志文件的最后若干行。写日志时已经把已知密钥替换成掩码。"""
        s = _ctl(request)
        path = s.data_dir / "logs" / "agent-core.log"
        n = min(max(lines, 1), 2000)
        if not path.exists():
            return {"file": str(path), "lines": []}
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 512 * 1024))
            data = f.read().decode("utf-8", errors="replace")
        tail = deque(data.splitlines(), maxlen=n)
        if size > 512 * 1024 and tail:
            tail.popleft()  # 第一行可能被截断
        return {"file": str(path), "lines": list(tail)}

    return r


def _content_of(raw_json: str | None) -> str:
    if not raw_json:
        return ""
    try:
        return str(json.loads(raw_json).get("content") or "")
    except (ValueError, AttributeError):
        return ""


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
