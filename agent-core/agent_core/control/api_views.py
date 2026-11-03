"""控制接口：会话、消息队列、日志三个页面用的路由，以及问答的处理过程。"""

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
from agent_core.core.ports.channel import RecallError
from agent_core.core.trace import Stopwatch
from agent_core.infra.db.repos import speaker_label

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
        return [_inbox_view(e) for e in rows]

    @r.get("/queue/inbox/{inbox_id}")
    async def inbox_detail(inbox_id: int, request: Request) -> dict[str, Any]:
        """列表里只有截断的预览；点开详情时再取完整内容和平台推来的原始事件。"""
        e = await _ctl(request).db.inbox.get(inbox_id)
        if e is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "事件不存在")
        return {
            **_inbox_view(e),
            "content": _content_of(e.raw_json),
            "raw": _json_or_text(e.raw_json),
            "turn_id": await _ctl(request).db.traces.turn_for_inbox(inbox_id),
        }

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
        windows: dict[tuple[int, str], int | None] = {}
        return [_outbox_view(o, c, await _recall_until(s, o, c, windows)) for o, c in rows]

    @r.get("/queue/outbox/{outbox_id}")
    async def outbox_detail(outbox_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        row = await s.db.outbox.get(outbox_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "回复不存在")
        o, c = row
        payload = _json_or_text(o.payload_json)
        return {
            **_outbox_view(o, c, await _recall_until(s, o, c, {})),
            "content": _outbox_text(payload) if isinstance(payload, dict) else "",
            "payload": payload,
            "turn_id": o.turn_id,
            "reply_to_event_id": o.reply_to_event_id,
            "msg_type": o.msg_type,
            "platform_message_id": o.platform_message_id,
            "platform_ts": o.platform_ts,
            "ref_idx": o.ref_idx,
            "attempts_log": [_attempt_view(a) for a in await s.db.traces.attempts(outbox_id)],
        }

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

    @r.post("/queue/outbox/{outbox_id}/recall")
    async def recall_outbox(outbox_id: int, request: Request) -> dict[str, Any]:
        """撤回一条已经发出的回复（目前只有群聊，发出 2 分钟内）。成败都记进发送记录。"""
        s = _ctl(request)
        row = await s.db.outbox.get(outbox_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "回复不存在")
        o, c = row
        if o.status != "sent" or not o.platform_message_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "只有已经发出的回复可以撤回")
        until = await _recall_until(s, o, c, {})
        if until is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "只能撤回群聊里的回复")
        if now_ms() > until:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "发出超过 2 分钟，平台不允许撤回")
        channel = await s.connections.channel(o.account_id)
        watch = Stopwatch()
        try:
            await channel.recall(Scene(c.scene), c.target_openid, o.platform_message_id)
        except Exception as e:
            reason = str(e) if isinstance(e, RecallError) else f"撤回失败：{type(e).__name__}: {e}"
            code = e.code if isinstance(e, RecallError) else None
            await s.db.traces.add_attempt(
                outbox_id, attempt=o.attempts, result="recall_failed", error_code=code, error=reason[:2000],
                started_at=watch.started_at, duration_ms=watch.ms,
            )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, reason) from e
        await s.db.outbox.mark(outbox_id, status="recalled", recalled_at=now_ms())
        await s.db.traces.add_attempt(
            outbox_id, attempt=o.attempts, result="recalled", started_at=watch.started_at, duration_ms=watch.ms
        )
        await s.db.events.add(
            "info", "outbox", f"已撤回发到「{_conversation_label(c)}」的回复 #{outbox_id}", account_id=o.account_id
        )
        await s.bus.publish(BusEvent("queue.changed"))
        return {"status": "recalled"}

    # ---- 问答过程 ----

    @r.get("/turns")
    async def list_turns(request: Request, limit: int = 100) -> list[dict[str, Any]]:
        s = _ctl(request)
        rows = await s.db.traces.turns(status=request.query_params.get("status"), limit=min(max(limit, 1), 500))
        return [_turn_view(t, c, m) for t, c, m in rows]

    @r.get("/turns/counts")
    async def turn_counts(request: Request) -> dict[str, int]:
        return await _ctl(request).db.traces.turn_counts()

    @r.get("/turns/{turn_id}")
    async def turn_detail(turn_id: int, request: Request) -> dict[str, Any]:
        """一次问答的完整过程：提问、每一步（组装上下文、请求模型、搜索、工具、回复编排、压缩）、发出去的几段。"""
        s = _ctl(request)
        row = await s.db.traces.turn(turn_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "问答不存在（可能已经过期清理）")
        t, c, m, contact = row
        return {
            **_turn_view(t, c, m),
            "question": m.content,
            "sender": speaker_label(c.scene, contact.openid, contact.display_name) if contact else None,
            "steps": [_step_view(x) for x in await s.db.traces.steps(turn_id)],
            "outbox": [
                {
                    "id": o.id,
                    "status": o.status,
                    "chunk_index": o.chunk_index,
                    "chunk_count": o.chunk_count,
                    "attempts": o.attempts,
                    "preview": _preview(_outbox_text(json.loads(o.payload_json))),
                }
                for o in await s.db.traces.outbox_for_turn(turn_id)
            ],
        }

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


def _inbox_view(e: Any) -> dict[str, Any]:
    return {
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


async def _recall_until(s: ControlState, o: Any, c: Any, windows: dict[tuple[int, str], int | None]) -> int | None:
    """已发送的回复最晚什么时候还能撤回（毫秒时间戳）；不能撤回（还没发、单聊、渠道不支持）时为 None。

    windows 缓存每个账号、场景的撤回时限，列表里不用每行都去取渠道。
    """
    if o.status != "sent" or not o.sent_at or not o.platform_message_id:
        return None
    key = (o.account_id, c.scene)
    if key not in windows:
        try:
            windows[key] = (await s.connections.channel(o.account_id)).recall_window(Scene(c.scene))
        except Exception:
            windows[key] = None
    window = windows[key]
    return None if window is None else o.sent_at + window * 1000


def _outbox_view(o: Any, c: Any, recall_until: int | None = None) -> dict[str, Any]:
    return {
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
        "recalled_at": o.recalled_at,
        "recall_until": recall_until,
        "created_at": o.created_at,
        "preview": _preview(_outbox_text(json.loads(o.payload_json))),
    }


def _turn_view(t: Any, c: Any, m: Any) -> dict[str, Any]:
    return {
        "id": t.id,
        "conversation_id": t.conversation_id,
        "conversation_label": _conversation_label(c),
        "inbox_id": t.inbox_id,
        "status": t.status,
        "provider": t.provider,
        "model": t.model,
        "stop_reason": t.stop_reason,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "cache_read_tokens": t.cache_read_tokens,
        "cache_write_tokens": t.cache_write_tokens,
        "cost_usd": t.cost_usd,
        "search_requests": t.search_requests,
        "tool_rounds": t.tool_rounds,
        "latency_ms": t.latency_ms,
        "last_error": t.last_error,
        "started_at": t.started_at,
        "finished_at": t.finished_at,
        "preview": _preview(m.content),
    }


def _step_view(x: Any) -> dict[str, Any]:
    return {
        "seq": x.seq,
        "kind": x.kind,
        "title": x.title,
        "detail": x.detail,
        "data": _json_or_text(x.data_json),
        "error": x.error,
        "started_at": x.started_at,
        "duration_ms": x.duration_ms,
    }


def _attempt_view(a: Any) -> dict[str, Any]:
    return {
        "attempt": a.attempt,
        "result": a.result,
        "error_code": a.error_code,
        "error": a.error,
        "note": a.note,
        "started_at": a.started_at,
        "duration_ms": a.duration_ms,
    }


def _outbox_text(payload: dict[str, Any]) -> str:
    """图片、卡片消息没有文字，显示成「[图片] 链接」「[卡片] 标题」。"""
    card = payload.get("card")
    if isinstance(card, dict):
        return f"[卡片] {card.get('title') or ''}"
    image_url = payload.get("image_url")
    return f"[图片] {image_url}" if image_url else str(payload.get("content") or "")


def _json_or_text(raw: str | None) -> Any:
    """原样存的 JSON 解析出来给桌面端排版；解析不了就当文本。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _content_of(raw_json: str | None) -> str:
    if not raw_json:
        return ""
    try:
        return str(json.loads(raw_json).get("content") or "")
    except (ValueError, AttributeError):
        return ""


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
