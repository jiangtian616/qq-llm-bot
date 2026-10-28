"""收件箱：接收只负责写库，处理由后台任务从数据库取活。

一条消息的写入顺序（每一步一个事务，调用模型和发送都在事务外）：
1. 收到事件：插入 inbox_events（event_id 重复就丢弃），同时更新 gateway_sessions.last_seq。
2. 开始处理：更新或新建 conversations、contacts；插入 messages（入站）；需要回复时插入 turns；收件箱标为已处理。
3. 调用模型：turns 标为运行中。
4. 模型返回：插入 provider_messages、messages（出站全文）、outbox（每个分段一行）；turns 标为成功并写入用量。
5. 发送：每发出一段，把 outbox 这一行标为已发送。
"""

from __future__ import annotations

from agent_core.core.models import RawEvent, ReplyWindow

LEASE_MS = 60_000
"""取活时给这一行加的租约；进程崩溃后租约到期，任务自动回到待处理。"""


def reply_deadline_ms(received_at_ms: int, window: ReplyWindow) -> int:
    """被动回复截止时间：群 +5 分钟，单聊 +60 分钟。"""
    return received_at_ms + window.seconds * 1000


class Inbox:
    """TODO(下一阶段)：基于 SQLite 的收件箱实现。"""

    async def record(self, event: RawEvent) -> bool:
        """写入收件箱并更新 seq；重复事件返回 False。"""
        raise NotImplementedError

    async def recover_stale(self) -> int:
        """启动时把租约已过期的「处理中」改回待处理，超出回复窗口的标为已过期。"""
        raise NotImplementedError
