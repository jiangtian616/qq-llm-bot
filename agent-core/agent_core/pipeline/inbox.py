"""收件箱：接收只负责写库，处理由后台任务从数据库取活。

一条消息的写入顺序（每一步一个事务，调用模型和发送都在事务外）：
1. 收到事件：插入 inbox_events（event_id 重复就丢弃），同时更新 gateway_sessions.last_seq。（ConnectionManager）
2. 开始处理：更新或新建 conversations、contacts；插入 messages（入站）；需要回复时插入 turns。（AgentEngine）
3. 调用模型：turns 标为运行中。
4. 模型返回：插入 provider_messages、messages（出站全文）、outbox（每个分段一行）；turns 标为成功并写入用量；
   收件箱标为已处理。
5. 发送：每发出一段，把 outbox 这一行标为已发送。（OutboxWorker）

收件箱这一行在第 4 步之前一直是「处理中」并带租约；进程崩溃后租约到期，会从第 2 步重新开始，
第 2 步是幂等的（消息按 platform_msg_id、问答按 inbound_message_id 去重）。
"""

from __future__ import annotations

from agent_core.core.models import ReplyWindow

LEASE_MS = 5 * 60_000
"""取活时给这一行加的租约；要比一次模型调用（含 SDK 重试）的最长耗时更长。"""


def reply_deadline_ms(received_at_ms: int, window: ReplyWindow) -> int:
    """被动回复截止时间：群 +5 分钟，单聊 +60 分钟。"""
    return received_at_ms + window.seconds * 1000
