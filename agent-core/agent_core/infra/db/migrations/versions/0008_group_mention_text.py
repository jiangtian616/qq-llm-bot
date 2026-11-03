"""全量模式的群消息：已存的入站消息里 <@openid> 换成「@昵称」，开头 @ 机器人自己的去掉；@ 了机器人的补上 is_mention。

昵称从收件箱里同一条消息的原始事件（mentions）取；收件箱已经清理掉的就换成「@群成员」。

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-04 02:00:00.000000
"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MENTION = re.compile(r"<@!?([0-9A-Za-z_-]+)>( ?)")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT m.id, m.content, m.platform_msg_id, c.account_id FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.direction = 'in' AND m.content LIKE '%<@%'"
        )
    ).all()
    for message_id, content, platform_msg_id, account_id in rows:
        raw = conn.execute(
            sa.text(
                "SELECT raw_json FROM inbox_events WHERE account_id = :a AND json_extract(raw_json, '$.id') = :m "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"a": account_id, "m": platform_msg_id},
        ).scalar()
        try:
            mentions = (json.loads(raw).get("mentions") or []) if raw else []
        except (ValueError, AttributeError):
            mentions = []
        people = {m.get("member_openid") or m.get("id"): m for m in mentions if isinstance(m, dict)}
        to_bot = False

        def mention_text(match: re.Match[str]) -> str:
            nonlocal to_bot
            openid, space = match.groups()
            if openid == "all":
                return "@全体成员" + space
            m = people.get(openid) or {}
            if m.get("is_you"):
                to_bot = True
                if not content[: match.start()].strip():
                    return ""
            return f"@{m.get('username') or '群成员'}{space}"

        new = _MENTION.sub(mention_text, content).strip()
        conn.execute(
            sa.text("UPDATE messages SET content = :c, is_mention = is_mention OR :b WHERE id = :id"),
            {"c": new, "b": int(to_bot), "id": message_id},
        )


def downgrade() -> None:
    # 只改了文字，降级后不需要恢复。
    pass
